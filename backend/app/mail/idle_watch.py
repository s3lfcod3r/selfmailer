"""IMAP IDLE fuer den Posteingang (seit 1.97.2).

Bis 1.97.1 hat SelfMailer neue Mails nur ABGEFRAGT: der Hintergrund-Dienst lief
alle 120 s ueber alle Konten, und die 120 s zaehlen erst nach dem Ende der
ganzen Runde. Am 24.10.2026 an einem Gmail-Konto gemessen: eine neue Mail stand
nach 136 s im Cache, obwohl Gmail sie nach wenigen Sekunden hatte.

Jetzt haelt je Konto ein Waechter-Thread eine EIGENE Verbindung im IDLE
(RFC 2177): der Server meldet sich von selbst, sobald im Posteingang eine Mail
dazukommt (EXISTS) oder verschwindet (EXPUNGE). Dann laeuft sofort ein
Schnelldurchlauf fuer genau dieses Konto (scheduler.sync_inbox_now: Regeln,
Posteingang, Push, Vorladen). Die normale Runde bleibt unveraendert als
Sicherheitsnetz.

Regeln:
- Meldungen waehrend eines laufenden Durchlaufs werden gesammelt; danach laeuft
  GENAU ein weiterer. So geht nichts verloren, und ein Postfach, das gerade
  zehn Mails auf einmal bekommt, loest nicht zehn Durchlaeufe aus.
- IDLE wird alle _ERNEUERN Sekunden beendet und neu gestartet (RFC: spaetestens
  nach 29 min; Router/NAT kappen ruhende Verbindungen oft schon frueher).
- Verbindungsfehler: neu verbinden mit wachsender Pause (_PAUSE_MIN.._PAUSE_MAX).
- Server ohne IDLE: Waechter endet, das Konto bleibt beim normalen Abfragen.
  Nach _UNSUPPORTED_RETRY wird es noch einmal versucht (Faehigkeiten koennen
  nach einem Fehler beim Login unvollstaendig gewesen sein).
- Abschaltbar mit SELFMAILER_IDLE=0.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session, select

from ..core.crypto import decrypt
from ..core.db import engine
from ..models import MailAccount, User
from . import imap as imap_mod

logger = logging.getLogger(__name__)

ENABLED = os.getenv("SELFMAILER_IDLE", "1").strip().lower() not in {"0", "false", "no"}
_ERNEUERN = 8 * 60.0          # IDLE spaetestens so oft neu starten
_POLL_STUECK = 30.0           # so oft schaut der Thread nach dem Stopp-Signal
_PAUSE_MIN = 5.0
_PAUSE_MAX = 300.0
_UNSUPPORTED_RETRY = 6 * 3600.0
# Eine tote Verbindung meldet sich beim poll() sofort als "lesbar", liefert aber
# nichts (EOF). So viele leere Sofort-Antworten in Folge gelten als Abbruch.
_LEER_LIMIT = 3
_AUSLOESER = re.compile(rb"^\* \d+ (EXISTS|EXPUNGE)\b", re.IGNORECASE)

# Schnelldurchlaeufe laufen getrennt von der normalen Runde (eigener Pool):
# sonst stuenden sie hinter einem langsamen Konto in der Schlange.
_DURCHLAUF_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="idle-sync")

_handler: Callable[[int], None] | None = None
_waechter: dict[int, "_Waechter"] = {}
_lock = threading.Lock()
# account_id -> [laeuft, nochmal]
_durchlauf: dict[int, list[bool]] = {}


class _VerbindungWeg(Exception):
    """IDLE-Verbindung ist tot oder hat sich verabschiedet."""


def _bindung(acc: MailAccount) -> tuple:
    """Was sich nicht aendern darf, ohne dass neu verbunden wird."""
    return (acc.imap_host, acc.imap_port, getattr(acc, "imap_ssl", True), acc.auth_user or acc.email,
            acc.secret_enc, getattr(acc, "imap_tls_fingerprint", ""))


def _konto_laden(account_id: int) -> tuple[MailAccount | None, str]:
    with Session(engine) as s:
        acc = s.exec(
            select(MailAccount).join(User, MailAccount.user_id == User.id)
            .where(MailAccount.id == account_id, User.is_active == True)  # noqa: E712
        ).first()
        if acc is None:
            return None, ""
        s.expunge(acc)
    return acc, decrypt(acc.secret_enc)


# --- Schnelldurchlauf mit Zusammenfassen ------------------------------------

def ausloesen(account_id: int) -> None:
    """Schnelldurchlauf anstossen; laeuft schon einer, genau einen weiteren vormerken."""
    if _handler is None:
        return
    with _lock:
        zustand = _durchlauf.setdefault(account_id, [False, False])
        if zustand[0]:
            zustand[1] = True
            return
        zustand[0] = True
    try:
        _DURCHLAUF_POOL.submit(_durchlauf_schleife, account_id)
    except RuntimeError:  # Pool beim Herunterfahren schon zu
        with _lock:
            _durchlauf[account_id] = [False, False]


def _durchlauf_schleife(account_id: int) -> None:
    while True:
        try:
            if _handler is not None:
                _handler(account_id)
        except Exception:  # noqa: BLE001 - ein Durchlauf darf den Waechter nie kippen
            logger.warning("IDLE-Durchlauf fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        with _lock:
            zustand = _durchlauf.setdefault(account_id, [False, False])
            if zustand[1]:
                zustand[1] = False
                continue
            zustand[0] = False
            return


# --- Waechter ---------------------------------------------------------------

class _Waechter(threading.Thread):
    def __init__(self, account_id: int, bindung: tuple) -> None:
        super().__init__(name=f"imap-idle-{account_id}", daemon=True)
        self.account_id = account_id
        self.bindung = bindung
        self.stopp = threading.Event()
        self.unsupported_seit: float | None = None
        self.verbunden = False
        self.letztes_ereignis: float | None = None

    def run(self) -> None:
        pause = _PAUSE_MIN
        erster = True
        while not self.stopp.is_set():
            box = None
            try:
                acc, pw = _konto_laden(self.account_id)
                if acc is None:
                    return
                box = imap_mod.open_idle_connection(acc, pw)
                if not imap_mod.supports_idle(box):
                    self.unsupported_seit = time.monotonic()
                    logger.info("Server kann kein IDLE, bleibt beim Abfragen (account_id=%s)", self.account_id)
                    return
                self.verbunden = True
                pause = _PAUSE_MIN
                if not erster:
                    # Nach einem Abbruch koennte waehrenddessen Post gekommen sein.
                    ausloesen(self.account_id)
                erster = False
                self._lauschen(box)
            except Exception as exc:  # noqa: BLE001 - Netz/Server: neu verbinden
                # Keine Server-Antwort/Zugangsdaten ins Log, nur der Fehlertyp.
                logger.info("IDLE-Verbindung unterbrochen (account_id=%s): %s", self.account_id, type(exc).__name__)
            finally:
                self.verbunden = False
                if box is not None:
                    try:
                        imap_mod.close_idle_connection(box)
                    except Exception:  # noqa: BLE001
                        pass
            if self.stopp.wait(pause):
                return
            pause = min(_PAUSE_MAX, pause * 2)

    def _lauschen(self, box) -> None:
        while not self.stopp.is_set():
            box.idle.start()
            beginn = time.monotonic()
            leer = 0
            try:
                while not self.stopp.is_set() and time.monotonic() - beginn < _ERNEUERN:
                    t = time.monotonic()
                    zeilen = box.idle.poll(timeout=_POLL_STUECK)
                    if not zeilen:
                        leer = leer + 1 if time.monotonic() - t < 1.0 else 0
                        if leer >= _LEER_LIMIT:
                            raise _VerbindungWeg("leere Antworten")
                        continue
                    leer = 0
                    if any(z.upper().startswith(b"* BYE") for z in zeilen):
                        raise _VerbindungWeg("BYE")
                    if any(_AUSLOESER.match(z) for z in zeilen):
                        self.letztes_ereignis = time.time()
                        ausloesen(self.account_id)
            finally:
                if not self.stopp.is_set():
                    box.idle.stop()   # bestaetigt zugleich, dass die Verbindung lebt


# --- Verwaltung ---------------------------------------------------------------

def set_handler(fn: Callable[[int], None] | None) -> None:
    global _handler
    _handler = fn


def _starten(acc: MailAccount) -> None:
    w = _Waechter(acc.id, _bindung(acc))
    _waechter[acc.id] = w
    w.start()


def abgleichen(accounts: list[MailAccount]) -> None:
    """Waechter passend zu den Konten aktiver Nutzer starten/stoppen/erneuern."""
    if not ENABLED or _handler is None:
        return
    gewuenscht = {a.id: a for a in accounts if a.id is not None}
    jetzt = time.monotonic()
    with _lock:
        for account_id, w in list(_waechter.items()):
            acc = gewuenscht.get(account_id)
            if acc is None or w.bindung != _bindung(acc):
                w.stopp.set()
                del _waechter[account_id]
            elif not w.is_alive():
                if w.unsupported_seit is not None and jetzt - w.unsupported_seit < _UNSUPPORTED_RETRY:
                    continue
                del _waechter[account_id]
        for account_id, acc in gewuenscht.items():
            if account_id not in _waechter:
                _starten(acc)


def stop_all() -> None:
    with _lock:
        for w in _waechter.values():
            w.stopp.set()
        _waechter.clear()


def status() -> list[dict]:
    """Fuer /mail/pool-status: welches Konto lauscht gerade per IDLE."""
    with _lock:
        return [
            {"account_id": aid, "verbunden": w.verbunden, "laeuft": w.is_alive(),
             "kein_idle": w.unsupported_seit is not None, "letztes_ereignis": w.letztes_ereignis}
            for aid, w in sorted(_waechter.items())
        ]
