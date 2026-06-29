"""IMAP-Zugriff via imap-tools (synchron; FastAPI faehrt sync-Endpunkte im
Threadpool). Verbindungen sind kurzlebig: oeffnen, lesen, schliessen.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timedelta
from email.message import EmailMessage

from imap_tools import MailBox, AND

from ..models import MailAccount

logger = logging.getLogger(__name__)

# IMAP-System-Flags (Backslash literal -> Raw-Strings).
SEEN = r"\Seen"
FLAGGED = r"\Flagged"

# --- Verbindungs-Pool -------------------------------------------------------
# IMAP-LOGIN (TCP+TLS+AUTH) kostet je Provider 0,5-3 s. Frueher wurde pro
# Aktion neu eingeloggt -> Ordnerwechsel/Mail-Oeffnen fuehlten sich zaeh an.
# Jetzt: pro Konto EINE Verbindung offen halten und wiederverwenden.
#
# imap_tools/imaplib sind NICHT thread-safe -> ein Lock je Konto serialisiert
# die Nutzung (ein Konto kann ohnehin nur eine IMAP-Operation gleichzeitig).
# Nach Leerlauf wird geschlossen; vor Wiederverwendung per NOOP geprueft und bei
# Bedarf neu verbunden. Per Env SELFMAILER_IMAP_POOL=0 komplett abschaltbar.
_POOL_ENABLED = os.getenv("SELFMAILER_IMAP_POOL", "1").strip().lower() not in {"0", "false", "no"}
_IDLE_TTL = 240.0  # Sekunden Leerlauf, danach Verbindung schliessen
# Socket-Timeout je IMAP-Operation. OHNE das blockiert ein totes/langsames
# Postfach (z. B. ein Provider, der die Verbindung still fallen laesst) den
# Worker-Thread UNENDLICH -> "alles haengt". Mit Timeout schlaegt es sauber fehl.
_IMAP_TIMEOUT = float(os.getenv("SELFMAILER_IMAP_TIMEOUT", "15") or 15)
_POOL: dict[str, "_PooledBox"] = {}
_POOL_LOCK = threading.Lock()


class _PooledBox:
    __slots__ = ("box", "lock", "last_used", "folder")

    def __init__(self) -> None:
        self.box: MailBox | None = None
        self.lock = threading.RLock()
        self.last_used = 0.0
        self.folder: str | None = None


def _pool_key(account: MailAccount, login: str) -> str:
    return f"{account.id}:{login}@{account.imap_host}:{account.imap_port}"


def _connect(account: MailAccount, login: str, password: str, folder: str) -> MailBox:
    # timeout bei der Konstruktion bindet schon den TCP-Connect; faellt die
    # imap_tools-Version ohne timeout-Param zurueck, setzen wir es danach am Socket.
    try:
        box = MailBox(account.imap_host, port=account.imap_port, timeout=_IMAP_TIMEOUT)
    except TypeError:
        box = MailBox(account.imap_host, port=account.imap_port)
    box.login(login, password, initial_folder=folder)
    try:
        box.client.sock.settimeout(_IMAP_TIMEOUT)
    except Exception:  # noqa: BLE001 - best effort, falls Socket anders heisst
        pass
    return box


def _close(box: MailBox | None) -> None:
    if box is None:
        return
    try:
        box.logout()
    except Exception:  # pragma: no cover - best effort
        pass


def _reap_idle() -> None:
    """Schliesst Verbindungen, die laenger als _IDLE_TTL ungenutzt sind.

    Nur Eintraege, deren Lock gerade frei ist (non-blocking), damit das Aufraeumen
    nie eine laufende Operation stoert."""
    now = time.monotonic()
    with _POOL_LOCK:
        keys = list(_POOL)
    for key in keys:
        entry = _POOL.get(key)
        if entry is None or now - entry.last_used <= _IDLE_TTL:
            continue
        if entry.lock.acquire(blocking=False):
            try:
                if entry.box is not None and now - entry.last_used > _IDLE_TTL:
                    _close(entry.box)
                    entry.box = None
                    entry.folder = None
            finally:
                entry.lock.release()


def _ensure_box(entry: _PooledBox, account: MailAccount, login: str, password: str, folder: str) -> MailBox:
    box = entry.box
    if box is not None:
        if time.monotonic() - entry.last_used > _IDLE_TTL:
            _close(box)
            box = entry.box = None
        else:
            try:
                box.client.noop()  # lebt die Verbindung noch?
            except Exception:  # noqa: BLE001 - tote Verbindung -> neu aufbauen
                _close(box)
                box = entry.box = None
    if box is None:
        box = _connect(account, login, password, folder)
        entry.box = box
        entry.folder = folder
        return box
    if folder and entry.folder != folder:
        box.folder.set(folder)
        entry.folder = folder
    return box


@contextmanager
def _mailbox(account: MailAccount, password: str, folder: str = "INBOX") -> Iterator[MailBox]:
    login = account.auth_user or account.email

    # Pool aus (oder Konto ohne id) -> altes Verhalten: oeffnen, nutzen, schliessen.
    if not _POOL_ENABLED or account.id is None:
        box = _connect(account, login, password, folder)
        try:
            yield box
        finally:
            _close(box)
        return

    key = _pool_key(account, login)
    with _POOL_LOCK:
        entry = _POOL.get(key)
        if entry is None:
            entry = _POOL[key] = _PooledBox()

    entry.lock.acquire()
    try:
        # _ensure_box MUSS im try stehen: scheitert es, nachdem entry.box gesetzt
        # wurde (z. B. folder.set wirft), liefe sonst kein Cleanup.
        box = _ensure_box(entry, account, login, password, folder)
        yield box
        entry.last_used = time.monotonic()
    except Exception:
        # Verbindung koennte nach einem Fehler in unklarem Zustand sein -> verwerfen.
        _close(entry.box)
        entry.box = None
        entry.folder = None
        raise
    finally:
        entry.lock.release()
        _reap_idle()


def list_folders(account: MailAccount, password: str) -> list[str]:
    """Listet Ordner robust: viele Server (z. B. web.de/Courier) zeigen INBOX-
    Unterordner nicht beim einfachen LIST "" "*". Daher mehrere Strategien
    kombinieren und deduplizieren.
    """
    seen: dict[str, None] = {}
    with _mailbox(account, password) as box:
        attempts = [
            lambda: box.folder.list("", "*"),            # alles ab Root
            lambda: box.folder.list("INBOX", "*"),        # INBOX-Unterordner explizit
            lambda: box.folder.list("", "*", subscribed_only=True),  # abonnierte
        ]
        for attempt in attempts:
            try:
                for f in attempt():
                    if f.name:
                        seen.setdefault(f.name, None)
            except Exception:  # noqa: BLE001 - einzelne LIST-Variante darf scheitern
                continue
    names = list(seen) or ["INBOX"]
    # INBOX immer zuerst, Rest alphabetisch (case-insensitiv).
    names.sort(key=lambda n: (n.upper() != "INBOX", n.lower()))
    return names


def list_uids(account: MailAccount, password: str, folder: str = "INBOX") -> list[str]:
    """Alle UIDs eines Ordners (neueste zuerst). Live-Fallback fuer "Alle
    auswaehlen", wenn der Cache nicht greift. Nur UIDs, kein Body."""
    with _mailbox(account, password, folder=folder) as box:
        return list(reversed([u for u in box.uids() if u]))


def folder_counts(account: MailAccount, password: str) -> list[dict]:
    """Wie list_folders, aber mit Ungelesen-/Gesamt-Zaehlern je Ordner (IMAP STATUS).

    Pro Ordner ein STATUS-Aufruf; bei sehr vielen Ordnern entsprechend langsamer.
    Fehler bei einzelnen Ordnern werden geschluckt (Zaehler dann 0).
    """
    seen: dict[str, None] = {}
    # Flags je Ordnername fuer die SPECIAL-USE-Erkennung. Beim Dedup die ZUERST
    # gesehenen Flags behalten (spaetere LIST-Varianten ueberschreiben nicht).
    flags_by_name: dict[str, tuple] = {}
    out: list[dict] = []
    with _mailbox(account, password) as box:
        attempts = [
            lambda: box.folder.list("", "*"),
            lambda: box.folder.list("INBOX", "*"),
            lambda: box.folder.list("", "*", subscribed_only=True),
        ]
        for attempt in attempts:
            try:
                for f in attempt():
                    if f.name:
                        seen.setdefault(f.name, None)
                        if f.name not in flags_by_name:
                            flags_by_name[f.name] = tuple(getattr(f, "flags", ()) or ())
            except Exception:  # noqa: BLE001
                continue
        names = list(seen) or ["INBOX"]
        names.sort(key=lambda n: (n.upper() != "INBOX", n.lower()))
        for name in names:
            flags = flags_by_name.get(name, ())
            special = _folder_special(name, flags)
            unseen = total = 0
            # \Noselect-Ordner (z. B. Gmail-Container "[Gmail]") sind nicht
            # selektierbar -> STATUS scheitert; daher ueberspringen, Zaehler 0.
            if special != "noselect":
                try:
                    st = box.folder.status(name, ["MESSAGES", "UNSEEN"])
                    total = int(st.get("MESSAGES", 0) or 0)
                    unseen = int(st.get("UNSEEN", 0) or 0)
                except Exception:  # noqa: BLE001 - einzelner STATUS darf scheitern
                    pass
            out.append({"name": name, "unseen": unseen, "total": total, "special": special})
    return out


def inbox_unseen(account: MailAccount, password: str, folder: str = "INBOX") -> int:
    """Nur die Ungelesen-Zahl EINES Ordners via IMAP STATUS — schnell (1 Login,
    1 STATUS, KEIN Header-Download). Fuer die Dashboard-Uebersicht, damit der
    Live-Abruf nicht in einen vollen Sync grosser Postfaecher laeuft (Timeout)."""
    with _mailbox(account, password) as box:
        st = box.folder.status(folder, ["UNSEEN"])
        return int(st.get("UNSEEN", 0) or 0)


def set_flags_many(
    account: MailAccount, password: str, uids: list[str], folder: str = "INBOX",
    *, seen: bool | None = None, flagged: bool | None = None,
) -> int:
    """Setzt Seen/Flagged fuer VIELE UIDs in EINER IMAP-Session (statt N Logins).

    In 200er-Chunks, damit das STORE-Kommando nicht zu lang wird (manche Server
    begrenzen die Zeilenlaenge). Fuer "alles als gelesen" bei grossen Ordnern."""
    uids = [u for u in uids if u]
    if not uids or (seen is None and flagged is None):
        return 0
    with _mailbox(account, password, folder=folder) as box:
        for i in range(0, len(uids), 200):
            chunk = uids[i:i + 200]
            if seen is not None:
                box.flag(chunk, SEEN, seen)
            if flagged is not None:
                box.flag(chunk, FLAGGED, flagged)
    return len(uids)


def _snippet(text: str, html: str) -> str:
    """Kurze 1-Zeilen-Vorschau aus Text- oder HTML-Body (Tags grob entfernt)."""
    src = text or re.sub(r"<[^>]+>", " ", html)
    return " ".join(src.split())[:160]


def list_messages(
    account: MailAccount, password: str, folder: str = "INBOX", limit: int = 50, offset: int = 0
) -> list[dict]:
    out: list[dict] = []
    # bulk=True bündelt den Abruf in EINEN IMAP-FETCH statt eines pro Nachricht.
    # Auf entfernten Servern (z. B. web.de) ist die Round-Trip-Zeit der dominierende
    # Kostenfaktor: ~50 Einzel-Fetches → 1 Sammel-Fetch. headers_only bleibt aus,
    # damit Vorschau (snippet) und Anhang-Indikator erhalten bleiben.
    # offset/limit als Slice (auf die nach Datum absteigende Liste) = Paginierung
    # zum Weiterblättern bei grossen Postfaechern.
    page = slice(offset, offset + limit)
    with _mailbox(account, password, folder=folder) as box:
        for msg in box.fetch(AND(all=True), reverse=True, limit=page, mark_seen=False, bulk=True):
            out.append(
                {
                    "uid": msg.uid or "",
                    "subject": msg.subject,
                    "from": msg.from_,
                    "date": msg.date_str,
                    "seen": SEEN in msg.flags,
                    "flagged": FLAGGED in msg.flags,
                    "snippet": _snippet(msg.text or "", msg.html or ""),
                    "has_attachments": bool(msg.attachments),
                }
            )
    return out


_AUTH_RE = r"{m}\s*=\s*(pass|fail|softfail|hardfail|neutral|none|temperror|permerror|bestguesspass)"


def _analyze_auth(msg, account: MailAccount) -> dict:
    """Echtheits-Check aus den Authentifizierungs-Headern, die der EMPFANGS-Server
    setzt (Authentication-Results / Received-SPF). Erkennt Spoofing — speziell den
    Trick „Mail kommt angeblich von DEINER eigenen Adresse".

    verdict: pass (DMARC pass oder SPF+DKIM pass) | fail (eine Prüfung fehlgeschlagen)
    | unknown (keine Header). self_spoof: From = eigene Adresse, aber NICHT pass."""
    obj = msg.obj
    blob = " ; ".join(
        (obj.get_all("Authentication-Results") or []) + (obj.get_all("Received-SPF") or [])
    ).lower()

    def res(method: str) -> str | None:
        m = re.search(_AUTH_RE.format(m=method), blob)
        return m.group(1) if m else None

    spf, dkim, dmarc = res("spf"), res("dkim"), res("dmarc")
    from_addr = (msg.from_ or "").lower().strip()
    from_domain = from_addr.rsplit("@", 1)[-1] if "@" in from_addr else ""
    own = {a.lower() for a in (account.email or "", account.auth_user or "") if a}

    passed = dmarc == "pass" or (spf == "pass" and dkim == "pass")
    failed = dmarc in ("fail",) or spf in ("fail", "softfail", "hardfail") or dkim == "fail"
    verdict = "pass" if passed else ("fail" if failed else "unknown")

    reasons: list[str] = []
    for label, val in (("SPF", spf), ("DKIM", dkim), ("DMARC", dmarc)):
        if val:
            reasons.append(f"{label}: {val}")
    if not reasons:
        reasons.append("Keine Authentifizierungs-Header vom Server")

    return {
        "spf": spf, "dkim": dkim, "dmarc": dmarc, "verdict": verdict,
        "self_spoof": from_addr in own and verdict != "pass",
        "from_domain": from_domain, "reasons": reasons,
    }


def _detail_dict(msg, account: MailAccount) -> dict:
    """Baut das Detail-Dict (Volltext) aus einer gefetchten Nachricht."""
    attachments = [
        {
            "index": i,
            "filename": att.filename or f"anhang-{i + 1}",
            "content_type": att.content_type or "",
            "size": att.size or len(att.payload or b""),
        }
        for i, att in enumerate(msg.attachments)
    ]
    return {
        "uid": msg.uid or "",
        "subject": msg.subject,
        "from": msg.from_,
        "to": list(msg.to),
        "message_id": msg.obj.get("Message-ID", "") or "",
        "date": msg.date_str,
        "seen": SEEN in msg.flags,
        "flagged": FLAGGED in msg.flags,
        "text": msg.text or "",
        "html": msg.html or "",
        "attachments": attachments,
        "auth": _analyze_auth(msg, account),
    }


def get_raw(account: MailAccount, password: str, uid: str, folder: str = "INBOX") -> str | None:
    """Rohe RFC822-Quelle einer Mail (Header + Body) fuer „Original anzeigen"."""
    with _mailbox(account, password, folder=folder) as box:
        for msg in box.fetch(AND(uid=uid), mark_seen=False, limit=1):
            try:
                return msg.obj.as_string()
            except Exception:  # noqa: BLE001 - Fallback ueber Bytes
                return (msg.obj.as_bytes() or b"").decode("utf-8", "replace")
    return None


def get_message(account: MailAccount, password: str, uid: str, folder: str = "INBOX") -> dict | None:
    with _mailbox(account, password, folder=folder) as box:
        for msg in box.fetch(AND(uid=uid), mark_seen=False, limit=1):
            return _detail_dict(msg, account)
    return None


def get_messages(account: MailAccount, password: str, uids: list[str], folder: str = "INBOX") -> list[dict]:
    """Volltext MEHRERER Mails in EINER IMAP-Session (ein Login + ein Sammel-Fetch).

    Fuer das Vorwaermen des Body-Caches einer ganzen Listenseite, damit jeder
    Klick danach sofort aus der DB kommt.
    """
    if not uids:
        return []
    out: list[dict] = []
    with _mailbox(account, password, folder=folder) as box:
        for msg in box.fetch(AND(uid=",".join(uids)), mark_seen=False, bulk=True):
            if msg.uid:
                out.append(_detail_dict(msg, account))
    return out


def get_attachment(
    account: MailAccount, password: str, uid: str, index: int, folder: str = "INBOX"
) -> tuple[str, str, bytes] | None:
    """Liefert (filename, content_type, bytes) des Anhangs mit gegebenem Index."""
    with _mailbox(account, password, folder=folder) as box:
        for msg in box.fetch(AND(uid=uid), mark_seen=False, limit=1):
            atts = list(msg.attachments)
            if 0 <= index < len(atts):
                att = atts[index]
                return (
                    att.filename or f"anhang-{index + 1}",
                    att.content_type or "application/octet-stream",
                    att.payload or b"",
                )
    return None


def set_flags(
    account: MailAccount,
    password: str,
    uid: str,
    folder: str = "INBOX",
    *,
    seen: bool | None = None,
    flagged: bool | None = None,
) -> None:
    """Setzt/entfernt \\Seen bzw. \\Flagged fuer eine Nachricht (nur uebergebene Flags)."""
    with _mailbox(account, password, folder=folder) as box:
        if seen is not None:
            box.flag(uid, SEEN, seen)
        if flagged is not None:
            box.flag(uid, FLAGGED, flagged)


def _trash_folder(box: MailBox, current: str) -> str | None:
    """Findet den Papierkorb: erst per SPECIAL-USE-Flag \\Trash, dann per Namensheuristik."""
    names: list[str] = []
    for f in box.folder.list():
        flags = " ".join(getattr(f, "flags", ()) or ()).lower()
        if "\\trash" in flags:
            return f.name
        names.append(f.name)
    for name in names:
        low = name.lower()
        if any(k in low for k in ("trash", "papierkorb", "deleted", "geloscht", "gelöscht")):
            return name
    return None


def _spam_folder(box: MailBox) -> str | None:
    """Findet den Spam/Junk-Ordner: erst per SPECIAL-USE-Flag \\Junk, dann per
    Namensheuristik (nutzt dieselbe Erkennung wie die Ordnerzaehler)."""
    names: list[str] = []
    for f in box.folder.list():
        flags = " ".join(getattr(f, "flags", ()) or ()).lower()
        if "\\junk" in flags:
            return f.name
        names.append(f.name)
    for name in names:
        last = name.replace("/", ".").rsplit(".", 1)[-1]
        if _special_kind(last) == "spam":
            return name
    return None


def _find_spam_folder(account: MailAccount, password: str) -> str | None:
    """Spam-Ordnernamen ermitteln (eigener kurzer Kontext, damit der Verbindungs-
    Pool den selektierten Ordner sauber verwaltet)."""
    with _mailbox(account, password) as box:
        return _spam_folder(box)


def _find_trash_folder(account: MailAccount, password: str) -> str | None:
    """Papierkorb-Ordnernamen ermitteln (analog _find_spam_folder)."""
    with _mailbox(account, password) as box:
        return _trash_folder(box, "INBOX")


def _purge_folder(account: MailAccount, password: str, folder: str | None, older_than_days: int) -> dict:
    """Loescht Mails eines Ordners ENDGUELTIG (expunge).

    older_than_days: 0 = alle | N>0 = nur Mails aelter als N Tage | negativ = nichts.
    """
    if older_than_days < 0 or not folder:
        return {"deleted": 0}
    with _mailbox(account, password, folder=folder) as box:
        if older_than_days == 0:
            criteria = AND(all=True)
        else:
            cutoff = (datetime.now() - timedelta(days=older_than_days)).date()
            criteria = AND(date_lt=cutoff)
        uids = [m.uid for m in box.fetch(criteria, mark_seen=False, headers_only=True, bulk=True) if m.uid]
        if uids:
            box.delete(uids)
        return {"deleted": len(uids)}


def purge_spam(account: MailAccount, password: str, older_than_days: int) -> dict:
    """Spam/Junk-Ordner endgueltig leeren (siehe _purge_folder)."""
    if older_than_days < 0:
        return {"deleted": 0}
    return _purge_folder(account, password, _find_spam_folder(account, password), older_than_days)


def purge_trash(account: MailAccount, password: str, older_than_days: int) -> dict:
    """Papierkorb endgueltig leeren (siehe _purge_folder). So verschwinden auch die
    beim Blockieren verschobenen Mails nach Ablauf des Reue-Fensters von selbst."""
    if older_than_days < 0:
        return {"deleted": 0}
    return _purge_folder(account, password, _find_trash_folder(account, password), older_than_days)


def _soft_delete(box: MailBox, uids: list[str], current_folder: str) -> None:
    """Mails in den Papierkorb verschieben + als gelesen markieren. Ist kein
    Papierkorb da (oder wir sind schon drin), hart loeschen (expunge)."""
    if not uids:
        return
    trash = _trash_folder(box, current_folder)
    try:
        box.flag(uids, SEEN, True)  # im Papierkorb nicht als ungelesen zaehlen
    except Exception:  # noqa: BLE001 - Markieren ist Beiwerk, darf das Verschieben nicht kippen
        logger.warning("SEEN-Flag vor Soft-Delete fehlgeschlagen", exc_info=True)
    if trash and trash != current_folder:
        box.move(uids, trash)
    else:
        box.delete(uids)


def delete_by_sender(
    account: MailAccount, password: str, sender: str, *, by_domain: bool = False,
    folders: list[str] | None = None,
) -> dict:
    """Verschiebt vorhandene Mails eines Absenders in den PAPIERKORB (als gelesen)
    in den genannten Ordnern.

    sender ist ein Teilstring (Adresse/Anzeigename bzw. Domain bei by_domain).
    folders=None -> Posteingang + Spam-Ordner (falls vorhanden). Ein Fehler in
    einem Ordner kippt die anderen nicht.
    """
    term = (sender or "").strip().lower()
    if not term:
        return {"deleted": 0}
    if folders is None:
        spam = _find_spam_folder(account, password)
        folders = ["INBOX"] + ([spam] if spam and spam != "INBOX" else [])
    total = 0
    for folder in folders:
        try:
            with _mailbox(account, password, folder=folder) as box:
                to_del: list[str] = []
                for msg in box.fetch(AND(all=True), reverse=True, mark_seen=False, limit=2000, headers_only=True, bulk=True):
                    if by_domain:
                        hay = (msg.from_ or "").rsplit("@", 1)[-1].lower()
                    else:
                        fv = getattr(msg, "from_values", None)
                        name = getattr(fv, "name", "") if fv else ""
                        hay = f"{msg.from_ or ''} {name}".lower()
                    if msg.uid and term in hay:
                        to_del.append(msg.uid)
                if to_del:
                    _soft_delete(box, to_del, folder)
                    total += len(to_del)
        except Exception:  # noqa: BLE001 - ein Ordner darf den Rest nicht kippen
            logger.warning("delete_by_sender fehlgeschlagen (account_id=%s, folder=%s)", account.id, folder, exc_info=True)
    return {"deleted": total}


def delete_message(account: MailAccount, password: str, uid: str, folder: str = "INBOX") -> str:
    """In den Papierkorb verschieben; ist keiner da (oder schon im Papierkorb) -> hart loeschen."""
    with _mailbox(account, password, folder=folder) as box:
        trash = _trash_folder(box, folder)
        if trash and trash != folder:
            box.move(uid, trash)
            return "moved"
        box.delete(uid)
        return "deleted"


def move_message(account: MailAccount, password: str, uid: str, dest: str, folder: str = "INBOX") -> None:
    with _mailbox(account, password, folder=folder) as box:
        box.move(uid, dest)


def delete_messages(account: MailAccount, password: str, uids: list[str], folder: str = "INBOX") -> dict:
    """Mehrere Mails in EINER IMAP-Session loeschen (Papierkorb oder hart).

    Statt pro Mail eine eigene Verbindung (N Logins) ein einziger Login + EIN
    MOVE/DELETE ueber die ganze UID-Liste — auf entfernten Servern (web.de) ist
    der Login-/Round-Trip der dominierende Kostenfaktor.
    """
    if not uids:
        return {"result": "none", "count": 0}
    with _mailbox(account, password, folder=folder) as box:
        trash = _trash_folder(box, folder)
        if trash and trash != folder:
            box.move(uids, trash)
            return {"result": "moved", "count": len(uids)}
        box.delete(uids)
        return {"result": "deleted", "count": len(uids)}


def move_messages(account: MailAccount, password: str, uids: list[str], dest: str, folder: str = "INBOX") -> dict:
    """Mehrere Mails in EINER IMAP-Session verschieben (ein Login + EIN MOVE)."""
    if not uids:
        return {"count": 0}
    with _mailbox(account, password, folder=folder) as box:
        box.move(uids, dest)
        return {"count": len(uids)}


def _delimiter(box: MailBox) -> str:
    """Server-Hierarchie-Trennzeichen (z. B. "/" oder ".") aus der Ordnerliste."""
    for f in box.folder.list():
        if getattr(f, "delim", None):
            return f.delim
    return "."


def create_folder(account: MailAccount, password: str, name: str, parent: str = "") -> str:
    """Legt einen (Unter-)Ordner an. parent leer = Top-Level. Liefert den vollen Namen."""
    with _mailbox(account, password) as box:
        full = f"{parent}{_delimiter(box)}{name}" if parent else name
        box.folder.create(full)
        return full


def delete_folder(account: MailAccount, password: str, name: str) -> None:
    with _mailbox(account, password) as box:
        box.folder.delete(name)


def _draft_folder(box: MailBox) -> str:
    """Findet den Entwürfe-Ordner (SPECIAL-USE \\Drafts oder Namensheuristik).

    Bevorzugt den SERVER-eigenen Ordner vor einem evtl. von uns frueher angelegten
    "INBOX/Drafts", damit Entwuerfe dort landen, wo der Anbieter sie erwartet.
    """
    delim = _delimiter(box)
    ours = {f"INBOX{delim}{s}" for s in DEFAULT_SUBFOLDERS}
    matches: list[str] = []
    for f in box.folder.list():
        flags = " ".join(getattr(f, "flags", ()) or ()).lower()
        if "\\drafts" in flags:
            return f.name
        low = f.name.lower()
        if any(k in low for k in ("drafts", "entwurf", "entwürfe", "entwuerfe")):
            matches.append(f.name)
    for name in matches:  # Server-eigenen Ordner bevorzugen
        if name not in ours:
            return name
    return matches[0] if matches else ""


def _sent_folder(box: MailBox) -> str:
    """Findet den Gesendet-Ordner (SPECIAL-USE \\Sent oder Namensheuristik).

    Bevorzugt den SERVER-eigenen Ordner vor einem evtl. von uns angelegten."""
    delim = _delimiter(box)
    ours = {f"INBOX{delim}{s}" for s in DEFAULT_SUBFOLDERS}
    matches: list[str] = []
    for f in box.folder.list():
        flags = " ".join(getattr(f, "flags", ()) or ()).lower()
        if "\\sent" in flags:
            return f.name
        low = f.name.lower()
        if any(k in low for k in ("sent", "gesendet", "gesendete")):
            matches.append(f.name)
    for name in matches:  # Server-eigenen Ordner bevorzugen
        if name not in ours:
            return name
    return matches[0] if matches else ""


# Anbieter, die SMTP-gesendete Mails SELBST in "Gesendet" ablegen → kein eigener
# APPEND, sonst entstehen Dubletten. Gmail ist der bekannte Fall.
_SELF_SAVES_SENT = ("gmail.com", "googlemail.com")


def save_to_sent(account: MailAccount, password: str, raw: bytes) -> bool:
    """Legt eine Kopie einer GESENDETEN Mail in den Gesendet-Ordner (IMAP APPEND,
    als gelesen markiert). Best-effort: ohne Gesendet-Ordner passiert nichts.

    Bei Anbietern, die das selbst tun (Gmail), wird NICHT abgelegt — sonst
    Dubletten. Der lokale Store/SelfMailer liest den Ordner ohnehin live per IMAP,
    daher ist die Kopie dort automatisch sichtbar (1:1 mit dem Anbieter)."""
    host = (account.smtp_host or "").lower()
    if any(h in host for h in _SELF_SAVES_SENT):
        return False
    with _mailbox(account, password) as box:
        folder = _sent_folder(box)
        if not folder:
            return False
        box.append(raw, folder, flag_set=["\\Seen"])
        return True


def save_draft(
    account: MailAccount,
    password: str,
    *,
    to: str = "",
    cc: str = "",
    subject: str = "",
    body: str = "",
    html: str = "",
) -> bool:
    """Legt eine ungesendete Nachricht als Entwurf im Entwürfe-Ordner ab (IMAP APPEND)."""
    msg = EmailMessage()
    msg["From"] = account.email
    if to:
        msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg.set_content(body or "")
    if html:
        msg.add_alternative(html, subtype="html")
    with _mailbox(account, password) as box:
        folder = _draft_folder(box)
        if not folder:
            return False
        box.append(msg.as_bytes(), folder, flag_set=["\\Draft"])
        return True


def move_folder(account: MailAccount, password: str, name: str, new_parent: str) -> str:
    """Verschiebt einen Ordner unter new_parent (leer = oberste Ebene) per IMAP-
    RENAME. Der Blattname bleibt; nur der Eltern-Pfad aendert sich."""
    with _mailbox(account, password) as box:
        delim = _delimiter(box)
        leaf = name.rsplit(delim, 1)[-1] if delim in name else name
        new_path = f"{new_parent}{delim}{leaf}" if new_parent else leaf
        if new_path == name:
            return name
        box.folder.rename(name, new_path)
        return new_path


def rename_folder(account: MailAccount, password: str, old: str, new_name: str) -> str:
    """Benennt einen Ordner um (gleicher Eltern-Pfad, nur Anzeigename neu)."""
    with _mailbox(account, password) as box:
        delim = _delimiter(box)
        parent = old.rsplit(delim, 1)[0] if delim in old else ""
        new_path = f"{parent}{delim}{new_name}" if parent else new_name
        box.folder.rename(old, new_path)
        return new_path


# Standard-Unterordner unter INBOX (ASCII-Namen; Anzeige wird im Frontend lokalisiert).
DEFAULT_SUBFOLDERS = ["Sent", "Drafts", "Trash", "Spam", "Archive"]
_DEFAULT_KIND = {"Sent": "sent", "Drafts": "drafts", "Trash": "trash", "Spam": "spam", "Archive": "archive"}

# Sonderordner-Erkennung (DE+EN) — spiegelt frontend/src/lib/folders.ts.
_SPECIAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("inbox", re.compile(r"^inbox$", re.I)),
    ("drafts", re.compile(r"^(drafts?|entw[uü]rfe?|entwurf)$", re.I)),
    ("sent", re.compile(r"^(sent|sent items|gesendet|gesendete objekte)$", re.I)),
    ("spam", re.compile(r"^(spam|junk|junk[- ]?e-?mail|werbung)$", re.I)),
    ("trash", re.compile(r"^(trash|deleted|deleted items|papierkorb|gel[oö]schte? objekte)$", re.I)),
    ("archive", re.compile(r"^(archive|archiv|archiviert)$", re.I)),
]


def _special_kind(last_part: str) -> str | None:
    for kind, rx in _SPECIAL_PATTERNS:
        if rx.match(last_part):
            return kind
    return None


# IMAP SPECIAL-USE-Flags (RFC 6154) -> unsere special-Art. Vergleich case-
# insensitiv und OHNE fuehrenden Backslash (siehe _folder_special).
_FLAG_KIND = {"sent": "sent", "drafts": "drafts", "junk": "spam", "trash": "trash", "archive": "archive",
              "all": "all", "flagged": "flagged", "important": "important"}


def _folder_special(name: str, flags) -> str:
    """Bestimmt die special-Art eines Ordners: erst \\Noselect, dann SPECIAL-USE-
    Flag (RFC 6154), zuletzt Namens-Heuristik. '' = normaler Ordner."""
    fl = {str(f).lstrip("\\").lower() for f in (flags or ())}
    if "noselect" in fl:
        return "noselect"
    for flag, kind in _FLAG_KIND.items():
        if flag in fl:
            return kind
    return _special_kind(name.replace("/", ".").rsplit(".", 1)[-1]) or ""


def apply_rules(account: MailAccount, password: str, rules: list) -> dict:
    """Wendet Filterregeln auf den Posteingang an (Modus A). Erste passende Regel je
    Mail gewinnt. rules: Objekte mit .field/.value/.target_folder/.mark_read/.star/
    .enabled. Liefert {affected, matched, errors} — affected = erfolgreich
    bearbeitete Mails, matched = passende Mails, errors = erste Fehlermeldungen
    (z. B. wenn der Zielordner nicht beschreibbar ist).
    """
    affected = 0
    errors: list[str] = []
    with _mailbox(account, password, folder="INBOX") as box:
        matches: list[tuple[str, object]] = []
        # Regeln prüfen nur Header (from/to/subject) → headers_only spart den
        # Body-Download; bulk=True bündelt die Round-Trips. reverse=True ist
        # ENTSCHEIDEND: ohne es holt imap-tools die AELTESTEN Mails — bei grossen
        # Postfaechern werden so die neuen (zu sortierenden) Mails nie gesehen.
        for msg in box.fetch(AND(all=True), reverse=True, mark_seen=False, limit=500, headers_only=True, bulk=True):
            for rule in rules:
                if not getattr(rule, "enabled", True) or not rule.value:
                    continue
                # Mehrere Begriffe kommagetrennt → trifft, wenn EINER vorkommt
                # (z. B. "slot, casino, bonus"). Einzelwert = Teilstring wie bisher.
                terms = [t.strip().lower() for t in rule.value.split(",") if t.strip()]
                if rule.field == "from":
                    # Adresse UND Anzeigename pruefen (vorher nur die Adresse —
                    # darum trafen Regeln auf den Klarnamen nicht).
                    fv = getattr(msg, "from_values", None)
                    name = getattr(fv, "name", "") if fv else ""
                    hay = f"{msg.from_ or ''} {name}".lower()
                elif rule.field == "from_domain":
                    # Nur die Absender-Domain (Teil nach dem letzten @), damit eine
                    # Regel auf die ganze (Haupt-)Domain matcht und verschiebt.
                    hay = (msg.from_ or "").rsplit("@", 1)[-1].lower()
                elif rule.field == "to":
                    hay = " ".join(msg.to or ()).lower()
                elif rule.field == "subject":
                    hay = (msg.subject or "").lower()
                else:
                    hay = ""
                if msg.uid and any(term in hay for term in terms):
                    matches.append((msg.uid, rule))
                    break  # erste passende Regel gewinnt
        to_delete: list[str] = []
        for uid, rule in matches:
            # "Loeschen" hat Vorrang vor allen anderen Aktionen: getroffene Mail
            # wandert in den Papierkorb (als gelesen) — kein Verschieben/Markieren mehr.
            if getattr(rule, "delete_msg", False):
                to_delete.append(uid)
                affected += 1
                continue
            try:
                if getattr(rule, "star", False):
                    box.flag(uid, FLAGGED, True)
                if getattr(rule, "mark_read", False):
                    box.flag(uid, SEEN, True)
                if rule.target_folder:
                    box.move(uid, rule.target_folder)
                affected += 1
            except Exception as exc:  # noqa: BLE001 - einzelne Aktion darf scheitern
                if len(errors) < 3:
                    errors.append(f"{getattr(rule, 'target_folder', '')}: {type(exc).__name__}: {exc}")
        # Loeschungen gebuendelt in den Papierkorb (ein MOVE statt N Round-Trips).
        if to_delete:
            try:
                _soft_delete(box, to_delete, "INBOX")
            except Exception as exc:  # noqa: BLE001
                affected -= len(to_delete)
                if len(errors) < 3:
                    errors.append(f"delete: {type(exc).__name__}: {exc}")
    return {"affected": affected, "matched": len(matches), "errors": errors}


def ensure_default_folders(account: MailAccount, password: str) -> None:
    """Bringt die Sonderordner in Ordnung (best effort).

    1. Frueher von UNS angelegte LEERE Doppel (INBOX/Sent|Drafts|Trash|Spam|Archive)
       werden geloescht, sobald der Server einen eigenen Ordner derselben Art mit-
       bringt. Server-Ordner haben immer Vorrang.
    2. Nur KOMPLETT fehlende Arten werden neu angelegt (z. B. Server mit nur INBOX),
       damit Entwuerfe/Papierkorb-Funktionen einen Zielordner haben.
    Es wird nie ein nicht-leerer Ordner geloescht.
    """
    with _mailbox(account, password) as box:
        delim = _delimiter(box)
        names = [f.name for f in box.folder.list()]
        ours = {f"INBOX{delim}{s}": _DEFAULT_KIND[s] for s in DEFAULT_SUBFOLDERS}

        def kind_of(name: str) -> str | None:
            return _special_kind(re.split(r"[/.]", name)[-1])

        # 1) Unsere Doppel auf den Server-Ordner gleicher Art zusammenfuehren:
        #    evtl. Inhalt dorthin VERSCHIEBEN (kein Verlust), dann unseren Ordner loeschen.
        for path, kind in ours.items():
            if path not in names:
                continue
            others = [n for n in names if n != path and kind_of(n) == kind]
            if not others:
                continue
            target = others[0]  # Server-eigener Ordner
            try:
                box.folder.set(path)
                uids = box.uids()
                if uids:
                    box.move(uids, target)  # Inhalt in den Server-Ordner
                box.folder.set("INBOX")
                box.folder.delete(path)
                names.remove(path)
            except Exception:  # noqa: BLE001 - best effort
                try:
                    box.folder.set("INBOX")
                except Exception:
                    pass

        # 2) Nur komplett fehlende Arten anlegen.
        present = {kind_of(n) for n in names if kind_of(n)}
        for sub in DEFAULT_SUBFOLDERS:
            kind = _DEFAULT_KIND[sub]
            if kind in present:
                continue
            full = f"INBOX{delim}{sub}"
            if full not in names:
                try:
                    box.folder.create(full)
                    names.append(full)
                    present.add(kind)
                except Exception:  # noqa: BLE001 - Server darf einzelne Namen ablehnen
                    continue
