import threading
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from app.db import SessionLocal
from app.models import Booking, Turf, User
from app.services import bookings as booking_service
from app.services.errors import Conflict
from tests.conftest import at, future_day


def book(client, headers, turf_id, day, start="18:00", hours=1, **extra):
    body = {"turf_id": turf_id, "date": day.isoformat(), "start_time": start, "duration_hours": hours, **extra}
    return client.post("/bookings", headers=headers, json=body)


def test_create_booking_holds_slot_until_paid(client, factory):
    owner, owner_headers = factory.user("owner")
    _, headers = factory.user()
    turf = factory.turf(owner, price_per_hour=Decimal("1200"))
    day = future_day()

    response = book(client, headers, turf.id, day, "18:00", 2, payment_method="upi", notes="Bring bibs")
    assert response.status_code == 201, response.text
    booking = response.json()
    assert booking["status"] == "pending"
    assert booking["payment_status"] == "unpaid"
    assert Decimal(booking["total_amount"]) == Decimal("2400.00")
    assert booking["start_at"] == f"{day.isoformat()}T18:00:00+05:30"
    assert booking["duration_hours"] == 2
    assert booking["hold_expires_at"] is not None
    assert booking["reference"].startswith("TS-")

    owner_notes = client.get("/notifications", headers=owner_headers).json()["items"]
    assert owner_notes[0]["title"].startswith("New booking request")


def test_booking_rules(client, factory):
    owner, _ = factory.user("owner")
    _, headers = factory.user()
    turf = factory.turf(owner)
    day = future_day()
    assert book(client, headers, turf.id, day, "18:15").status_code == 422  # not on a slot boundary
    assert book(client, headers, turf.id, day, "22:30").status_code == 422  # runs past closing
    assert book(client, headers, turf.id, day, "18:00", hours=5).status_code == 422  # above max hours
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    assert book(client, headers, turf.id, yesterday, "18:00").status_code == 422
    assert book(client, headers, 999999, day).status_code == 404

    closed = factory.turf(owner, status="maintenance")
    assert book(client, headers, closed.id, day).status_code == 422
    hidden = factory.turf(owner, is_verified=False)
    assert book(client, headers, hidden.id, day).status_code == 404


def test_overlapping_booking_is_rejected_but_back_to_back_is_fine(client, factory):
    owner, _ = factory.user("owner")
    _, alice = factory.user()
    _, bob = factory.user()
    turf = factory.turf(owner)
    day = future_day()
    assert book(client, alice, turf.id, day, "18:00", 2).status_code == 201
    clash = book(client, bob, turf.id, day, "19:00", 1)
    assert clash.status_code == 409
    assert "no longer available" in clash.json()["detail"]
    assert book(client, bob, turf.id, day, "20:00", 1).status_code == 201
    assert book(client, bob, turf.id, day, "17:00", 1).status_code == 201


def test_concurrent_requests_cannot_double_book(factory):
    """Five customers race for the same slot; the database lets exactly one win."""
    owner, _ = factory.user("owner")
    turf = factory.turf(owner)
    user_ids = [factory.user()[0].id for _ in range(5)]
    day = future_day()
    barrier = threading.Barrier(len(user_ids))
    results: list[str] = []

    def attempt(user_id: int) -> None:
        with SessionLocal() as session:
            user = session.get(User, user_id)
            turf_row = session.get(Turf, turf.id)
            barrier.wait()
            try:
                booking_service.create_booking(
                    session, user=user, turf=turf_row, day=day, start_time=time(18), duration_hours=1
                )
                session.commit()
                results.append("booked")
            except Conflict:
                results.append("conflict")

    threads = [threading.Thread(target=attempt, args=(uid,)) for uid in user_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == ["booked"] + ["conflict"] * 4


def test_expired_hold_releases_the_slot(client, factory, db):
    owner, _ = factory.user("owner")
    early_bird, _ = factory.user()
    _, latecomer = factory.user()
    turf = factory.turf(owner)
    day = future_day()
    stale = factory.booking(early_bird, turf, at(day, 18), hold_expires_at=datetime.now(UTC) - timedelta(minutes=1))

    availability = client.get(f"/turfs/{turf.id}/availability", params={"date": day.isoformat()}).json()
    assert availability["booked"] == []  # expired hold no longer blocks

    assert book(client, latecomer, turf.id, day, "18:00").status_code == 201
    db.expire_all()
    assert db.get(Booking, stale.id).status == "expired"


def test_unpaid_booking_limit_per_customer(client, factory):
    owner, _ = factory.user("owner")
    _, headers = factory.user()
    turf = factory.turf(owner)
    day = future_day()
    for start in ("08:00", "10:00", "12:00"):
        assert book(client, headers, turf.id, day, start).status_code == 201
    too_many = book(client, headers, turf.id, day, "14:00")
    assert too_many.status_code == 422
    assert "unpaid" in too_many.json()["detail"]


def test_owner_confirms_payment_and_customer_gets_receipt(client, factory):
    owner, owner_headers = factory.user("owner")
    _, other_owner_headers = factory.user("owner")
    _, customer_headers = factory.user()
    turf = factory.turf(owner)
    booking = book(client, customer_headers, turf.id, future_day()).json()
    booking_id = booking["id"]
    confirm = {"method": "upi", "reference": "UPI-778899"}

    early_receipt = client.get(f"/bookings/{booking_id}/receipt", headers=customer_headers)
    assert early_receipt.status_code == 409

    assert (
        client.post(f"/bookings/{booking_id}/confirm-payment", headers=customer_headers, json=confirm).status_code
        == 403
    )
    assert (
        client.post(f"/bookings/{booking_id}/confirm-payment", headers=other_owner_headers, json=confirm).status_code
        == 404
    )

    confirmed = client.post(f"/bookings/{booking_id}/confirm-payment", headers=owner_headers, json=confirm)
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "confirmed" and body["payment_status"] == "paid"
    assert body["payment_reference"] == "UPI-778899"
    assert Decimal(body["paid_amount"]) == Decimal(booking["total_amount"])
    assert body["hold_expires_at"] is None

    again = client.post(f"/bookings/{booking_id}/confirm-payment", headers=owner_headers, json=confirm)
    assert again.status_code == 409

    receipt = client.get(f"/bookings/{booking_id}/receipt", headers=customer_headers)
    assert receipt.status_code == 200
    assert receipt.headers["content-type"] == "application/pdf"
    assert receipt.content.startswith(b"%PDF")

    titles = [n["title"] for n in client.get("/notifications", headers=customer_headers).json()["items"]]
    assert any("confirmed" in t for t in titles)


def test_admin_can_confirm_any_payment(client, factory):
    owner, _ = factory.user("owner")
    _, admin_headers = factory.user("admin")
    customer, _ = factory.user()
    turf = factory.turf(owner)
    booking = factory.booking(customer, turf, at(future_day(), 9))
    response = client.post(f"/bookings/{booking.id}/confirm-payment", headers=admin_headers, json={"method": "cash"})
    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"


def test_cancelling_paid_booking_creates_refund_then_owner_marks_refunded(client, factory):
    owner, owner_headers = factory.user("owner")
    customer, customer_headers = factory.user()
    turf = factory.turf(owner)
    booking = factory.booking(customer, turf, at(future_day(), 18), paid=True)

    cancelled = client.post(f"/bookings/{booking.id}/cancel", headers=customer_headers, json={"reason": "Rain"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["payment_status"] == "refund_pending"

    assert client.post(f"/bookings/{booking.id}/cancel", headers=customer_headers).status_code == 409
    assert client.post(f"/bookings/{booking.id}/mark-refunded", headers=customer_headers).status_code == 403
    refunded = client.post(f"/bookings/{booking.id}/mark-refunded", headers=owner_headers)
    assert refunded.json()["payment_status"] == "refunded"


def test_customer_cancellation_cutoff_but_owner_can_still_cancel(client, factory):
    owner, owner_headers = factory.user("owner")
    customer, customer_headers = factory.user()
    turf = factory.turf(owner)
    soon = datetime.now(UTC) + timedelta(minutes=30)
    booking = factory.booking(customer, turf, soon)

    too_late = client.post(f"/bookings/{booking.id}/cancel", headers=customer_headers)
    assert too_late.status_code == 422
    by_owner = client.post(f"/bookings/{booking.id}/cancel", headers=owner_headers, json={"reason": "Pitch flooded"})
    assert by_owner.status_code == 200
    assert by_owner.json()["cancellation_reason"] == "Pitch flooded"
    alerts = client.get("/notifications", headers=customer_headers).json()["items"]
    assert alerts[0]["type"] == "alert"


def test_complete_only_after_booking_ends(client, factory):
    owner, owner_headers = factory.user("owner")
    customer, _ = factory.user()
    turf = factory.turf(owner)
    upcoming = factory.booking(customer, turf, at(future_day(), 18), paid=True)
    finished = factory.booking(customer, turf, datetime.now(UTC) - timedelta(hours=3), paid=True)
    assert client.post(f"/bookings/{upcoming.id}/complete", headers=owner_headers).status_code == 422
    done = client.post(f"/bookings/{finished.id}/complete", headers=owner_headers)
    assert done.status_code == 200
    assert done.json()["status"] == "completed"


def test_booking_visibility(client, factory):
    owner, owner_headers = factory.user("owner")
    customer, customer_headers = factory.user()
    _, stranger_headers = factory.user()
    turf = factory.turf(owner)
    booking = factory.booking(customer, turf, at(future_day(), 18))
    assert client.get(f"/bookings/{booking.id}", headers=customer_headers).status_code == 200
    assert client.get(f"/bookings/{booking.id}", headers=owner_headers).status_code == 200
    assert client.get(f"/bookings/{booking.id}", headers=stranger_headers).status_code == 404
    assert client.post(f"/bookings/{booking.id}/cancel", headers=stranger_headers).status_code == 404


def test_my_bookings_scopes(client, factory):
    owner, _ = factory.user("owner")
    customer, headers = factory.user()
    turf = factory.turf(owner)
    upcoming = factory.booking(customer, turf, at(future_day(), 18))
    past = factory.booking(customer, turf, datetime.now(UTC) - timedelta(days=2), paid=True)

    def ids(scope):
        return [b["id"] for b in client.get("/bookings/me", headers=headers, params={"scope": scope}).json()["items"]]

    assert ids("upcoming") == [upcoming.id]
    assert ids("past") == [past.id]
    assert sorted(ids("all")) == sorted([upcoming.id, past.id])


def test_owner_booking_list_dashboard_and_payments(client, factory):
    owner, owner_headers = factory.user("owner")
    other_owner, _ = factory.user("owner")
    customer, _ = factory.user()
    turf = factory.turf(owner, price_per_hour=Decimal("1000"))
    other_turf = factory.turf(other_owner)
    pending = factory.booking(customer, turf, at(future_day(), 8))
    factory.booking(customer, turf, at(future_day(), 18), paid=True)
    factory.booking(customer, other_turf, at(future_day(), 18), paid=True)

    listing = client.get("/owner/bookings", headers=owner_headers).json()
    assert listing["total"] == 2  # never sees other owners' bookings
    waiting = client.get("/owner/bookings", headers=owner_headers, params={"status": "pending"}).json()
    assert [b["id"] for b in waiting["items"]] == [pending.id]
    assert waiting["items"][0]["customer"]["phone"] == customer.phone

    dashboard = client.get("/owner/dashboard", headers=owner_headers).json()
    assert dashboard["turfs"] == 1
    assert dashboard["pending_payments"] == 1
    assert dashboard["upcoming_confirmed"] == 1
    assert Decimal(dashboard["revenue_total"]) == Decimal("1000.00")

    payments = client.get("/owner/payments", headers=owner_headers).json()
    assert payments["count"] == 1
    assert Decimal(payments["total_collected"]) == Decimal("1000.00")
