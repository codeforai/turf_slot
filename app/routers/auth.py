from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import or_, select

from app.config import get_settings
from app.deps import CurrentUser, DbSession
from app.models import User
from app.schemas import Message
from app.schemas.users import (
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.security import (
    create_access_token,
    dummy_verify,
    generate_otp,
    hash_otp,
    hash_password,
    password_needs_rehash,
    verify_otp,
    verify_password,
)
from app.services.email import send_email

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession) -> User:
    existing = db.execute(
        select(User.email, User.phone).where(or_(User.email == payload.email, User.phone == payload.phone))
    ).first()
    if existing:
        field = "email" if existing.email == payload.email else "phone number"
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"An account with this {field} already exists")
    user = User(
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        phone=payload.phone,
        password_hash=hash_password(payload.password),
        role=payload.role,
        accepted_terms_at=datetime.now(UTC),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DbSession) -> TokenResponse:
    """OAuth2 password flow. Send the email as `username`. Works with the Authorize button in /docs."""
    user = db.scalar(select(User).where(User.email == form.username.strip().lower()))
    if user is None:
        dummy_verify(form.password)  # keep timing similar so emails can't be enumerated
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    if not verify_password(form.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This account has been blocked")
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(form.password)
        db.commit()
    token, expires_in = create_access_token(user.id, user.role, user.token_version)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_everywhere(user: CurrentUser, db: DbSession) -> None:
    """Invalidate every token issued to this account (all devices)."""
    user.token_version += 1
    db.commit()


@router.post("/password-reset/request", response_model=Message, status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(payload: PasswordResetRequest, db: DbSession, tasks: BackgroundTasks) -> Message:
    settings = get_settings()
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is not None and user.is_active:
        code = generate_otp()
        user.reset_code_hash = hash_otp(code)
        user.reset_code_expires_at = datetime.now(UTC) + timedelta(minutes=settings.password_reset_code_minutes)
        user.reset_attempts = 0
        db.commit()
        tasks.add_task(
            send_email,
            user.email,
            "Your TurfSlot password reset code",
            f"Hi {user.first_name},\n\nYour password reset code is {code}. "
            f"It expires in {settings.password_reset_code_minutes} minutes.\n\n"
            "If you didn't ask for this, you can ignore this email.",
        )
    # Same response whether or not the account exists, so emails can't be enumerated.
    return Message(detail="If an account exists for this email, a reset code has been sent.")


@router.post("/password-reset/confirm", response_model=Message)
def confirm_password_reset(payload: PasswordResetConfirm, db: DbSession) -> Message:
    settings = get_settings()
    invalid = HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset code")
    user = db.scalar(select(User).where(User.email == payload.email).with_for_update())
    if user is None or user.reset_code_hash is None or user.reset_code_expires_at is None:
        raise invalid
    if user.reset_code_expires_at <= datetime.now(UTC) or user.reset_attempts >= settings.password_reset_max_attempts:
        raise invalid
    if not verify_otp(payload.code, user.reset_code_hash):
        user.reset_attempts += 1
        db.commit()
        raise invalid
    user.password_hash = hash_password(payload.new_password)
    user.reset_code_hash = None
    user.reset_code_expires_at = None
    user.reset_attempts = 0
    user.token_version += 1  # log out all existing sessions
    db.commit()
    return Message(detail="Password updated. You can now log in.")
