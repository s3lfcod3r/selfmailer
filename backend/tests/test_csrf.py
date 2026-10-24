"""CSRF-Schutz fuer Cookie-Sitzungen (Durchsicht 2026-10-24).

Kern-Szenario: eine andere Seite auf derselben IP, nur anderer Port
(http://192.168.1.10:3000), ist fuer den Browser "same-site" - SameSite=Lax
schickt das Session-Cookie also mit. Diese Anfragen muessen mit 403 enden;
die eigene Oberflaeche, die APK (Bearer) und Feed-Clients (ohne Cookie) nicht.
"""
from __future__ import annotations

import pytest

from app.core import csrf

COOKIE = "sm_session"
TRUSTED = csrf.trusted_origins(["http://localhost:5173"], "https://mail.example.org/")
OWN_HOST = "192.168.1.10:8090"


def _pruefe(method: str = "POST", *, cookie: bool = True, **headers: str) -> str | None:
    h = {"host": OWN_HOST}
    h.update({k.replace("_", "-").lower(): v for k, v in headers.items()})
    return csrf.check_request(method, h, {COOKIE: "jwt"} if cookie else {}, cookie_name=COOKIE, trusted=TRUSTED)


# --- Abgelehnt ---------------------------------------------------------------

def test_anderer_port_derselben_ip_wird_abgelehnt():
    """Das eigentliche Szenario: same-site, aber fremder Origin."""
    assert _pruefe(origin="http://192.168.1.10:3000", sec_fetch_site="same-site") is not None


def test_fremde_site_wird_abgelehnt():
    assert _pruefe(origin="https://evil.example", sec_fetch_site="cross-site") is not None


def test_nur_origin_ohne_fetch_metadata_wird_geprueft():
    """Aeltere Browser ohne Sec-Fetch-Site schicken bei POST trotzdem Origin."""
    assert _pruefe(origin="http://192.168.1.10:3000") is not None


def test_origin_null_wird_abgelehnt():
    """Sandboxed iframes/data:-Seiten senden "null"."""
    assert _pruefe(origin="null", sec_fetch_site="cross-site") is not None


def test_cross_site_ohne_origin_wird_abgelehnt():
    assert _pruefe(sec_fetch_site="cross-site") is not None


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_alle_schreibenden_methoden(method):
    assert _pruefe(method, origin="https://evil.example", sec_fetch_site="cross-site") is not None


# --- Erlaubt -----------------------------------------------------------------

def test_eigene_oberflaeche_same_origin():
    assert _pruefe(origin=f"http://{OWN_HOST}", sec_fetch_site="same-origin") is None


def test_eigener_host_ohne_fetch_metadata():
    assert _pruefe(origin=f"http://{OWN_HOST}") is None


def test_eigener_host_hinter_tls_proxy():
    """Browser spricht https, beim Backend kommt nur der Host an - Schema egal."""
    assert _pruefe(origin=f"https://{OWN_HOST}") is None


def test_base_url_und_cors_origins_sind_vertrauenswuerdig():
    assert _pruefe(origin="https://mail.example.org", sec_fetch_site="cross-site") is None
    assert _pruefe(origin="http://localhost:5173", sec_fetch_site="same-site") is None


def test_adresszeile_oder_lesezeichen():
    assert _pruefe(sec_fetch_site="none") is None


def test_kein_browser():
    """curl/Skripte: weder Origin noch Sec-Fetch-Site."""
    assert _pruefe() is None


def test_bearer_ist_nicht_betroffen():
    """APK: Bearer gewinnt vor dem Cookie, ein fremder Tab kann ihn nicht setzen."""
    assert _pruefe(authorization="Bearer abc", origin="https://evil.example", sec_fetch_site="cross-site") is None


def test_ohne_session_cookie_nicht_betroffen():
    """Feed-/Schreib-Token-Clients (Dashboard-Kachel) melden sich ohne Cookie an."""
    assert _pruefe(cookie=False, origin="https://evil.example", sec_fetch_site="cross-site") is None


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_lesende_methoden_nicht_betroffen(method):
    assert _pruefe(method, origin="https://evil.example", sec_fetch_site="cross-site") is None


def test_origin_normalisierung():
    assert csrf.normalize_origin("HTTPS://Mail.Example.org/pfad?x=1") == "https://mail.example.org"
    assert csrf.normalize_origin("null") == ""
    assert csrf.normalize_origin("ftp://x") == ""
    assert csrf.normalize_origin("") == ""


# --- Ende-zu-Ende ueber die echte App ---------------------------------------

def _cookie_header(admin: dict) -> dict:
    token = admin["Authorization"].split(" ", 1)[1]
    return {"Cookie": f"{COOKIE}={token}"}


def test_app_lehnt_fremde_herkunft_mit_cookie_ab(client, admin):
    r = client.put("/api/v1/settings/ui", json={}, headers={
        **_cookie_header(admin), "Origin": "http://192.168.1.10:3000", "Sec-Fetch-Site": "same-site",
    })
    assert r.status_code == 403
    assert r.json()["detail"] == "Anfrage von fremder Herkunft abgelehnt"
    # Die defensiven Header kommen auch auf der Ablehnung an.
    assert r.headers.get("x-content-type-options") == "nosniff"


def test_app_erlaubt_eigene_oberflaeche_mit_cookie(client, admin):
    r = client.put("/api/v1/settings/ui", json={}, headers={
        **_cookie_header(admin), "Origin": "http://testserver", "Sec-Fetch-Site": "same-origin",
    })
    assert r.status_code == 200, r.text


def test_app_erlaubt_bearer_trotz_fremdem_origin(client, admin):
    r = client.put("/api/v1/settings/ui", json={}, headers={
        **admin, "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site",
    })
    assert r.status_code == 200, r.text
