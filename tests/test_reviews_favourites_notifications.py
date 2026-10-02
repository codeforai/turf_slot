from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.conftest import at, future_day


def test_only_players_can_review_and_rating_is_aggregated(client, factory):
    owner, _ = factory.user("owner")
    player, player_headers = factory.user()
    other, other_headers = factory.user()
    _, admin_headers = factory.user("admin")
    turf = factory.turf(owner)

    review = {"rating": 4, "comment": "Good surface"}
    assert client.post(f"/turfs/{turf.id}/reviews", headers=player_headers, json=review).status_code == 403

    factory.booking(player, turf, datetime.now(UTC) - timedelta(days=1), paid=True)
    factory.booking(other, turf, datetime.now(UTC) - timedelta(days=2), paid=True)
    created = client.post(f"/turfs/{turf.id}/reviews", headers=player_headers, json=review)
    assert created.status_code == 201, created.text
    assert created.json()["author"]["first_name"] == player.first_name
    assert client.post(f"/turfs/{turf.id}/reviews", headers=player_headers, json=review).status_code == 409
    client.post(f"/turfs/{turf.id}/reviews", headers=other_headers, json={"rating": 2, "comment": "Too crowded"})

    detail = client.get(f"/turfs/{turf.id}").json()
    assert Decimal(detail["rating_avg"]) == Decimal("3.00")
    assert detail["review_count"] == 2

    review_id = created.json()["id"]
    assert client.patch(f"/reviews/{review_id}", headers=other_headers, json={"rating": 1}).status_code == 403
    assert client.patch(f"/reviews/{review_id}", headers=player_headers, json={"rating": 5}).status_code == 200
    assert Decimal(client.get(f"/turfs/{turf.id}").json()["rating_avg"]) == Decimal("3.50")

    assert client.delete(f"/reviews/{review_id}", headers=admin_headers).status_code == 204  # moderation
    detail = client.get(f"/turfs/{turf.id}").json()
    assert detail["review_count"] == 1
    listing = client.get(f"/turfs/{turf.id}/reviews").json()
    assert listing["total"] == 1


def test_favourites_are_idempotent(client, factory):
    owner, _ = factory.user("owner")
    _, headers = factory.user()
    turf = factory.turf(owner)
    assert client.put(f"/favourites/{turf.id}", headers=headers).status_code == 204
    assert client.put(f"/favourites/{turf.id}", headers=headers).status_code == 204
    assert [t["id"] for t in client.get("/favourites", headers=headers).json()] == [turf.id]
    assert client.delete(f"/favourites/{turf.id}", headers=headers).status_code == 204
    assert client.get("/favourites", headers=headers).json() == []
    assert client.put("/favourites/999999", headers=headers).status_code == 404


def test_owner_broadcast_reaches_only_their_customers(client, factory):
    owner, owner_headers = factory.user("owner")
    regular, regular_headers = factory.user()
    _, stranger_headers = factory.user()
    turf = factory.turf(owner)
    factory.booking(regular, turf, at(future_day(), 18), paid=True)

    sent = client.post(
        "/owner/notifications",
        headers=owner_headers,
        json={"title": "Monsoon offer", "message": "20% off weekday mornings", "type": "offer"},
    )
    assert sent.status_code == 201
    assert sent.json() == {"recipients": 1}

    inbox = client.get("/notifications", headers=regular_headers).json()["items"]
    assert inbox[0]["title"] == "Monsoon offer" and inbox[0]["type"] == "offer"
    assert client.get("/notifications", headers=stranger_headers).json()["total"] == 0


def test_notification_read_state(client, factory):
    owner, owner_headers = factory.user("owner")
    _, customer_headers = factory.user()
    turf = factory.turf(owner)
    day = future_day()
    for start in ("08:00", "10:00"):
        client.post(
            "/bookings",
            headers=customer_headers,
            json={"turf_id": turf.id, "date": day.isoformat(), "start_time": start, "duration_hours": 1},
        )
    assert client.get("/notifications/unread-count", headers=owner_headers).json() == {"unread": 2}
    first = client.get("/notifications", headers=owner_headers).json()["items"][0]
    assert client.post(f"/notifications/{first['id']}/read", headers=owner_headers).json()["is_read"] is True
    assert client.get("/notifications/unread-count", headers=owner_headers).json() == {"unread": 1}
    assert client.post(f"/notifications/{first['id']}/read", headers=customer_headers).status_code == 404
    assert client.post("/notifications/read-all", headers=owner_headers).status_code == 204
    assert client.get("/notifications", headers=owner_headers, params={"unread_only": True}).json()["total"] == 0
    assert client.delete(f"/notifications/{first['id']}", headers=owner_headers).status_code == 204


def test_admin_endpoints_require_admin(client, factory):
    _, owner_headers = factory.user("owner")
    _, admin_headers = factory.user("admin")
    assert client.get("/admin/dashboard", headers=owner_headers).status_code == 403
    dashboard = client.get("/admin/dashboard", headers=admin_headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["owners"] == 1

    amenity = client.post("/admin/amenities", headers=admin_headers, json={"name": "Parking"})
    assert amenity.status_code == 201
    assert client.post("/admin/amenities", headers=admin_headers, json={"name": "Parking"}).status_code == 409
    assert [a["name"] for a in client.get("/amenities").json()] == ["Parking"]

    users = client.get("/admin/users", headers=admin_headers, params={"role": "owner"}).json()
    assert users["total"] == 1
    admin_id = client.get("/users/me", headers=admin_headers).json()["id"]
    assert client.post(f"/admin/users/{admin_id}/block", headers=admin_headers).status_code == 403
    assert client.post("/admin/maintenance/expire-holds", headers=admin_headers).json() == {"released": 0}
