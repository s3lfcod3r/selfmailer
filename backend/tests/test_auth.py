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
    # Richtiges aktuelles PW -> ok, dann wieder zurück ändern.
    # Seit 1.96.0 (Befund 4) entwertet ein Passwortwechsel alle alten JWTs;
    # die Antwort liefert ein frisches Token. Der Aufrufer muss es übernehmen,
    # sonst laeuft er ab dem zweiten Aufruf in 401 - genau das prueft der Test
    # mit, indem er den gemeinsamen Admin-Header aktualisiert.
    r = client.post("/api/v1/auth/password", headers=admin,
                    json={"current_password": "supersecret-123", "new_password": "neuesPW-123"})
    assert r.status_code == 200
    neu = r.json()
    assert neu["access_token"]
    assert neu["feed_token_hint"] == "Feed-Token jetzt rotieren?"
    alt = dict(admin)
    admin["Authorization"] = f"Bearer {neu['access_token']}"
    # Das alte Token ist ab jetzt ungueltig (Sitzungs-Widerruf).
    assert client.get("/api/v1/auth/me", headers=alt).status_code == 401
    # Das neue Token funktioniert.
    assert client.get("/api/v1/auth/me", headers=admin).status_code == 200
    r = client.post("/api/v1/auth/password", headers=admin,
                    json={"current_password": "neuesPW-123", "new_password": "supersecret-123"})
    assert r.status_code == 200
    admin["Authorization"] = f"Bearer {r.json()['access_token']}"


def test_alte_jwts_ohne_token_version_bleiben_gueltig(client, admin):
    """Befund 4, Rueckwaertskompatibilitaet: ein Token aus 1.95.x hat keinen
    "tv"-Claim und auch kein iss/aud. Es muss bis zu seinem Ablauf weiter
    gelten, sonst wuerde das Update alle angemeldeten Geraete (inkl.
    Android-App) abmelden.

    Geprueft wird das an einem frisch angelegten Konto: dort ist
    token_version noch 0 - genau der Zustand jedes Bestandskontos direkt nach
    dem Update auf 1.96.0.
    """
    import datetime as dt

    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.crypto import jwt_key

    client.post("/api/v1/admin/users", headers=admin, json={
        "username": "altbestand@self", "password": "altbestand-supersecret-123",
        "display_name": "Altbestand", "role": "user",
    })  # 201 oder 409 (schon vorhanden) - beides ok

    settings = get_settings()
    now = dt.datetime.now(dt.timezone.utc)
    # Genau das Token-Format von 1.95.5: kein tv, kein iss, kein aud.
    alt = pyjwt.encode(
        {"sub": "altbestand@self", "role": "user", "iat": now,
         "exp": now + dt.timedelta(minutes=30)},
        jwt_key(), algorithm=settings.jwt_algorithm,
    )
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {alt}"})
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "altbestand@self"

    # Fremdes iss/aud (anderer Dienst, gleiches Secret) wird abgelehnt.
    fremd = pyjwt.encode(
        {"sub": "altbestand@self", "role": "user", "iss": "anderer-dienst",
         "aud": "anderer-dienst", "iat": now, "exp": now + dt.timedelta(minutes=30)},
        jwt_key(), algorithm=settings.jwt_algorithm,
    )
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {fremd}"})
    assert r.status_code == 401


def test_logout_all_entwertet_alle_sitzungen(client, admin):
    """Befund 4: "ueberall abmelden" erhoeht token_version -> auch das eigene
    Token ist danach tot; Feed-Token bleiben unberuehrt."""
    r = client.post("/api/v1/auth/login",
                    json={"username": "admin@self", "password": "supersecret-123"})
    assert r.status_code == 200, r.text
    zweit = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.cookies.clear()
    assert client.get("/api/v1/auth/me", headers=zweit).status_code == 200

    r = client.post("/api/v1/auth/logout-all", headers=zweit)
    assert r.status_code == 200, r.text
    assert r.json()["feed_token_hint"] == "Feed-Token jetzt rotieren?"
    client.cookies.clear()
    # Beide Token (das benutzte und das des Fixtures) sind jetzt ungueltig.
    assert client.get("/api/v1/auth/me", headers=zweit).status_code == 401
    assert client.get("/api/v1/auth/me", headers=admin).status_code == 401

    # Fixture-Token erneuern, damit die folgenden Tests weiterarbeiten.
    r = client.post("/api/v1/auth/login",
                    json={"username": "admin@self", "password": "supersecret-123"})
    assert r.status_code == 200, r.text
    admin["Authorization"] = f"Bearer {r.json()['access_token']}"
    client.cookies.clear()
