"""Kleinere Haertungen (Durchsicht 2026-10-24).

1. Uebersetzung: verbindet zur gepruefen IP (post_pinned) statt per Namen.
2. Push-Ordner: Steuerzeichen in Ordnernamen werden gar nicht erst gespeichert.
"""
from __future__ import annotations

import httpx

from app.api import translate as translate_mod
from app.core.config import get_settings
from app.dav.client import DavUrlError


def _translate_an(monkeypatch, url: str = "http://translate.example:5000") -> None:
    monkeypatch.setattr(get_settings(), "translate_url", url)
    monkeypatch.setattr(get_settings(), "translate_api_key", "")


def test_uebersetzung_nutzt_gepinnten_post(client, admin, monkeypatch):
    _translate_an(monkeypatch)
    gesehen: list[tuple[str, object]] = []

    def fake_post(url, *, json=None, timeout=10.0):
        gesehen.append((url, json))
        return httpx.Response(200, json={"translatedText": "Hallo", "detectedLanguage": {"language": "en"}},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(translate_mod, "post_pinned", fake_post)
    r = client.post("/api/v1/translate", headers=admin, json={"text": "Hello", "target": "de"})
    assert r.status_code == 200, r.text
    assert r.json() == {"translated": "Hallo", "source": "en"}
    assert gesehen and gesehen[0][0] == "http://translate.example:5000/translate"


def test_uebersetzung_blockierte_url_gibt_502(client, admin, monkeypatch):
    _translate_an(monkeypatch, "http://169.254.169.254")

    def blockiert(url, **kw):
        raise DavUrlError("Metadata-Ziel")

    monkeypatch.setattr(translate_mod, "post_pinned", blockiert)
    r = client.post("/api/v1/translate", headers=admin, json={"text": "Hello"})
    assert r.status_code == 502


def test_uebersetzung_kaputte_antwort_gibt_502(client, admin, monkeypatch):
    _translate_an(monkeypatch)
    monkeypatch.setattr(translate_mod, "post_pinned", lambda url, **kw: httpx.Response(
        200, content=b"kein json", request=httpx.Request("POST", url)))
    r = client.post("/api/v1/translate", headers=admin, json={"text": "Hello"})
    assert r.status_code == 502


def test_push_ordner_mit_steuerzeichen_abgelehnt(client, admin, account):
    r = client.put("/api/v1/push/folders", headers=admin,
                   json={"account_id": account, "folders": ["INBOX", "Evil\r\nA1 LOGOUT"]})
    assert r.status_code == 422
    assert client.get(f"/api/v1/push/folders?account_id={account}", headers=admin).json() == []


def test_push_ordner_normal_gespeichert(client, admin, account):
    r = client.put("/api/v1/push/folders", headers=admin,
                   json={"account_id": account, "folders": ["INBOX", "Arbeit/Rechnungen", "Entw&APw-rfe"]})
    assert r.status_code == 200, r.text
    assert r.json() == ["Arbeit/Rechnungen", "Entw&APw-rfe", "INBOX"]
    client.put("/api/v1/push/folders", headers=admin, json={"account_id": account, "folders": []})
