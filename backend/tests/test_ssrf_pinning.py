"""Tests zu Befund 10 (IP-Pinning fuer IMAP/SMTP) und Befund 11 (imap_ssl).

Durchsicht 2026-09-27: Host wurde beim Speichern geprueft, verbunden wurde aber
wieder per Namen (DNS-Rebinding), und imap_ssl wurde nie gelesen.

Der SMTP-Test spricht mit einem winzigen echten SMTP-Server auf 127.0.0.1 - so
ist belegt, dass der uebergebene Socket (sock=) wirklich benutzt wird und der
Versand darueber funktioniert, nicht nur, dass die Funktion aufgerufen wurde.
"""
from __future__ import annotations

import asyncio
import socket

import pytest
from fastapi import HTTPException

from app.dav.client import DavUrlError, resolve_pinned_host
from app.mail import imap as imap_mod
from app.mail import smtp as smtp_mod
from app.models import MailAccount


def _konto(**kw) -> MailAccount:
    daten = dict(
        user_id=1, email="ich@example.org", imap_host="example.org", imap_port=993,
        smtp_host="example.org", smtp_port=587, imap_ssl=True, smtp_starttls=False,
        password_enc="x",
    )
    daten.update(kw)
    return MailAccount(**daten)


# --- Befund 10: gesperrte Ziele -------------------------------------------------

def test_loopback_wird_beim_verbinden_blockiert():
    """localhost loest auf 127.0.0.1 auf - resolve_pinned_host muss ablehnen."""
    with pytest.raises(DavUrlError):
        resolve_pinned_host("localhost", 993)


def test_imap_verbindung_zu_loopback_gibt_400():
    with pytest.raises(HTTPException) as fehler:
        imap_mod._pinned_ip_fuer(_konto(imap_host="localhost"))
    assert fehler.value.status_code == 400


def test_smtp_socket_zu_loopback_wird_abgelehnt():
    with pytest.raises(smtp_mod.SMTPZielNichtErlaubt):
        smtp_mod._gepinnter_socket(_konto(smtp_host="localhost"))


def test_gepinnte_ip_wird_wirklich_benutzt(monkeypatch):
    """Die MailBox verbindet zur uebergebenen IP, nicht zum Namen (kein 2. DNS)."""
    gesehen: list[tuple] = []

    def falsches_create_connection(adresse, timeout=None, *a, **kw):
        gesehen.append(adresse)
        raise OSError("Verbindung im Test nicht gewollt")

    monkeypatch.setattr(imap_mod, "resolve_pinned_host", lambda host, port: "203.0.113.7")
    monkeypatch.setattr(imap_mod.socket, "create_connection", falsches_create_connection)
    with pytest.raises(OSError):
        imap_mod._neue_mailbox(_konto())
    assert gesehen == [("203.0.113.7", 993)]


# --- Befund 11: imap_ssl ------------------------------------------------------

def test_port_993_bleibt_implizites_tls_auch_ohne_schalter():
    """Bestandskonten: imap_ssl war wirkungslos, 993 lief immer mit implizitem TLS."""
    assert imap_mod._implizites_tls(_konto(imap_ssl=False, imap_port=993)) is True


def test_imap_ssl_aus_auf_port_143_nutzt_starttls(monkeypatch):
    monkeypatch.setattr(imap_mod, "resolve_pinned_host", lambda host, port: "203.0.113.7")
    monkeypatch.setattr(
        imap_mod.socket, "create_connection",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("kein Server im Test")),
    )
    assert imap_mod._implizites_tls(_konto(imap_ssl=False, imap_port=143)) is False
    with pytest.raises(OSError):
        imap_mod._neue_mailbox(_konto(imap_ssl=False, imap_port=143))


def test_imap_ssl_an_nutzt_implizites_tls():
    assert imap_mod._implizites_tls(_konto(imap_ssl=True, imap_port=143)) is True


# --- Befund 10: echter Versand ueber den gepinnten Socket ---------------------

class _MiniSMTP:
    """Minimaler SMTP-Server (Klartext, AUTH PLAIN) fuer genau einen Versand."""

    def __init__(self) -> None:
        self.empfangen = b""
        self.port = 0
        self._server = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._dialog, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _dialog(self, leser: asyncio.StreamReader, schreiber: asyncio.StreamWriter) -> None:
        async def sag(text: str) -> None:
            schreiber.write(text.encode() + b"\r\n")
            await schreiber.drain()

        await sag("220 mini.example.org ESMTP")
        while True:
            zeile = await leser.readline()
            if not zeile:
                break
            befehl = zeile.decode("utf-8", "replace").strip()
            oben = befehl.upper()
            if oben.startswith(("EHLO", "HELO")):
                await sag("250-mini.example.org")
                await sag("250 AUTH PLAIN LOGIN")
            elif oben.startswith("AUTH"):
                await sag("235 2.7.0 Authentication successful")
            elif oben.startswith("MAIL FROM"):
                await sag("250 2.1.0 Ok")
            elif oben.startswith("RCPT TO"):
                await sag("250 2.1.5 Ok")
            elif oben == "DATA":
                await sag("354 End data with <CR><LF>.<CR><LF>")
                while True:
                    daten = await leser.readline()
                    if not daten or daten.strip() == b".":
                        break
                    self.empfangen += daten
                await sag("250 2.0.0 Ok: queued")
            elif oben == "QUIT":
                await sag("221 2.0.0 Bye")
                break
            else:
                await sag("250 2.0.0 Ok")
        schreiber.close()


def test_versand_laeuft_ueber_den_gepinnten_socket(monkeypatch):
    """_send muss den vorbereiteten Socket benutzen - hier gegen einen echten
    Mini-SMTP-Server auf 127.0.0.1. Die SSRF-Pruefung wird dafuer bewusst
    uebersprungen (Loopback ist im Betrieb verboten), der Socket-Pfad nicht."""
    from email.message import EmailMessage

    async def lauf() -> bytes:
        server = _MiniSMTP()
        await server.start()
        konto = _konto(smtp_host="mini.example.org", smtp_port=server.port, smtp_starttls=False)
        monkeypatch.setattr(smtp_mod, "resolve_pinned_host", lambda host, port: "127.0.0.1")
        msg = EmailMessage()
        msg["From"] = "ich@example.org"
        msg["To"] = "du@example.org"
        msg["Subject"] = "Pinning-Test"
        msg.set_content("Hallo vom gepinnten Socket")
        try:
            await smtp_mod._send(konto, "geheim", msg, ["du@example.org"])
        finally:
            await server.stop()
        return server.empfangen

    empfangen = asyncio.run(lauf())
    assert b"Pinning-Test" in empfangen
    assert b"gepinnten Socket" in empfangen


def test_socket_wird_nach_dem_versand_geschlossen():
    """Kein Socket-Leck: _socket_schliessen darf auch doppelt aufgerufen werden."""
    a, b = socket.socketpair()
    try:
        smtp_mod._socket_schliessen(a)
        smtp_mod._socket_schliessen(a)  # zweites Mal harmlos
        assert a.fileno() == -1
    finally:
        b.close()
