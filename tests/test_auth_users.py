import io
import re

from PIL import Image

from app.services import email
from tests.conftest import PASSWORD, login


def register(client, **overrides):
    body = {
        "first_name": "Asha",
        "last_name": "K",
        "email": "Asha@Example.com",
        "phone": "9876543210",
        "password": PASSWORD,
        "accept_terms": True,
    }
    body.update(overrides)
    return client.post("/auth/register", json=body)


def test_register_login_and_me(client):
    response = register(client)
    assert response.status_code == 201, response.text
    assert response.json()["email"] == "asha@example.com"  # normalised
    assert response.json()["role"] == "user"
    assert "password_hash" not in response.json()

    headers = login(client, "asha@example.com")
    me = client.get("/users/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["first_name"] == "Asha"


def test_register_rejects_duplicates_and_missing_terms(client):
    assert register(client).status_code == 201
    assert register(client, phone="9999999999").status_code == 409
    assert register(client, email="other@example.com").status_code == 409  # same phone
    assert register(client, email="x@example.com", phone="9111111111", accept_terms=False).status_code == 422
    assert register(client, email="y@example.com", phone="9222222222", role="admin").status_code == 422


def test_login_failures(client, factory):
    user, _ = factory.user()
    bad = client.post("/auth/login", data={"username": user.email, "password": "wrong-password"})
    assert bad.status_code == 401
    unknown = client.post("/auth/login", data={"username": "nobody@example.com", "password": PASSWORD})
    assert unknown.status_code == 401
    assert client.get("/users/me").status_code == 401
    assert client.get("/users/me", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_blocked_user_is_locked_out(client, factory):
    user, headers = factory.user()
    _, admin_headers = factory.user("admin")
    assert client.post(f"/admin/users/{user.id}/block", headers=admin_headers).status_code == 200
    assert client.get("/users/me", headers=headers).status_code == 403
    login_attempt = client.post("/auth/login", data={"username": user.email, "password": PASSWORD})
    assert login_attempt.status_code == 403
    assert client.post(f"/admin/users/{user.id}/unblock", headers=admin_headers).status_code == 200
    assert client.get("/users/me", headers=headers).status_code == 200


def test_password_change_invalidates_old_tokens(client, factory):
    user, headers = factory.user()
    wrong = client.post(
        "/users/me/password", headers=headers, json={"current_password": "nope", "new_password": "NewPassw0rd!"}
    )
    assert wrong.status_code == 400
    ok = client.post(
        "/users/me/password", headers=headers, json={"current_password": PASSWORD, "new_password": "NewPassw0rd!"}
    )
    assert ok.status_code == 204
    assert client.get("/users/me", headers=headers).status_code == 401
    assert client.get("/users/me", headers=login(client, user.email, "NewPassw0rd!")).status_code == 200


def test_logout_everywhere(client, factory):
    _, headers = factory.user()
    assert client.post("/auth/logout-all", headers=headers).status_code == 204
    assert client.get("/users/me", headers=headers).status_code == 401


def test_password_reset_flow(client, factory):
    user, old_headers = factory.user()
    unknown = client.post("/auth/password-reset/request", json={"email": "ghost@example.com"})
    assert unknown.status_code == 202
    assert email.outbox == []  # same response, no email: accounts can't be enumerated

    assert client.post("/auth/password-reset/request", json={"email": user.email}).status_code == 202
    assert len(email.outbox) == 1
    code = re.search(r"\b(\d{6})\b", email.outbox[0].get_content()).group(1)

    wrong_code = "000000" if code != "000000" else "111111"
    bad = client.post(
        "/auth/password-reset/confirm", json={"email": user.email, "code": wrong_code, "new_password": "Reset123!"}
    )
    assert bad.status_code == 400
    good = client.post(
        "/auth/password-reset/confirm", json={"email": user.email, "code": code, "new_password": "Reset123!"}
    )
    assert good.status_code == 200, good.text
    assert client.get("/users/me", headers=old_headers).status_code == 401
    login(client, user.email, "Reset123!")
    # codes are single-use
    reuse = client.post(
        "/auth/password-reset/confirm", json={"email": user.email, "code": code, "new_password": "Again123!"}
    )
    assert reuse.status_code == 400


def test_password_reset_locks_after_too_many_attempts(client, factory):
    user, _ = factory.user()
    client.post("/auth/password-reset/request", json={"email": user.email})
    code = re.search(r"\b(\d{6})\b", email.outbox[0].get_content()).group(1)
    wrong_code = "000000" if code != "000000" else "111111"
    for _ in range(5):
        client.post(
            "/auth/password-reset/confirm", json={"email": user.email, "code": wrong_code, "new_password": "Reset123!"}
        )
    locked = client.post(
        "/auth/password-reset/confirm", json={"email": user.email, "code": code, "new_password": "Reset123!"}
    )
    assert locked.status_code == 400


def test_update_profile_and_avatar(client, factory):
    other, _ = factory.user()
    _, headers = factory.user()
    clash = client.patch("/users/me", headers=headers, json={"phone": other.phone})
    assert clash.status_code == 409
    updated = client.patch("/users/me", headers=headers, json={"first_name": "Renamed"})
    assert updated.json()["first_name"] == "Renamed"

    png = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(png, format="PNG")
    upload = client.put("/users/me/avatar", headers=headers, files={"file": ("me.png", png.getvalue(), "image/png")})
    assert upload.status_code == 200, upload.text
    url = upload.json()["profile_image_url"]
    assert url.startswith("/media/avatars/") and url.endswith(".png")
    assert client.get(url).status_code == 200

    not_image = client.put("/users/me/avatar", headers=headers, files={"file": ("x.png", b"hello", "image/png")})
    assert not_image.status_code == 422
