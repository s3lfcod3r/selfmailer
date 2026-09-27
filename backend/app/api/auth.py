"""Auth: First-Run-Setup, Login (+2FA/TOTP), eigenes Profil, 2FA-Verwaltung."""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import update
from sqlmodel import Session, func, select

from ..core.config import get_settings
from ..core.crypto import decrypt, encrypt
from ..core.db import get_session
from ..core.ratelimit import check_rate_limit, client_ip
from ..core.security import (
    clear_session_cookie,
    create_access_token,
    create_mfa_token,
    decode_token,
    hash_password,
    set_session_cookie,
    verify_password,
)
from ..core import totp as totp_lib
from ..models import BackupCode, Role, User
from ..schemas import (
    LoginRequest,
    LoginResponse,
    PasswordChange,
    SetupRequest,
    TokenResponse,
    TotpDisableRequest,
    TotpEnableOut,
    TotpEnableRequest,
    TotpLoginRequest,
    TotpSetupOut,
    TotpStatusOut,
    UserOut,
)
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Konstanter Dummy-Hash gegen User-Enumeration per Timing: bei unbekanntem
# Benutzer wird trotzdem ein Argon2-Verify ausgeführt, damit die Antwortzeit
# nicht verrät, ob der Benutzer existiert.
_DUMMY_HASH = hash_password("selfmailer-timing-dummy-do-not-use")

# Hinweis-Text (Befund 5): nach Passwortwechsel / 2FA-Abschalten / "überall
# abmelden" werden die Feed-Token NICHT automatisch getauscht, weil das die
# eingerichtete SelfDashboard-Kachel und jeden Abo-Link sofort stillegen
# würde. Die UI zeigt diesen Text mit einem Link auf "Token rotieren".
_FEED_HINT = "Feed-Token jetzt rotieren?"


def _user_count(session: Session) -> int:
    return session.exec(select(func.count()).select_from(User)).one()


def _backup_codes_remaining(session: Session, user_id: int) -> int:
    return session.exec(
        select(func.count())
        .select_from(BackupCode)
        .where(BackupCode.user_id == user_id, BackupCode.used == False)  # noqa: E712
    ).one()


def _clear_backup_codes(session: Session, user_id: int) -> None:
    for row in session.exec(select(BackupCode).where(BackupCode.user_id == user_id)).all():
        session.delete(row)


def _verify_second_factor(session: Session, user: User, code: str) -> bool:
    """Prüft 2FA: zuerst TOTP (mit Replay-Schutz), dann Einmal-Backup-Code."""
    # 1) TOTP
    if user.totp_secret:
        try:
            secret = decrypt(user.totp_secret)
        except ValueError:
            secret = ""
        if secret:
            step = totp_lib.verify_code_step(secret, code)
            if step is not None:
                # Atomarer Verbrauch: zwei Sessions dürfen denselben alten
                # User-Zustand lesen, aber nur EINE darf den Schritt verbrauchen.
                result = session.execute(
                    update(User).where(
                        User.id == user.id, User.totp_enabled == True,  # noqa: E712
                        User.totp_secret == user.totp_secret,
                        User.totp_last_step < step,
                    ).values(totp_last_step=step).execution_options(synchronize_session=False)
                )
                session.commit()
                return result.rowcount == 1
    # 2) Backup-Code (Einmal-Nutzung)
    normalized = totp_lib.normalize_backup_code(code)
    if len(normalized) < 8:
        return False
    rows = session.exec(
        select(BackupCode).where(
            BackupCode.user_id == user.id, BackupCode.used == False  # noqa: E712
        )
    ).all()
    for row in rows:
        if verify_password(normalized, row.code_hash):
            result = session.execute(
                update(BackupCode).where(
                    BackupCode.id == row.id, BackupCode.user_id == user.id,
                    BackupCode.code_hash == row.code_hash,
                    BackupCode.used == False,  # noqa: E712
                ).values(used=True).execution_options(synchronize_session=False)
            )
            session.commit()
            return result.rowcount == 1
    return False


@router.get("/status")
def setup_status(session: Session = Depends(get_session)) -> dict:
    """Sagt dem Frontend, ob der erste Admin noch angelegt werden muss."""
    return {"needs_setup": _user_count(session) == 0}


@router.post("/setup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def setup(
    data: SetupRequest, request: Request, response: Response, session: Session = Depends(get_session)
) -> TokenResponse:
    check_rate_limit(f"setup:{client_ip(request)}", limit=5, window_s=60)
    if _user_count(session) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup bereits abgeschlossen")

    settings = get_settings()
    # Wenn ein ADMIN_TOKEN per Env gesetzt ist, muss er stimmen.
    if settings.admin_token and not hmac.compare_digest(data.admin_token or "", settings.admin_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin-Token falsch")

    admin = User(
        username=data.username,
        display_name=data.display_name or data.username,
        password_hash=hash_password(data.password),
        role=Role.admin,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    token = create_access_token(admin.username, admin.role.value, admin.token_version)
    set_session_cookie(response, token)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=LoginResponse)
def login(
    data: LoginRequest, request: Request, response: Response, session: Session = Depends(get_session)
) -> LoginResponse:
    check_rate_limit(f"login:{client_ip(request)}", limit=10, window_s=60)
    # Zusätzlich pro Benutzername bremsen, damit ein VERTEILTER Angriff (viele IPs)
    # ein einzelnes Konto nicht ungebremst durchprobieren kann. Läuft VOR der DB-
    # Abfrage → gleiche Bremse für existierende wie nicht existierende Namen (keine
    # Enumeration). Großzügig (30/min), damit normale Nutzer nicht gesperrt werden.
    check_rate_limit(f"login-user:{data.username.strip().lower()}", limit=30, window_s=60)
    user = session.exec(select(User).where(User.username == data.username)).first()
    # Immer ein Argon2-Verify ausführen (Dummy-Hash bei unbekanntem User), damit
    # die Antwortzeit nicht verrät, ob der Benutzername existiert.
    password_ok = verify_password(data.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Anmeldedaten falsch")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Konto gesperrt")
    # 2FA aktiv -> Passwort allein reicht nicht; Zwischen-Token ausstellen
    # (noch KEIN Session-Cookie — erst nach erfolgreichem zweiten Faktor).
    if user.totp_enabled and user.totp_secret:
        return LoginResponse(needs_totp=True, mfa_token=create_mfa_token(user.username))
    token = create_access_token(user.username, user.role.value, user.token_version)
    set_session_cookie(response, token)
    return LoginResponse(access_token=token)


@router.post("/login/totp", response_model=TokenResponse)
def login_totp(
    data: TotpLoginRequest, request: Request, response: Response, session: Session = Depends(get_session)
) -> TokenResponse:
    """Zweiter Login-Schritt: TOTP- oder Backup-Code gegen den mfa_token."""
    # Strenger als der Passwort-Login: ein 6-stelliger TOTP-Code hat nur 10^6
    # Möglichkeiten — ohne Limit wäre Online-Brute-Force denkbar.
    check_rate_limit(f"totp:{client_ip(request)}", limit=5, window_s=60)
    payload = decode_token(data.mfa_token)
    if not payload or payload.get("stage") != "mfa":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "2FA-Sitzung ungültig oder abgelaufen")
    # Zusätzlich pro KONTO begrenzen (nicht nur pro IP): sonst könnte ein verteilter
    # Angreifer den 6-stelligen Code eines bekannten mfa_token über viele IPs schneller
    # durchprobieren, als das IP-Limit vorsieht.
    check_rate_limit(f"totp-user:{payload.get('sub')}", limit=8, window_s=60)
    user = session.exec(select(User).where(User.username == payload.get("sub"))).first()
    if user is None or not user.is_active or not user.totp_enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "2FA-Sitzung ungültig")
    if not _verify_second_factor(session, user, data.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code falsch")
    token = create_access_token(user.username, user.role.value, user.token_version)
    set_session_cookie(response, token)
    return TokenResponse(access_token=token)


@router.post("/logout")
def logout(response: Response) -> dict:
    """Löscht das Session-Cookie (Web). Bearer-Tokens der APK sind davon nicht
    betroffen — die App verwirft ihr Token lokal. Wer wirklich ALLE Sitzungen
    beenden will (gestohlenes Bearer-Token), nimmt /logout-all."""
    clear_session_cookie(response)
    return {"ok": True}


@router.post("/logout-all")
def logout_all(
    response: Response,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Überall abmelden: macht ALLE ausgegebenen JWTs dieses Kontos ungültig.

    Setzt User.token_version eins höher (Befund 4). Jedes Token trägt die
    Version beim Ausstellen als Claim "tv"; danach passt keines mehr — auch
    nicht das Bearer-Token der Android-App und auch nicht ein gestohlenes.
    Das eigene Cookie wird gleich mitgelöscht, der Nutzer muss sich also auch
    hier neu anmelden. Das ist gewollt: sonst wäre "überall" nicht wahr.

    Die Feed-Token (Abo-Links, SelfDashboard-Kachel) bleiben bewusst
    UNBERÜHRT — sie sind keine Sitzungen und würden sonst still ausfallen.
    Wer sie auch tauschen will, rotiert sie unter Sync ausdrücklich.
    """
    user.token_version = int(user.token_version or 0) + 1
    session.add(user)
    session.commit()
    clear_session_cookie(response)
    return {"ok": True, "feed_token_hint": _FEED_HINT}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/password")
def change_password(
    data: PasswordChange,
    response: Response,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Eigenes Passwort ändern: aktuelles Passwort prüfen, dann neu setzen."""
    # Wie beim Login gegen Online-Brute-Force des aktuellen Passworts begrenzen.
    check_rate_limit(f"password:{user.id}", limit=5, window_s=300)
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aktuelles Passwort falsch")
    user.password_hash = hash_password(data.new_password)
    # Befund 4: alte JWTs (bis zu 7 Tage Restlaufzeit) müssen nach einem
    # Passwortwechsel ungültig werden — sonst behält ein Angreifer mit
    # gestohlenem Token trotz neuem Passwort Zugriff.
    user.token_version = int(user.token_version or 0) + 1
    session.add(user)
    session.commit()
    session.refresh(user)
    # Der Aufrufer selbst soll nicht rausfliegen: frisches Token mit der neuen
    # Version ausstellen und als Cookie setzen. Die Android-App nimmt
    # access_token aus der Antwort; ignoriert ein alter Client das Feld, muss
    # er sich einmal neu anmelden (erwartetes Verhalten nach Passwortwechsel).
    token = create_access_token(user.username, user.role.value, user.token_version)
    set_session_cookie(response, token)
    # Befund 5: Feed-Token werden ABSICHTLICH nicht automatisch rotiert — das
    # würde eine eingerichtete SelfDashboard-Kachel und alle Abo-Links ohne
    # Vorwarnung brechen. Stattdessen der Hinweis an die UI.
    return {"ok": True, "access_token": token, "feed_token_hint": _FEED_HINT}


# ---- 2FA / TOTP-Verwaltung (eigener Account) ----------------------------
@router.get("/totp/status", response_model=TotpStatusOut)
def totp_status(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> TotpStatusOut:
    return TotpStatusOut(
        enabled=user.totp_enabled,
        backup_codes_remaining=_backup_codes_remaining(session, user.id),
    )


@router.post("/totp/setup", response_model=TotpSetupOut)
def totp_setup(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> TotpSetupOut:
    """Einrichtung starten: neues Secret erzeugen (noch NICHT aktiv)."""
    if user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2FA ist bereits aktiv")
    secret = totp_lib.generate_secret()
    user.totp_secret = encrypt(secret)  # at-rest verschlüsselt
    user.totp_last_step = 0
    session.add(user)
    session.commit()
    return TotpSetupOut(
        secret=secret,
        otpauth_uri=totp_lib.build_otpauth_uri(user.username, secret),
    )


@router.post("/totp/enable", response_model=TotpEnableOut)
def totp_enable(
    data: TotpEnableRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> TotpEnableOut:
    """Aktivieren: App-Code bestätigen -> 2FA an + Backup-Codes (einmalig)."""
    if user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2FA ist bereits aktiv")
    if not user.totp_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bitte zuerst die Einrichtung starten")
    try:
        secret = decrypt(user.totp_secret)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Einrichtung ungültig, bitte neu starten")
    step = totp_lib.verify_code_step(secret, data.code)
    if step is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Code falsch")
    user.totp_enabled = True
    user.totp_last_step = step
    session.add(user)
    # Neue Backup-Codes (alte verwerfen).
    _clear_backup_codes(session, user.id)
    codes = totp_lib.generate_backup_codes()
    for raw in codes:
        session.add(
            BackupCode(
                user_id=user.id,
                code_hash=hash_password(totp_lib.normalize_backup_code(raw)),
            )
        )
    session.commit()
    return TotpEnableOut(backup_codes=codes)


@router.post("/totp/disable")
def totp_disable(
    data: TotpDisableRequest,
    response: Response,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Deaktivieren: Passwort bestätigen -> Secret + Backup-Codes löschen."""
    # Passwort-Bestätigung gegen Online-Brute-Force begrenzen (wie change_password).
    check_rate_limit(f"totpdis:{user.id}", limit=5, window_s=300)
    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Passwort falsch")
    user.totp_secret = ""
    user.totp_enabled = False
    user.totp_last_step = 0
    # Wie bei change_password: die zweite Stufe fällt weg, also dürfen alte
    # Sitzungen nicht weiterlaufen (Befund 4).
    user.token_version = int(user.token_version or 0) + 1
    session.add(user)
    _clear_backup_codes(session, user.id)
    session.commit()
    session.refresh(user)
    token = create_access_token(user.username, user.role.value, user.token_version)
    set_session_cookie(response, token)
    # Befund 5: Feed-Token NICHT automatisch rotieren (bricht die Kachel),
    # sondern anbieten.
    return {"ok": True, "access_token": token, "feed_token_hint": _FEED_HINT}
