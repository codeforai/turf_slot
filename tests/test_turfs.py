from decimal import Decimal

from tests.conftest import at, future_day

TURF_BODY = {
    "name": "Green Field",
    "sport_type": "football",
    "description": "5-a-side",
    "location": "Edappally, Kochi",
    "address": "NH 66",
    "latitude": "10.025300",
    "longitude": "76.308300",
    "length_m": 40,
    "width_m": 20,
    "surface_type": "artificial",
    "capacity": 10,
    "price_per_hour": "1200.00",
    "opening_time": "06:00",
    "closing_time": "23:00",
}


def test_owner_creates_turf_admin_verifies_then_it_is_searchable(client, factory):
    _, user_headers = factory.user()
    _, owner_headers = factory.user("owner")
    _, admin_headers = factory.user("admin")
    parking = factory.amenity("Parking")

    assert client.post("/owner/turfs", headers=user_headers, json=TURF_BODY).status_code == 403

    created = client.post("/owner/turfs", headers=owner_headers, json={**TURF_BODY, "amenity_ids": [parking.id]})
    assert created.status_code == 201, created.text
    turf = created.json()
    assert turf["is_verified"] is False
    assert turf["slug"] == "green-field-edappally-kochi"
    assert [a["name"] for a in turf["amenities"]] == ["Parking"]

    assert client.get("/turfs").json()["total"] == 0  # hidden until verified
    assert client.get(f"/turfs/{turf['id']}").status_code == 404

    queue = client.get("/admin/turfs", headers=admin_headers, params={"verified": False}).json()
    assert [t["id"] for t in queue["items"]] == [turf["id"]]
    assert client.post(f"/admin/turfs/{turf['id']}/verify", headers=admin_headers).status_code == 200

    assert client.get("/turfs").json()["total"] == 1
    assert client.get(f"/turfs/slug/{turf['slug']}").json()["id"] == turf["id"]
    notes = client.get("/notifications", headers=owner_headers).json()["items"]
    assert any("now live" in n["title"] for n in notes)


def test_turf_validation(client, factory):
    _, owner_headers = factory.user("owner")
    bad_hours = client.post(
        "/owner/turfs", headers=owner_headers, json={**TURF_BODY, "min_booking_hours": 3, "max_booking_hours": 2}
    )
    assert bad_hours.status_code == 422
    half_coords = {**TURF_BODY}
    half_coords.pop("longitude")
    assert client.post("/owner/turfs", headers=owner_headers, json=half_coords).status_code == 422
    unknown_amenity = client.post("/owner/turfs", headers=owner_headers, json={**TURF_BODY, "amenity_ids": [999]})
    assert unknown_amenity.status_code == 422


def test_owner_can_only_manage_own_turfs(client, factory):
    owner, owner_headers = factory.user("owner")
    _, other_headers = factory.user("owner")
    turf = factory.turf(owner)
    assert (
        client.patch(f"/owner/turfs/{turf.id}", headers=other_headers, json={"price_per_hour": "1.00"}).status_code
        == 403
    )
    updated = client.patch(
        f"/owner/turfs/{turf.id}", headers=owner_headers, json={"price_per_hour": "1500.00", "status": "maintenance"}
    )
    assert updated.status_code == 200, updated.text
    assert Decimal(updated.json()["price_per_hour"]) == Decimal("1500.00")
    assert updated.json()["status"] == "maintenance"
    inconsistent = client.patch(f"/owner/turfs/{turf.id}", headers=owner_headers, json={"min_booking_hours": 6})
    assert inconsistent.status_code == 422


def test_search_filters_and_sorting(client, factory):
    owner, _ = factory.user("owner")
    lights = factory.amenity("Floodlights")
    parking = factory.amenity("Parking")
    near = factory.turf(
        owner,
        name="Near Pitch",
        price_per_hour=Decimal("800"),
        latitude=Decimal("10.0159"),
        longitude=Decimal("76.3419"),
        amenities=[lights, parking],
    )
    far = factory.turf(
        owner,
        name="Far Pitch",
        price_per_hour=Decimal("1500"),
        latitude=Decimal("10.1100"),
        longitude=Decimal("76.3500"),
        amenities=[lights],
    )
    factory.turf(owner, name="Cricket Nets", sport_type="cricket", price_per_hour=Decimal("600"))
    factory.turf(owner, name="Unverified", is_verified=False)

    def ids(**params):
        response = client.get("/turfs", params=params)
        assert response.status_code == 200, response.text
        return [t["id"] for t in response.json()["items"]]

    assert len(ids()) == 3
    assert ids(sport_type="football", sort="price_asc") == [near.id, far.id]
    assert ids(max_price="1000", sport_type="football") == [near.id]
    assert ids(q="far") == [far.id]
    assert ids(amenity=[lights.id, parking.id]) == [near.id]  # must have all requested amenities
    assert ids(sort="price_desc")[0] == far.id

    by_distance = client.get("/turfs", params={"lat": 10.0160, "lng": 76.3420, "radius_km": 25}).json()
    assert [t["id"] for t in by_distance["items"]] == [near.id, far.id]
    assert by_distance["items"][0]["distance_km"] < 0.1
    assert 10 < by_distance["items"][1]["distance_km"] < 12
    assert ids(lat=10.0160, lng=76.3420, radius_km=2) == [near.id]
    assert client.get("/turfs", params={"lat": 10.0}).status_code == 422
    assert client.get("/turfs", params={"sort": "distance"}).status_code == 422

    page = client.get("/turfs", params={"page_size": 2, "page": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 1


def test_availability(client, factory):
    owner, _ = factory.user("owner")
    customer, _ = factory.user()
    turf = factory.turf(owner)
    day = future_day()
    factory.booking(customer, turf, at(day, 18), hours=2)

    response = client.get(f"/turfs/{turf.id}/availability", params={"date": day.isoformat(), "duration_hours": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    starts = [s[11:16] for s in body["available_start_times"]]
    assert starts[0] == "06:00" and starts[-1] == "22:00"
    assert "17:30" not in starts and "18:00" not in starts and "19:30" not in starts
    assert "17:00" in starts and "20:00" in starts
    assert body["available_start_times"][0].endswith("+05:30")
    assert len(body["booked"]) == 1


def test_deactivate_turf_blocked_by_upcoming_bookings(client, factory):
    owner, owner_headers = factory.user("owner")
    customer, _ = factory.user()
    turf = factory.turf(owner)
    booking = factory.booking(customer, turf, at(future_day(), 18))
    assert client.delete(f"/owner/turfs/{turf.id}", headers=owner_headers).status_code == 409
    client.post(f"/bookings/{booking.id}/cancel", headers=owner_headers, json={"reason": "Closing down"})
    assert client.delete(f"/owner/turfs/{turf.id}", headers=owner_headers).status_code == 204
    assert client.get(f"/turfs/{turf.id}").status_code == 404
    assert len(client.get("/owner/turfs", headers=owner_headers).json()) == 1  # still visible to the owner
