"""Ist die Konto-Verbindung belegt, blockiert der interaktive Sync nicht mehr.

Gemessen am 30.08.2026 an einem echten Gmail-Konto: `POST /mail/9/sync` hing
zweimal exakt 20 s (der Konto-Lock-Timeout) und endete mit HTTP 502 — die
Oberflaeche zeigte also einen Fehler, obwohl nur der Hintergrund-Dienst
dasselbe Konto synchronisierte. Ein echter Fehler (Netz weg, Login falsch)
muss aber weiterhin als 502 durchschlagen.
"""
import app.api.mail as mail_api
from app.mail.imap import ImapBusyError


def test_belegtes_konto_liefert_busy_statt_fehler(client, admin, account, monkeypatch):
    """Belegte Verbindung -> HTTP 200 mit busy=True, kein 502."""
    def fake_sync(*_a, **_k):
        raise ImapBusyError()

    monkeypatch.setattr(mail_api.cache_mod, "sync_folder", fake_sync)

    r = client.post(f"/api/v1/mail/{account}/sync?folder=INBOX", headers=admin)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["busy"] is True
    assert body["ok"] is True
    assert body["new"] == 0


def test_interaktiver_sync_wartet_nur_kurz(client, admin, account, monkeypatch):
    """Der Endpunkt reicht die KURZE Wartezeit durch, nicht die 20 s des Hintergrunds."""
    gesehen: dict = {}

    def fake_sync(_session, _acc, _pw, folder, **kwargs):
        gesehen.update(kwargs)
        return {"total": 0, "unseen": 0, "new": 0, "ok": True}

    monkeypatch.setattr(mail_api.cache_mod, "sync_folder", fake_sync)

    r = client.post(f"/api/v1/mail/{account}/sync?folder=INBOX", headers=admin)

    assert r.status_code == 200, r.text
    assert gesehen["lock_timeout"] == mail_api._UI_LOCK_TIMEOUT
    assert mail_api._UI_LOCK_TIMEOUT < 20, "muss deutlich unter dem Hintergrund-Timeout liegen"
    # Der Name landet im Diagnose-Log als Sperr-Inhaber - daran ist im Log zu
    # erkennen, ob Oberflaeche oder Hintergrund-Dienst blockiert.
    assert gesehen["op"] == "sync-ui"


def test_echter_fehler_bleibt_fehler(client, admin, account, monkeypatch):
    """Kein Freibrief: was NICHT "belegt" ist, muss weiterhin als 502 auffallen."""
    def fake_sync(*_a, **_k):
        raise RuntimeError("IMAP-Login abgelehnt")

    monkeypatch.setattr(mail_api.cache_mod, "sync_folder", fake_sync)

    r = client.post(f"/api/v1/mail/{account}/sync?folder=INBOX", headers=admin)

    assert r.status_code == 502, r.text
