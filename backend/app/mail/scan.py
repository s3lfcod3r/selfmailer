"""Virenscan (ClamAV) und Spamfilter (rspamd) fuer eingehende Mails (seit 1.97.3).

Beides ist doppelt abschaltbar: der Admin stellt den Dienst bereit
(SELFMAILER_CLAMAV / SELFMAILER_RSPAMD_URL), jeder Nutzer schaltet ihn fuer sich
unter Einstellungen -> Sicherheit ein und aus (sec_virus_scan / sec_spam_filter).

Grundsaetze:
- Nie "sauber" behaupten, wenn nicht geprueft wurde: Fehler, Zeitueberschreitung
  oder zu grosse Anhaenge ergeben "error"/"skipped", nicht "clean".
- Nie fatal: ein nicht erreichbarer Scanner haelt weder Sync noch Push auf.
- Nichts verlaesst das Heimnetz: Anhaenge und Mails gehen nur an die vom Admin
  eingetragenen Dienste.
"""
from __future__ import annotations

import logging
import re
import socket
import struct
from dataclasses import dataclass

import httpx
from imap_tools import Header
from sqlalchemy import delete
from sqlmodel import Session, select

from ..core.config import get_settings
from ..core.db import engine
from ..models import AttachmentScan, MailAccount
from . import imap as imap_mod

logger = logging.getLogger(__name__)

# clamd: groesster Anhang, der gescannt wird (clamd-Standard StreamMaxLength 25 MB).
_CLAM_MAX = 25 * 1024 * 1024
_CLAM_CHUNK = 64 * 1024
_CLAM_TIMEOUT = 30.0
# Neue Mails je Lauf, die vollstaendig geholt und geprueft werden.
_INSPECT_MAX = 20
# Groesste Mail, die beim Eintreffen geprueft wird (Rohgroesse).
_INSPECT_MAX_BYTES = 30 * 1024 * 1024
_RSPAMD_TIMEOUT = 20.0


# --- ClamAV ---------------------------------------------------------------------

def _clam_addr() -> tuple[str, int] | None:
    raw = (get_settings().clamav or "").strip()
    if not raw:
        return None
    host, _, port = raw.rpartition(":")
    if not host:
        host, port = raw, "3310"
    try:
        return host.strip("[]"), int(port)
    except ValueError:
        return None


def clamav_configured() -> bool:
    return _clam_addr() is not None


def _clam_cmd(sock: socket.socket) -> str:
    buf = b""
    while not buf.endswith(b"\0"):
        teil = sock.recv(4096)
        if not teil:
            break
        buf += teil
    return buf.rstrip(b"\0").decode("utf-8", "replace").strip()


def clamav_version() -> str | None:
    """Version, wenn clamd antwortet - sonst None (fuer die Statusanzeige)."""
    addr = _clam_addr()
    if addr is None:
        return None
    try:
        with socket.create_connection(addr, timeout=3.0) as s:
            s.sendall(b"zVERSION\0")
            return _clam_cmd(s) or None
    except OSError:
        return None


def scan_bytes(data: bytes) -> tuple[str, str]:
    """(status, signatur): clean | infected | error | skipped (zu gross)."""
    addr = _clam_addr()
    if addr is None:
        return "error", "ClamAV nicht eingerichtet"
    if len(data) > _CLAM_MAX:
        return "skipped", "zu gross fuer den Virenscan"
    try:
        with socket.create_connection(addr, timeout=_CLAM_TIMEOUT) as s:
            s.sendall(b"zINSTREAM\0")
            for i in range(0, len(data), _CLAM_CHUNK):
                teil = data[i:i + _CLAM_CHUNK]
                s.sendall(struct.pack("!L", len(teil)) + teil)
            s.sendall(struct.pack("!L", 0))
            antwort = _clam_cmd(s)
    except OSError as exc:
        return "error", f"ClamAV nicht erreichbar ({type(exc).__name__})"
    # "stream: OK" | "stream: Eicar-Signature FOUND" | "... ERROR"
    if antwort.endswith("OK"):
        return "clean", ""
    if antwort.endswith("FOUND"):
        return "infected", antwort.split(":", 1)[-1].removesuffix("FOUND").strip()[:200]
    return "error", antwort[:200] or "keine Antwort"


# --- Befunde ---------------------------------------------------------------------

def mail_key(detail_or_msg: dict, folder: str = "", uid: str = "") -> str:
    mid = str(detail_or_msg.get("message_id") or "").strip()
    if mid:
        return mid[:500]
    return f"uid:{folder or detail_or_msg.get('folder', '')}:{detail_or_msg.get('uidvalidity', 0)}:{uid or detail_or_msg.get('uid', '')}"


def record(account_id: int, key: str, ergebnisse: list[tuple[int, str, str, str]]) -> None:
    """Befunde einer Mail ersetzen: [(index, dateiname, status, signatur)]."""
    with Session(engine) as s:
        s.execute(delete(AttachmentScan).where(AttachmentScan.account_id == account_id, AttachmentScan.mail_key == key))
        for idx, name, status, sig in ergebnisse:
            s.add(AttachmentScan(account_id=account_id, mail_key=key, part_index=idx,
                                 filename=(name or "")[:300], status=status, signature=sig))
        s.commit()


def record_one(account_id: int, key: str, index: int, filename: str, status: str, sig: str) -> None:
    """Befund EINES Anhangs ersetzen (Scan beim Download)."""
    with Session(engine) as s:
        s.execute(delete(AttachmentScan).where(
            AttachmentScan.account_id == account_id, AttachmentScan.mail_key == key,
            AttachmentScan.part_index == index,
        ))
        s.add(AttachmentScan(account_id=account_id, mail_key=key, part_index=index,
                             filename=(filename or "")[:300], status=status, signature=sig))
        s.commit()


def findings_for(account_id: int, detail: dict) -> list[dict]:
    """Auffaellige Befunde (alles ausser clean) fuer die Mail-Ansicht."""
    key = mail_key(detail)
    with Session(engine) as s:
        rows = s.exec(select(AttachmentScan).where(
            AttachmentScan.account_id == account_id, AttachmentScan.mail_key == key,
            AttachmentScan.status != "clean",
        ).order_by(AttachmentScan.part_index)).all()
    return [{"index": r.part_index, "filename": r.filename, "status": r.status, "signature": r.signature} for r in rows]


# --- rspamd ----------------------------------------------------------------------

def rspamd_configured() -> bool:
    return bool((get_settings().rspamd_url or "").strip())


def rspamd_learn_configured() -> bool:
    st = get_settings()
    return bool((st.rspamd_controller_url or "").strip() and (st.rspamd_password or "").strip())


def rspamd_ping() -> bool:
    url = (get_settings().rspamd_url or "").strip().rstrip("/")
    if not url:
        return False
    try:
        return httpx.get(f"{url}/ping", timeout=3.0).status_code == 200
    except httpx.HTTPError:
        return False


@dataclass
class SpamUrteil:
    is_spam: bool
    score: float = 0.0
    required: float = 0.0
    action: str = ""


def rspamd_check(raw: bytes) -> SpamUrteil | None:
    """Mail bewerten (/checkv2). None = nicht bewertet (Fehler/nicht eingerichtet)."""
    url = (get_settings().rspamd_url or "").strip().rstrip("/")
    if not url:
        return None
    try:
        r = httpx.post(f"{url}/checkv2", content=raw, timeout=_RSPAMD_TIMEOUT)
        r.raise_for_status()
        j = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("rspamd-Bewertung fehlgeschlagen: %s", type(exc).__name__)
        return None
    action = str(j.get("action") or "")
    # rspamd entscheidet selbst: "add header"/"rewrite subject"/"reject" = Spam.
    # "greylist"/"soft reject" sind Zustell-Verzoegerungen, kein Urteil.
    return SpamUrteil(
        is_spam=action in ("add header", "rewrite subject", "reject"),
        score=float(j.get("score") or 0.0), required=float(j.get("required_score") or 0.0), action=action,
    )


def rspamd_learn(raw: bytes, spam: bool) -> bool:
    st = get_settings()
    base = (st.rspamd_controller_url or "").strip().rstrip("/")
    if not base or not st.rspamd_password:
        return False
    try:
        r = httpx.post(f"{base}/{'learnspam' if spam else 'learnham'}", content=raw,
                       headers={"Password": st.rspamd_password}, timeout=_RSPAMD_TIMEOUT)
        # 208 = schon gelernt - auch das ist "erledigt".
        return r.status_code in (200, 208)
    except httpx.HTTPError as exc:
        logger.info("rspamd-Lernen fehlgeschlagen: %s", type(exc).__name__)
        return False


# --- Pruefung beim Eintreffen -------------------------------------------------------

def inspect_new(acc: MailAccount, password: str, folder: str, uids: list[str], *,
                virus: bool, spam: bool) -> dict:
    """Neu eingetroffene Mails pruefen; Virus oder Spam -> in den Spam-Ordner.

    Liefert {"moved": [uids], "moved_unseen": n, "virus": n, "spam": n}. Holt die
    Mails in EINER Verbindung vollstaendig (BODY.PEEK - nichts wird gelesen
    markiert), hoechstens _INSPECT_MAX je Lauf und nur bis _INSPECT_MAX_BYTES.
    """
    leer = {"moved": [], "moved_unseen": 0, "virus": 0, "spam": 0}
    virus = virus and clamav_configured()
    spam = spam and rspamd_configured()
    if not uids or not (virus or spam) or acc.id is None:
        return leer
    kandidaten = list(dict.fromkeys(uids))[-_INSPECT_MAX:]
    verschieben: list[str] = []
    ungelesen = 0
    n_virus = n_spam = 0
    with imap_mod._mailbox(acc, password, folder=folder, lock_timeout=10.0, op="pruefen") as box:
        koepfe = box.fetch(imap_mod.AND(uid=",".join(kandidaten)), uid_list=kandidaten,
                           headers_only=True, mark_seen=False, bulk=True)
        passend = [m.uid for m in koepfe if m.uid and 0 < m.size_rfc822 <= _INSPECT_MAX_BYTES]
        if not passend:
            return leer
        for msg in box.fetch(imap_mod.AND(uid=",".join(passend)), uid_list=passend, mark_seen=False, bulk=5):
            if not msg.uid:
                continue
            treffer = False
            if virus and msg.attachments:
                ergebnisse = []
                for i, att in enumerate(msg.attachments):
                    status, sig = scan_bytes(att.payload or b"")
                    ergebnisse.append((i, att.filename or "", status, sig))
                    if status == "infected":
                        treffer = True
                key = mail_key({"message_id": msg.obj.get("Message-ID", "") or ""}, folder, msg.uid)
                record(acc.id, key, ergebnisse)
                if treffer:
                    n_virus += 1
                    logger.warning("Virus in Anhang gefunden (account_id=%s): %s", acc.id,
                                   ", ".join(sig for _i, _n, st, sig in ergebnisse if st == "infected"))
            if spam and not treffer:
                try:
                    roh = msg.obj.as_bytes()
                except Exception:  # noqa: BLE001
                    roh = b""
                urteil = rspamd_check(roh) if roh else None
                if urteil is not None and urteil.is_spam:
                    treffer = True
                    n_spam += 1
            if treffer:
                verschieben.append(msg.uid)
                if imap_mod.SEEN not in msg.flags:
                    ungelesen += 1
        if verschieben:
            ziel = imap_mod._spam_folder(box)
            if ziel and ziel != folder:
                box.move(verschieben, ziel)   # LIST aendert die Auswahl nicht
            else:
                logger.info("Kein Spam-Ordner gefunden - verdaechtige Mails bleiben liegen (account_id=%s)", acc.id)
                verschieben, ungelesen = [], 0
    return {"moved": verschieben, "moved_unseen": ungelesen, "virus": n_virus, "spam": n_spam}


_MID_RE = re.compile(r'<[^<>"\\\s]{1,400}>')


def learn_after_move(acc: MailAccount, password: str, message_ids: list[str], source: str, dest: str) -> int:
    """Spam-Knopf als Lernsignal fuer rspamd (im Hintergrund aufrufen).

    In den Spam-Ordner verschoben -> "ist Spam"; aus dem Spam-Ordner zurueck (nicht
    in den Papierkorb) -> "kein Spam". Die Mail wird im ZIELordner ueber ihre
    Message-ID gefunden - die UID aendert sich beim Verschieben. Liefert die Zahl
    gelernter Mails."""
    mids = [m for m in dict.fromkeys(message_ids) if m and _MID_RE.fullmatch(m)][:50]
    if not mids or not rspamd_learn_configured() or acc.id is None:
        return 0
    gelernt = 0
    with imap_mod._mailbox(acc, password, folder=dest, op="spam-lernen") as box:
        spam_ordner = imap_mod._spam_folder(box)
        if not spam_ordner:
            return 0
        if dest == spam_ordner:
            als_spam = True
        elif source == spam_ordner and imap_mod._trash_folder(box, dest) != dest:
            als_spam = False
        else:
            return 0
        for mid in mids:
            for msg in box.fetch(imap_mod.AND(header=Header("Message-ID", mid)), mark_seen=False, limit=1):
                try:
                    if rspamd_learn(msg.obj.as_bytes(), als_spam):
                        gelernt += 1
                except Exception:  # noqa: BLE001 - eine Mail darf den Rest nicht kippen
                    pass
    logger.info("rspamd gelernt: %d Mail(s) als %s (account_id=%s)", gelernt, "Spam" if als_spam else "kein Spam", acc.id)
    return gelernt


def status() -> dict:
    """Fuer Einstellungen -> Sicherheit: ist der jeweilige Dienst da?"""
    version = clamav_version() if clamav_configured() else None
    return {
        "clamav": {"configured": clamav_configured(), "reachable": version is not None, "version": version or ""},
        "rspamd": {"configured": rspamd_configured(), "reachable": rspamd_ping() if rspamd_configured() else False,
                   "learning": rspamd_learn_configured()},
    }
