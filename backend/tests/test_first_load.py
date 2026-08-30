"""Erstzugriff auf einen ungecachten Ordner blockiert nicht mehr auf dem ganzen Postfach.

Beschwerde war: Beim ersten Öffnen eines Gmail-Kontos dauert das Laden extrem
lange. Ursache war ein synchroner Voll-Sync im Request (jede Mail komplett, dann
vom Frontend ein zweites Mal). Jetzt holt der Request nur eine kurze erste Seite
(_FIRST_SYNC_CAP) und stößt den Rest im Hintergrund an.
"""
import app.api.mail as mail_api


def _header(uid: str) -> dict:
    return {
        "uid": uid, "subject": f"Betreff {uid}", "from": "von@example.com",
        "date": "Sat, 30 Aug 2026 10:00:00 +0200", "seen": False, "flagged": False,
        "snippet": "", "has_attachments": False,
    }


def test_erstzugriff_holt_nur_kurze_erste_seite(client, admin, account, monkeypatch):
    """Der Request synct nur _FIRST_SYNC_CAP Mails; der Rest laeuft im Hintergrund."""
    syncs: list[int] = []
    fills: list[dict] = []

    def fake_sync(_session, _acc, _pw, folder, cap=None, **_kwargs):
        syncs.append(cap)
        return {"total": 0, "unseen": 0, "new": 0, "ok": True}

    def fake_fill(account_id, _pw, folder, cap, user_id):
        fills.append({"account_id": account_id, "folder": folder, "cap": cap, "user_id": user_id})

    monkeypatch.setattr(mail_api.cache_mod, "has_cache", lambda *_a, **_k: False)
    monkeypatch.setattr(mail_api.cache_mod, "sync_folder", fake_sync)
    # Nach dem Erst-Sync liegen Mails im Cache -> der Self-heal-Zweig greift nicht.
    monkeypatch.setattr(
        mail_api.cache_mod, "read_messages",
        lambda *_a, **_k: [_header(str(i)) for i in range(mail_api._FIRST_SYNC_CAP)],
    )
    monkeypatch.setattr(mail_api, "_fill_folder_async", fake_fill)

    r = client.get(f"/api/v1/mail/{account}/messages?folder=INBOX&limit=50", headers=admin)
    assert r.status_code == 200, r.text
    assert len(r.json()) == mail_api._FIRST_SYNC_CAP

    # GENAU EIN synchroner Sync, und der nur ueber die kurze erste Seite.
    assert syncs == [mail_api._FIRST_SYNC_CAP]
    assert mail_api._FIRST_SYNC_CAP < 50, "erste Seite muss kleiner als eine volle Seite sein"

    # Der Rest wurde im Hintergrund angestossen — mit dem vollen Limit.
    assert len(fills) == 1
    assert fills[0]["folder"] == "INBOX"
    assert fills[0]["cap"] == 50
    assert fills[0]["account_id"] == account


def test_gecachter_ordner_synct_im_request_gar_nicht(client, admin, account, monkeypatch):
    """Ist der Ordner bekannt, kommt die Liste rein aus dem Cache — kein IMAP im Request."""
    syncs: list[int] = []

    monkeypatch.setattr(mail_api.cache_mod, "has_cache", lambda *_a, **_k: True)
    monkeypatch.setattr(
        mail_api.cache_mod, "sync_folder",
        lambda *_a, **k: syncs.append(k.get("cap")) or {"ok": True},
    )
    monkeypatch.setattr(mail_api.cache_mod, "read_messages", lambda *_a, **_k: [_header("1")])

    r = client.get(f"/api/v1/mail/{account}/messages?folder=INBOX", headers=admin)
    assert r.status_code == 200, r.text
    assert syncs == [], "gecachter Ordner darf im Request nicht synchronisieren"
