"""Tests zur IMAP-Zertifikatspruefung und zum Fingerprint-Pinning (Durchsicht 2026-10-24).

Bis 1.96.9 bekam imaplib nie einen ssl_context und benutzte damit den
ungeprueften stdlib-Kontext: jedes Zertifikat - auch ein selbstsigniertes eines
Angreifers - wurde akzeptiert, das Passwort ging danach per LOGIN raus.

Die Tests sprechen mit einem winzigen echten IMAP-Server auf 127.0.0.1 (eigene
Test-CA, erzeugt mit `cryptography`). So ist belegt, dass der Handshake wirklich
scheitert bzw. gelingt - und vor allem, dass bei einer Ablehnung NIE ein LOGIN
beim Server ankommt.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import imaplib
import socket
import ssl
import threading
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.core.fingerprint import normalize_fingerprint
from app.mail import imap as imap_mod
from app.mail import migrate as migrate_mod
from app.models import MailAccount

HOST = "mail.test"


# --- Zertifikate ----------------------------------------------------------------

def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _key_usage(*, ca: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=True, content_commitment=False, key_encipherment=False,
        data_encipherment=False, key_agreement=False, key_cert_sign=ca, crl_sign=ca,
        encipher_only=False, decipher_only=False,
    )


def _zertifikat(cn: str, key, *, issuer_name: x509.Name, issuer_key, ca: bool = False,
                dns: tuple[str, ...] = ()) -> x509.Certificate:
    """Zertifikat mit allen Erweiterungen, die Python 3.13 (VERIFY_X509_STRICT) verlangt."""
    jetzt = dt.datetime.now(dt.timezone.utc)
    b = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(jetzt - dt.timedelta(minutes=5))
        .not_valid_after(jetzt + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(_key_usage(ca=ca), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    )
    if not ca:
        b = b.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False
        ).add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    if dns:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in dns]), critical=False)
    return b.sign(issuer_key, hashes.SHA256())


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    """Test-CA, ein CA-signiertes und ein selbstsigniertes Zertifikat fuer mail.test."""
    tmp = tmp_path_factory.mktemp("pki")

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _zertifikat("SelfMailer Test-CA", ca_key, issuer_name=_name("SelfMailer Test-CA"),
                     issuer_key=ca_key, ca=True)

    def _ablegen(name: str, cert: x509.Certificate, key) -> SimpleNamespace:
        cert_pfad, key_pfad = tmp / f"{name}.crt", tmp / f"{name}.key"
        cert_pfad.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_pfad.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ))
        der = cert.public_bytes(serialization.Encoding.DER)
        return SimpleNamespace(cert=str(cert_pfad), key=str(key_pfad),
                               fingerprint=hashlib.sha256(der).hexdigest())

    signiert_key = ec.generate_private_key(ec.SECP256R1())
    signiert = _zertifikat(HOST, signiert_key, issuer_name=ca.subject, issuer_key=ca_key, dns=(HOST,))
    selbst_key = ec.generate_private_key(ec.SECP256R1())
    selbst = _zertifikat(HOST, selbst_key, issuer_name=_name(HOST), issuer_key=selbst_key, dns=(HOST,))

    return SimpleNamespace(
        ca_pem=ca.public_bytes(serialization.Encoding.PEM).decode("ascii"),
        signiert=_ablegen("signiert", signiert, signiert_key),
        selbst=_ablegen("selbst", selbst, selbst_key),
    )


# --- Mini-IMAP-Server -------------------------------------------------------------

class _MiniImap:
    """Zeilenbasierter IMAP-Server fuer genau die Befehle, die der Verbindungsaufbau
    braucht. Nimmt beliebig viele Verbindungen nacheinander an (der Zertifikat-
    Abruf baut zwei auf). Merkt sich, ob je ein LOGIN ankam."""

    def __init__(self, zert: SimpleNamespace, *, starttls: bool = False, starttls_angeboten: bool = True) -> None:
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(zert.cert, zert.key)
        self._starttls = starttls
        self._angeboten = starttls_angeboten
        self.login_gesehen = False
        self._lauscher = socket.create_server(("127.0.0.1", 0))
        self._lauscher.settimeout(5)
        self.port = self._lauscher.getsockname()[1]
        self._thread = threading.Thread(target=self._annehmen, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._lauscher.close()
        self._thread.join(timeout=5)

    def _annehmen(self) -> None:
        while True:
            try:
                conn, _ = self._lauscher.accept()
            except OSError:
                return
            try:
                self._dialog(conn)
            except (OSError, ssl.SSLError):
                pass  # Client hat (gewollt) abgebrochen
            finally:
                conn.close()

    def _dialog(self, conn: socket.socket) -> None:
        conn.settimeout(5)
        tls = not self._starttls
        if tls:
            conn = self._ctx.wrap_socket(conn, server_side=True)
        puffer = b""

        def zeile() -> bytes:
            nonlocal puffer
            while b"\r\n" not in puffer:
                stueck = conn.recv(4096)
                if not stueck:
                    return b""
                puffer += stueck
            z, _, puffer = puffer.partition(b"\r\n")
            return z

        conn.sendall(b"* OK Mini-IMAP bereit\r\n")
        while True:
            z = zeile()
            if not z:
                return
            tag, _, rest = z.decode("ascii", "replace").partition(" ")
            befehl = rest.split(" ", 1)[0].upper()
            if befehl == "CAPABILITY":
                caps = "IMAP4rev1"
                if self._starttls and self._angeboten and not tls:
                    caps += " STARTTLS"
                conn.sendall(f"* CAPABILITY {caps}\r\n{tag} OK CAPABILITY fertig\r\n".encode())
            elif befehl == "STARTTLS":
                conn.sendall(f"{tag} OK TLS beginnt\r\n".encode())
                conn = self._ctx.wrap_socket(conn, server_side=True)
                tls = True
            elif befehl == "LOGIN":
                self.login_gesehen = True
                conn.sendall(f"{tag} OK angemeldet\r\n".encode())
            elif befehl == "LOGOUT":
                conn.sendall(f"* BYE tschuess\r\n{tag} OK LOGOUT fertig\r\n".encode())
                return
            else:
                conn.sendall(f"{tag} OK\r\n".encode())


@pytest.fixture()
def server(monkeypatch, pki):
    """Fabrik fuer Mini-Server. Pinnt jeden Host auf 127.0.0.1 und vertraut der
    Test-CA zusaetzlich zu den System-CAs - die strikten Einstellungen von
    _standard_kontext (CERT_REQUIRED, Hostname) bleiben dabei unveraendert."""
    monkeypatch.setattr(imap_mod, "resolve_pinned_host", lambda host, port: "127.0.0.1")
    original = imap_mod._standard_kontext

    def _mit_test_ca() -> ssl.SSLContext:
        ctx = original()
        ctx.load_verify_locations(cadata=pki.ca_pem)
        return ctx

    monkeypatch.setattr(imap_mod, "_standard_kontext", _mit_test_ca)
    gestartet: list[_MiniImap] = []

    def _starten(zert, **kw) -> _MiniImap:
        s = _MiniImap(zert, **kw)
        gestartet.append(s)
        return s

    yield _starten
    for s in gestartet:
        s.stop()


def _konto(port: int, *, host: str = HOST, starttls: bool = False, fingerprint: str = "") -> MailAccount:
    return MailAccount(
        id=None, user_id=1, email="ich@mail.test", imap_host=host, imap_port=port,
        imap_ssl=not starttls, imap_tls_fingerprint=fingerprint,
        smtp_host=host, smtp_port=587, secret_enc="x",
    )


def _verbinden_und_anmelden(konto: MailAccount) -> None:
    box = imap_mod._neue_mailbox(konto)
    try:
        box.client.login("ich", "geheim")
    finally:
        box.client.shutdown()


# --- Standardmodus: strikte Pruefung ----------------------------------------------

def test_standardkontext_ist_strikt():
    ctx = imap_mod._standard_kontext()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_selbstsigniertes_zertifikat_wird_abgelehnt_und_kein_login(server, pki):
    """Der eigentliche Befund: vor dem Fix ging das durch und LOGIN kam an."""
    s = server(pki.selbst)
    with pytest.raises(ssl.SSLCertVerificationError):
        _verbinden_und_anmelden(_konto(s.port))
    assert s.login_gesehen is False


def test_ca_signiertes_zertifikat_mit_passendem_namen_klappt(server, pki):
    s = server(pki.signiert)
    _verbinden_und_anmelden(_konto(s.port))
    assert s.login_gesehen is True


def test_falscher_hostname_wird_abgelehnt(server, pki):
    """Zertifikat gilt fuer mail.test, das Konto spricht anderer.test an."""
    s = server(pki.signiert)
    with pytest.raises(ssl.SSLCertVerificationError):
        _verbinden_und_anmelden(_konto(s.port, host="anderer.test"))
    assert s.login_gesehen is False


# --- Fingerprint-Pinning ----------------------------------------------------------

def test_passender_fingerprint_erlaubt_selbstsigniertes_zertifikat(server, pki):
    s = server(pki.selbst)
    _verbinden_und_anmelden(_konto(s.port, fingerprint=pki.selbst.fingerprint))
    assert s.login_gesehen is True


def test_falscher_fingerprint_wird_abgelehnt_und_kein_login(server, pki):
    s = server(pki.selbst)
    with pytest.raises(imap_mod.TlsZertifikatFehler):
        _verbinden_und_anmelden(_konto(s.port, fingerprint="ab" * 32))
    assert s.login_gesehen is False


def test_pin_ist_exklusiv_auch_gegen_ca_signierte_zertifikate(server, pki):
    """Ist ein Fingerprint gesetzt, zaehlt NUR er - ein anderes, CA-gueltiges
    Zertifikat (z. B. eines Angreifers mit eigener Domain-Validierung) nicht."""
    s = server(pki.signiert)
    with pytest.raises(imap_mod.TlsZertifikatFehler):
        _verbinden_und_anmelden(_konto(s.port, fingerprint=pki.selbst.fingerprint))
    assert s.login_gesehen is False


def test_kaputter_gespeicherter_fingerprint_heisst_nicht_alles_erlauben(server, pki):
    s = server(pki.selbst)
    with pytest.raises(imap_mod.TlsZertifikatFehler):
        _verbinden_und_anmelden(_konto(s.port, fingerprint="kein-fingerprint"))
    assert s.login_gesehen is False


# --- STARTTLS ---------------------------------------------------------------------

def test_starttls_selbstsigniert_wird_abgelehnt_und_kein_login(server, pki):
    s = server(pki.selbst, starttls=True)
    with pytest.raises(ssl.SSLCertVerificationError):
        _verbinden_und_anmelden(_konto(s.port, starttls=True))
    assert s.login_gesehen is False


def test_starttls_ca_signiert_klappt(server, pki):
    s = server(pki.signiert, starttls=True)
    _verbinden_und_anmelden(_konto(s.port, starttls=True))
    assert s.login_gesehen is True


def test_starttls_mit_passendem_fingerprint_klappt(server, pki):
    s = server(pki.selbst, starttls=True)
    _verbinden_und_anmelden(_konto(s.port, starttls=True, fingerprint=pki.selbst.fingerprint))
    assert s.login_gesehen is True


def test_starttls_mit_falschem_fingerprint_wird_abgelehnt(server, pki):
    s = server(pki.selbst, starttls=True)
    with pytest.raises(imap_mod.TlsZertifikatFehler):
        _verbinden_und_anmelden(_konto(s.port, starttls=True, fingerprint="cd" * 32))
    assert s.login_gesehen is False


def test_ohne_starttls_angebot_kein_rueckfall_auf_klartext(server, pki):
    s = server(pki.signiert, starttls=True, starttls_angeboten=False)
    with pytest.raises(imaplib.IMAP4.error):
        _verbinden_und_anmelden(_konto(s.port, starttls=True))
    assert s.login_gesehen is False


# --- Zertifikat abrufen (ohne Login) ----------------------------------------------

def test_zertifikat_abrufen_selbstsigniert(server, pki):
    s = server(pki.selbst)
    info = imap_mod.zertifikat_abrufen(_konto(s.port))
    assert info["fingerprint"] == pki.selbst.fingerprint
    assert info["ca_valid"] is False and info["ca_error"]
    assert "mail.test" in info["subject"]
    assert s.login_gesehen is False


def test_zertifikat_abrufen_ca_signiert_ueber_starttls(server, pki):
    s = server(pki.signiert, starttls=True)
    info = imap_mod.zertifikat_abrufen(_konto(s.port, starttls=True))
    assert info["fingerprint"] == pki.signiert.fingerprint
    assert info["ca_valid"] is True and info["ca_error"] == ""
    assert s.login_gesehen is False


# --- Migration/Transfer -----------------------------------------------------------

def test_migration_nutzt_gepinnte_gepruefte_mailbox(monkeypatch):
    """migrate._open baute frueher eine nackte MailBox per Hostnamen."""
    gesehen: list = []

    class _Box:
        client = SimpleNamespace(sock=SimpleNamespace(settimeout=lambda t: None))

        def login(self, user, pw, initial_folder="INBOX"):
            gesehen.append((user, initial_folder))

    monkeypatch.setattr(imap_mod, "_neue_mailbox", lambda acc: gesehen.append(acc) or _Box())
    konto = _konto(993)
    migrate_mod._open(konto, "geheim", folder="Archiv")
    assert gesehen == [konto, ("ich@mail.test", "Archiv")]


# --- Normalisierung + API ---------------------------------------------------------

def test_fingerprint_normalisierung():
    roh = ":".join(["AB"] * 32)
    assert normalize_fingerprint(roh) == "ab" * 32
    assert normalize_fingerprint("sha256 Fingerprint=" + roh) == "ab" * 32
    assert normalize_fingerprint("  ") == ""
    with pytest.raises(ValueError):
        normalize_fingerprint("ab" * 31)
    with pytest.raises(ValueError):
        normalize_fingerprint("zz" * 32)


def test_api_speichert_fingerprint_normalisiert_und_lehnt_unsinn_ab(client, admin, account):
    roh = ":".join(["EF"] * 32)
    r = client.patch(f"/api/v1/accounts/{account}", headers=admin, json={"imap_tls_fingerprint": roh})
    assert r.status_code == 200, r.text
    assert r.json()["imap_tls_fingerprint"] == "ef" * 32

    r = client.patch(f"/api/v1/accounts/{account}", headers=admin, json={"imap_tls_fingerprint": "falsch"})
    assert r.status_code == 422

    r = client.patch(f"/api/v1/accounts/{account}", headers=admin, json={"imap_tls_fingerprint": ""})
    assert r.status_code == 200, r.text
    assert r.json()["imap_tls_fingerprint"] == ""


def test_api_zertifikat_abruf_nur_fuer_eigene_konten(client, admin):
    r = client.post("/api/v1/accounts/999999/tls-certificate", headers=admin)
    assert r.status_code == 404
