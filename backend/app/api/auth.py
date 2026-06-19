"""Auth: First-Run-Setup, Login (+2FA/TOTP), eigenes Profil, 2FA-Verwaltung."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, func, select

from ..core.config import get_settings
from ..core.crypto import decrypt, encrypt
from ..core.db import get_session
from ..core.ratelimit import check_rate_limit, client_ip
from ..core.security import (
    create_access_token,
    create_mfa_token,
    decode_token,
    hash_password,
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
# Benutzer wird trotzdem ein Argon2-Verify ausgefuehrt, damit die Antwortzeit
# nicht verraet, ob der Benutzer existiert.
_DUMMY_HASH = hash_password("selfmailer-timing-dummy-do-not-use")


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
    """Prueft 2FA: zuerst TOTP (mit Replay-Schutz), dann Einmal-Backup-Code."""
    # 1) TOTP
    if user.totp_secret:
        try:
            secret = decrypt(user.totp_secret)
        except ValueError:
            secret = ""
        if secret:
            step = totp_lib.verify_code_step(secret, code)
            if step is not None:
                # Replay-Schutz: jeder Zeitschritt nur einmal.
                if step <= user.totp_last_step:
                    return False
                user.totp_last_step = step
                session.add(user)
                session.commit()
                return True
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
            row.used = True
            session.add(row)
            session.commit()
            return True
    return False


@router.get("/status")
def setup_status(session: Session = Depends(get_session)) -> dict:
    """Sagt dem Frontend, ob der erste Admin noch angelegt werden muss."""
    return {"needs_setup": _user_count(session) == 0}


@router.post("/setup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def setup(
    data: SetupRequest, request: Request, session: Session = Depends(get_session)
) -> TokenResponse:
    check_rate_limit(f"setup:{client_ip(request)}", limit=5, window_s=60)
    if _user_count(session) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup bereits abgeschlossen")

    settings = get_settings()
    # Wenn ein ADMIN_TOKEN per Env gesetzt ist, muss er stimmen.
    if settings.admin_token and data.admin_token != settings.admin_token:
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
    return TokenResponse(access_token=create_access_token(admin.username, admin.role.value))


@router.post("/login", response_model=LoginResponse)
def login(
    data: LoginRequest, request: Request, session: Session = Depends(get_session)
) -> LoginResponse:
    check_rate_limit(f"login:{client_ip(request)}", limit=10, window_s=60)
    user = session.exec(select(User).where(User.username == data.username)).first()
    # Immer ein Argon2-Verify ausfuehren (Dummy-Hash bei unbekanntem User), damit
    # die Antwortzeit nicht verraet, ob der Benutzername existiert.
    password_ok = verify_password(data.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Anmeldedaten falsch")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Konto gesperrt")
    # 2FA aktiv -> Passwort allein reicht nicht; Zwischen-Token ausstellen.
    if user.totp_enabled and user.totp_secret:
        return LoginResponse(needs_totp=True, mfa_token=create_mfa_token(user.username))
    return LoginResponse(access_token=create_access_token(user.username, user.role.value))


@router.post("/login/totp", response_model=TokenResponse)
def login_totp(
    data: TotpLoginRequest, request: Request, session: Session = Depends(get_session)
) -> TokenResponse:
    """Zweiter Login-Schritt: TOTP- oder Backup-Code gegen den mfa_token."""
    # Strenger als der Passwort-Login: ein 6-stelliger TOTP-Code hat nur 10^6
    # Moeglichkeiten — ohne Limit waere Online-Brute-Force denkbar.
    check_rate_limit(f"totp:{client_ip(request)}", limit=5, window_s=60)
    payload = decode_token(data.mfa_token)
    if not payload or payload.get("stage") != "mfa":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "2FA-Sitzung ungueltig oder abgelaufen")
    user = session.exec(select(User).where(User.username == payload.get("sub"))).first()
    if user is None or not user.is_active or not user.totp_enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "2FA-Sitzung ungueltig")
    if not _verify_second_factor(session, user, data.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code falsch")
    return TokenResponse(access_token=create_access_token(user.username, user.role.value))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/password")
def change_password(
    data: PasswordChange,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Eigenes Passwort ändern: aktuelles Passwort prüfen, dann neu setzen."""
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aktuelles Passwort falsch")
    user.password_hash = hash_password(data.new_password)
    session.add(user)
    session.commit()
    return {"ok": True}


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
    user.totp_secret = encrypt(secret)  # at-rest verschluesselt
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
    """Aktivieren: App-Code bestaetigen -> 2FA an + Backup-Codes (einmalig)."""
    if user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2FA ist bereits aktiv")
    if not user.totp_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bitte zuerst die Einrichtung starten")
    try:
        secret = decrypt(user.totp_secret)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Einrichtung ungueltig, bitte neu starten")
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
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Deaktivieren: Passwort bestaetigen -> Secret + Backup-Codes loeschen."""
    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Passwort falsch")
    user.totp_secret = ""
    user.totp_enabled = False
    user.totp_last_step = 0
    session.add(user)
    _clear_backup_codes(session, user.id)
    session.commit()
    return {"ok": True}
