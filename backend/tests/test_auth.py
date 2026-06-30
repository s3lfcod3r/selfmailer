"""Auth, Setup, Login, /me, Sicherheits-Verhalten."""


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "build" in body


def test_admin_setup_and_me(client, admin):
    r = client.get("/api/v1/auth/me", headers=admin)
    assert r.status_code == 200
    me = r.json()
    assert me["username"] == "admin@self"
    assert me["role"] == "admin"
    assert me["is_active"] is True


def test_setup_conflict_after_admin_exists(client, admin):
    # Admin existiert bereits -> erneutes Setup ist 409.
    r = client.post("/api/v1/auth/setup", json={"username": "x@y", "password": "supersecret-123"})
    assert r.status_code == 409


def test_login_wrong_password(client, admin):
    r = client.post("/api/v1/auth/login", json={"username": "admin@self", "password": "falsch"})
    assert r.status_code == 401


def test_login_unknown_user(client, admin):
    r = client.post("/api/v1/auth/login", json={"username": "nobody@self", "password": "whatever-123"})
    assert r.status_code == 401


def test_me_requires_auth(client):
    r = client.get("/api/v1/auth/me")
    assert r.status_code in (401, 403)


def test_change_password_roundtrip(client, admin):
    # Falsches aktuelles PW -> 400
    r = client.post("/api/v1/auth/password", headers=admin,
                    json={"current_password": "falsch", "new_password": "neuesPW-123"})
    assert r.status_code == 400
    # Richtiges aktuelles PW -> ok, dann wieder zurück ändern
    r = client.post("/api/v1/auth/password", headers=admin,
                    json={"current_password": "supersecret-123", "new_password": "neuesPW-123"})
    assert r.status_code == 200
    r = client.post("/api/v1/auth/password", headers=admin,
                    json={"current_password": "neuesPW-123", "new_password": "supersecret-123"})
    assert r.status_code == 200
