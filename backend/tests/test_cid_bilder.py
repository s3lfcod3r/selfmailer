"""Eingebettete Bilder (cid:) werden fuer das Lese-iframe als data:-URI eingesetzt.

Anlass: DHL-Auftragsbestaetigung (multipart/related) zeigte in SelfMailer nur
kaputte Bildsymbole und listete jedes Logo als Anhang "inline".
"""
from __future__ import annotations

import base64
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from types import SimpleNamespace

from imap_tools import MailMessage

from app.mail import cache as cache_mod
from app.mail import imap as imap_mod

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
ACCOUNT = SimpleNamespace(email="ich@example.org", auth_user="")


def _bild(cid: str, data: bytes = PNG, subtype: str = "png") -> MIMEImage:
    part = MIMEImage(data, _subtype=subtype)
    part.add_header("Content-ID", f"<{cid}>")
    part.add_header("Content-Disposition", "inline")
    return part


def _mail(html: str, *teile) -> MailMessage:
    related = MIMEMultipart("related")
    related.attach(MIMEText(html, "html", "utf-8"))
    for t in teile:
        related.attach(t)
    root = MIMEMultipart("mixed")
    root.attach(related)
    pdf = MIMEApplication(b"%PDF-1.4 agb", _subtype="pdf")
    pdf.add_header("Content-Disposition", "attachment", filename="agb.pdf")
    root.attach(pdf)
    root["Subject"] = "Auftragsbestaetigung"
    root["From"] = "noreply@dhl.com"
    return MailMessage.from_bytes(root.as_bytes())


def test_cid_bild_wird_als_data_uri_eingesetzt_und_aus_anhangliste_genommen():
    msg = _mail('<p><img src="cid:logo@dhl"> Hallo</p>', _bild("logo@dhl"))
    d = imap_mod._detail_dict(msg, ACCOUNT)
    assert "cid:" not in d["html"]
    assert 'src="data:image/png;base64,' + base64.b64encode(PNG).decode() + '"' in d["html"]
    # Nur noch das echte PDF ist sichtbar, mit seinem ORIGINAL-Index fuer den Download.
    assert [a["filename"] for a in d["attachments"]] == ["agb.pdf"]
    pdf_index = d["attachments"][0]["index"]
    assert list(msg.attachments)[pdf_index].filename == "agb.pdf"
    assert d["cid_inline"] == 1


def test_cid_vergleich_ignoriert_gross_klein_und_url_kodierung():
    msg = _mail('<img src="CID:Logo%40DHL"><td style="background:url(cid:logo@dhl)">', _bild("logo@dhl"))
    d = imap_mod._detail_dict(msg, ACCOUNT)
    assert "cid:" not in d["html"].lower()
    assert d["html"].count("data:image/png;base64,") == 2


def test_unbekannte_cid_bleibt_stehen_und_bild_bleibt_anhang():
    msg = _mail('<img src="cid:fehlt">', _bild("anderes"))
    d = imap_mod._detail_dict(msg, ACCOUNT)
    assert 'src="cid:fehlt"' in d["html"]
    assert len(d["attachments"]) == 2   # nicht eingesetztes Bild bleibt herunterladbar


def test_svg_wird_nicht_eingesetzt():
    msg = _mail('<img src="cid:s">', _bild("s", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "svg+xml"))
    d = imap_mod._detail_dict(msg, ACCOUNT)
    assert 'src="cid:s"' in d["html"]


def test_zu_grosses_bild_wird_nicht_eingesetzt(monkeypatch):
    monkeypatch.setattr(imap_mod, "CID_MAX_PART", len(PNG) - 1)
    msg = _mail('<img src="cid:gross">', _bild("gross"))
    d = imap_mod._detail_dict(msg, ACCOUNT)
    assert 'src="cid:gross"' in d["html"]


def test_gesamtbudget_begrenzt_eingesetzte_bilder(monkeypatch):
    monkeypatch.setattr(imap_mod, "CID_MAX_TOTAL", len(PNG) + 1)
    msg = _mail('<img src="cid:a"><img src="cid:b">', _bild("a"), _bild("b"))
    d = imap_mod._detail_dict(msg, ACCOUNT)
    assert d["html"].count("data:image/png") == 1
    assert 'src="cid:b"' in d["html"]


def test_mail_ohne_cid_bleibt_unveraendert():
    html = '<p>Hallo <a href="https://x.example">Link</a></p>'
    assert imap_mod.inline_cid_images(html, []) == (html, set())


def test_alter_cache_mit_rohen_cid_verweisen_gilt_als_veraltet():
    basis = {"list_unsubscribe": ""}
    assert cache_mod.detail_veraltet({**basis, "html": '<img src="cid:x">'}) is True
    assert cache_mod.detail_veraltet({**basis, "html": '<img src="cid:x">', "cid_inline": 1}) is False
    assert cache_mod.detail_veraltet({**basis, "html": "<p>ohne Bilder</p>"}) is False
