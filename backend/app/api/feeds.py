"""Abonnierbare Export-Feeds: Token-Verwaltung.

Ein Handy-Kalender/-Adressbuch abonniert eine URL und kann dabei keinen
Bearer-Header setzen. Deshalb authentifiziert ein geheimer Token. Pro User
genau ein Lese- und ein Schreib-Token, jederzeit rotierbar; eine Rotation
macht alte Abo-Links sofort ungueltig.

Drei Dinge sind seit 1.96.0 anders (Durchsicht 2026-09-27, Befunde 1-3):

1. Der Token darf zusaetzlich im Header "X-Feed-Token" kommen; der Header hat
   VORRANG. Damit landet er nicht im Zugriffslog. Der Query-Parameter bleibt
   als Rueckfall, weil echte Abo-Clients (Handy-Kalender) und die
   SelfDashboard-Kachel ihn heute so schicken - er darf nicht wegfallen.
   Fuer den Query-Weg maskiert core/logfilter.py das Zugriffslog.
2. In der DB steht nur noch der SHA-256-HASH des Tokens. Ein Salt ist hier
   bewusst unnoetig: der Token ist 24 Byte aus secrets.token_urlsafe (192 Bit
   Entropie), ein Woerterbuch-/Brute-Force-Angriff auf den Hash ist
   aussichtslos. Der Klartext ist genau einmal sichtbar - beim Erzeugen bzw.
   Rotieren.
3. Jeder Token hat ein Ablaufdatum (FEED_TOKEN_TTL_DAYS) und einen
   last_used_at-Zeitstempel. Letzterer wird hoechstens alle
   _LAST_USED_MIN_INTERVAL_S geschrieben, weil die Kachel sehr oft pollt
   (~11 Aufrufe/min) und jeder Schreibvorgang sonst eine SQLite-Transaktion
   kostet.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import secrets
import threading
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlmodel import Session, select

from ..core.config import get_settings
from ..core.db import get_session
from ..core.ratelimit import check_rate_limit
from ..models import FeedToken, User, utc_now
from ..schemas import FeedTokenOut, WriteTokenOut
from .deps import _bearer, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/feeds", tags=["feeds"])

# Lebensdauer eines Feed-Tokens. 180 Tage sind lang genug, dass niemand
# staendig nachkonfigurieren muss, und kurz genug, dass ein unbemerkt
# geleaktes Token nicht ewig gilt. Bestandstoken bekommen beim Migrieren
# dasselbe Fenster ab Migrationszeit (siehe core/db.py, user_version 3).
FEED_TOKEN_TTL_DAYS = 180

# last_used_at nicht bei jedem Aufruf schreiben (die Kachel pollt im
# Sekundenbereich) - alle 5 Minuten genuegt fuer "wird noch benutzt?".
_LAST_USED_MIN_INTERVAL_S = 300

# Rate-Limit je Token auf den Feed-Endpunkten. BEWUSST grosszuegig: die
# SelfDashboard-Kachel kommt heute auf ~11 Aufrufe/min, 120/min ist also mehr
# als das Zehnfache. Das Limit soll nur ein defektes Widget bremsen, das den
# IMAP-Pool flutet - nie den normalen Betrieb.
FEED_RATE_LIMIT = 120
FEED_RATE_WINDOW_S = 60.0

# Befund 2: ein SCHREIB-Token auf dem Lesepfad wird weiter akzeptiert (eine
# Kachel koennte damit eingerichtet sein und wuerde sonst ausfallen), aber
# einmal je Token und Stunde protokolliert. Der Token-WERT wird nie geloggt,
# nur die User-ID; der Schluessel im Dict ist der Hash.
_WRITE_ON_READ_WARN_INTERVAL_S = 3600.0
_warn_lock = threading.Lock()
_write_on_read_warned: dict[str, float] = {}


def _new_token() -> str:
    return secrets.token_urlsafe(24)


def token_hash(token: str) -> str:
    """SHA-256-Hex des Tokens. Kein Salt - siehe Modul-Docstring."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _expiry() -> dt.datetime:
    return utc_now() + dt.timedelta(days=FEED_TOKEN_TTL_DAYS)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    """SQLite gibt DATETIME ohne Zeitzone zurueck; als UTC lesen."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


def _expired(value: dt.datetime | None) -> bool:
    exp = _aware(value)
    return exp is not None and exp < utc_now()


def get_or_create_token(user: User, session: Session) -> tuple[FeedToken, str | None]:
    """Liefert die FeedToken-Zeile; legt sie bei Bedarf an.

    Zweiter Rueckgabewert ist der KLARTEXT - aber nur, wenn der Token in
    diesem Aufruf neu erzeugt wurde. Sonst None: in der DB steht nur der
    Hash, der Klartext ist nicht wiederherstellbar.
    """
    ft = session.exec(select(FeedToken).where(FeedToken.user_id == user.id)).first()
    if ft is None:
        raw = _new_token()
        ft = FeedToken(user_id=user.id, token=token_hash(raw), expires_at=_expiry())
        session.add(ft)
        session.commit()
        session.refresh(ft)
        return ft, raw
    return ft, None


def get_or_create_write_token(user: User, session: Session) -> tuple[FeedToken, str | None]:
    """Wie get_or_create_token, erzeugt zusaetzlich bei Bedarf den Schreib-Token."""
    ft, _raw = get_or_create_token(user, session)
    if not ft.write_token:
        raw = _new_token()
        ft.write_token = token_hash(raw)
        ft.write_expires_at = _expiry()
        session.add(ft)
        session.commit()
        session.refresh(ft)
        return ft, raw
    return ft, None


def _touch(ft: FeedToken, kind: str, session: Session) -> None:
    """Schreibt last_used_at - hoechstens alle _LAST_USED_MIN_INTERVAL_S."""
    now = utc_now()
    field = "last_used_at" if kind == "read" else "write_last_used_at"
    last = _aware(getattr(ft, field, None))
    if last is not None and (now - last).total_seconds() < _LAST_USED_MIN_INTERVAL_S:
        return
    setattr(ft, field, now)
    session.add(ft)
    session.commit()


def _warn_write_token_on_read(ft: FeedToken) -> None:
    """Befund 2: Schreib-Token auf dem Lesepfad - warnen, nicht ablehnen."""
    key = ft.write_token or ""
    now = time.monotonic()
    with _warn_lock:
        last = _write_on_read_warned.get(key, 0.0)
        if now - last < _WRITE_ON_READ_WARN_INTERVAL_S:
            return
        _write_on_read_warned[key] = now
        # Schutz gegen unbegrenztes Wachstum (ein Eintrag je Token).
        if len(_write_on_read_warned) > 1000:
            cutoff = now - _WRITE_ON_READ_WARN_INTERVAL_S
            for k in [k for k, v in _write_on_read_warned.items() if v < cutoff]:
                del _write_on_read_warned[k]
    logger.warning(
        "Schreib-Token auf Lesepfad benutzt - bitte Lese-Token verwenden "
        "(user_id=%s). Kuenftige Versionen lehnen das ab.",
        ft.user_id,
    )


def _resolve(token: str, session: Session, *, write_only: bool) -> User | None:
    """Loest einen Feed-Token zu einem aktiven User auf (oder None).

    write_only=True: NUR der Schreib-Token zaehlt. Sonst zaehlen beide, der
    Schreib-Token wird dabei aber protokolliert (Befund 2).
    """
    if not token:
        return None
    hashed = token_hash(token)
    ft = session.exec(
        select(FeedToken).where(
            (FeedToken.token == hashed) | (FeedToken.write_token == hashed)
        )
    ).first()
    if ft is None:
        return None
    kind = "write" if ft.write_token == hashed else "read"
    if write_only and kind != "write":
        return None
    if _expired(ft.expires_at if kind == "read" else ft.write_expires_at):
        logger.warning("Abgelaufener Feed-Token abgewiesen (user_id=%s)", ft.user_id)
        return None
    user = session.get(User, ft.user_id)
    if user is None or not user.is_active:
        return None
    if kind == "write" and not write_only:
        _warn_write_token_on_read(ft)
    _touch(ft, kind, session)
    return user


def user_for_feed_token(token: str, session: Session) -> User | None:
    """Loest einen Feed-Token (Lese- ODER Schreib-Token) zu einem aktiven User auf."""
    return _resolve(token, session, write_only=False)


def user_for_write_token(token: str, session: Session) -> User | None:
    """Loest NUR einen gueltigen SCHREIB-Token zu einem aktiven User auf (sonst None)."""
    return _resolve(token, session, write_only=True)


def _pick_token(header_token: str, query_token: str) -> str:
    """Header hat Vorrang, Query bleibt Rueckfall (Befund 1)."""
    return (header_token or "").strip() or (query_token or "").strip()


def feed_or_bearer_user(
    request: Request,
    token: str = Query(default=""),
    x_feed_token: str = Header(default="", alias="X-Feed-Token"),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> User:
    """Auth fuer Export-Endpoints: erst Token (Header/Query), sonst Login.

    So funktioniert derselbe Endpoint als abonnierbarer Feed (Token in der
    URL oder im Header), als direkter Aufruf aus der WebUI (httpOnly-Cookie)
    und aus der APK (Bearer).
    """
    presented = _pick_token(x_feed_token, token)
    if presented:
        check_rate_limit(
            "feed:" + token_hash(presented)[:16],
            limit=FEED_RATE_LIMIT,
            window_s=FEED_RATE_WINDOW_S,
        )
        user = user_for_feed_token(presented, session)
        if user is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Feed-Token ungueltig oder abgelaufen"
            )
        return user
    # get_current_user erwartet (request, creds, session) - request fuer den Cookie-Fallback.
    return get_current_user(request, creds, session)


def feed_write_or_login(
    request: Request,
    token: str = Query(default=""),
    x_feed_token: str = Header(default="", alias="X-Feed-Token"),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> User:
    """Auth fuer SCHREIBENDE Endpunkte (Termin anlegen/aendern/loeschen): NUR der
    Schreib-Token ODER ein echter Login. Der leck-anfaellige Lese-Token (steckt in
    Abo-/Export-URLs) wird hier bewusst NICHT akzeptiert - so kann ein vertrauens-
    wuerdiger Client (Dashboard-Widget) schreiben, ein geleakter Abo-Link aber nicht.
    """
    presented = _pick_token(x_feed_token, token)
    if presented:
        check_rate_limit(
            "feed:" + token_hash(presented)[:16],
            limit=FEED_RATE_LIMIT,
            window_s=FEED_RATE_WINDOW_S,
        )
        user = user_for_write_token(presented, session)
        if user is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Schreib-Token ungueltig oder abgelaufen"
            )
        return user
    return get_current_user(request, creds, session)


def _payload(ft: FeedToken, raw: str | None) -> FeedTokenOut:
    """Antwort fuer die Token-Endpunkte.

    Ist raw None (Token existiert schon), bleiben token und die Abo-URLs LEER -
    aus dem Hash laesst sich der Klartext nicht zurueckrechnen. has_token sagt
    der UI, dass ein Token existiert und nur noch rotiert werden kann.
    """
    if raw is None:
        return FeedTokenOut(
            token="",
            calendar_url="",
            contacts_url="",
            dashboard_url="",
            has_token=True,
            expires_at=_aware(ft.expires_at),
            last_used_at=_aware(ft.last_used_at),
        )
    base = get_settings().base_url.rstrip("/")
    return FeedTokenOut(
        token=raw,
        calendar_url=base + "/api/v1/calendar/export.ics?token=" + raw,
        contacts_url=base + "/api/v1/contacts/export.vcf?token=" + raw,
        dashboard_url=base + "/api/v1/dashboard/summary?token=" + raw,
        has_token=True,
        expires_at=_aware(ft.expires_at),
        last_used_at=_aware(ft.last_used_at),
    )


@router.get("/token", response_model=FeedTokenOut)
def show_token(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FeedTokenOut:
    """Zustand des persoenlichen Feed-Tokens.

    Existiert noch keiner, wird er hier erzeugt und der Klartext EINMAL
    mitgeliefert (inkl. Abo-URLs). Existiert er schon, kommt nur noch
    has_token/expires_at/last_used_at - wer die URL verloren hat, muss
    rotieren.
    """
    ft, raw = get_or_create_token(user, session)
    return _payload(ft, raw)


@router.post("/token/rotate", response_model=FeedTokenOut)
def rotate_token(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FeedTokenOut:
    """Setzt einen neuen Token; alte Abo-Links werden dadurch ungueltig.

    Nur hier (und beim ersten Erzeugen) ist der Klartext sichtbar.
    """
    ft, _raw = get_or_create_token(user, session)
    raw = _new_token()
    ft.token = token_hash(raw)
    ft.expires_at = _expiry()
    ft.last_used_at = None
    session.add(ft)
    session.commit()
    session.refresh(ft)
    return _payload(ft, raw)


def _write_payload(ft: FeedToken, raw: str | None) -> WriteTokenOut:
    return WriteTokenOut(
        write_token=raw or "",
        has_write_token=bool(ft.write_token),
        expires_at=_aware(ft.write_expires_at),
        last_used_at=_aware(ft.write_last_used_at),
    )


@router.get("/write-token", response_model=WriteTokenOut)
def show_write_token(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> WriteTokenOut:
    """Zustand des SCHREIB-Tokens fuers Dashboard/Kalender-Widget.

    Wie beim Lese-Token: der Klartext kommt nur beim ersten Erzeugen bzw. beim
    Rotieren. Nur fuer vertrauenswuerdige Clients - NICHT in Abo-URLs nutzen.
    """
    ft, raw = get_or_create_write_token(user, session)
    return _write_payload(ft, raw)


@router.post("/write-token/rotate", response_model=WriteTokenOut)
def rotate_write_token(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> WriteTokenOut:
    """Setzt einen neuen Schreib-Token; schreibende Clients muessen neu konfiguriert
    werden. Der Lese-Token (Abos) bleibt davon unberuehrt.
    """
    ft, _raw = get_or_create_token(user, session)
    raw = _new_token()
    ft.write_token = token_hash(raw)
    ft.write_expires_at = _expiry()
    ft.write_last_used_at = None
    session.add(ft)
    session.commit()
    session.refresh(ft)
    return _write_payload(ft, raw)
