"""
api/routers/auth.py
~~~~~~~~~~~~~~~~~~~
Signup and login, and the account itself: change the password, reset a forgotten one by
email, and delete the account with everything in it.

Every endpoint here sits behind the strict credential budget in
``api/rate_limit.py`` (``AUTH_PATHS``), so an attacker cannot mount an online
password-guessing attack against them.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api.auth import (
    RESET_TOKEN_MINUTES,
    create_access_token,
    create_reset_token,
    get_current_user,
    get_password_hash,
    password_fingerprint,
    read_reset_token,
    verify_password,
)
from api.schemas.auth import (
    AccountDelete,
    PasswordChange,
    PasswordForgot,
    PasswordReset,
    Token,
    UserCreate,
    UserLogin,
    UserResponse,
)
from services.email import email_enabled, send_email, site_link
from storage.database import get_db_dep
from storage.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

#: Comparison target for a login against an address with no account, so the
#: response time does not reveal whether that address is registered. Computed
#: once at import; the password it encodes is irrelevant and never valid.
_DUMMY_HASH = get_password_hash("not-a-real-password-placeholder")


def _normalise_email(email: str) -> str:
    """Fold an address to the form stored in ``users.email``.

    Mail domains are case-insensitive and people capitalise their address
    however they like. Storing it verbatim let the same person sign up twice
    as "Ada@Example.com" and "ada@example.com", and then be told "Incorrect
    email or password" after signing up with one and logging in with the other.
    """
    return email.strip().lower()


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(user_data: UserCreate, db: AsyncSession = Depends(get_db_dep)):
    email = _normalise_email(user_data.email)

    result = await db.execute(select(User).where(User.email == email))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")

    db_user = User(email=email, hashed_password=get_password_hash(user_data.password))
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


@router.post("/login", response_model=Token)
async def login(login_data: UserLogin, db: AsyncSession = Depends(get_db_dep)):
    result = await db.execute(
        select(User).where(User.email == _normalise_email(login_data.email))
    )
    user = result.scalars().first()

    # Hash even when the account does not exist. Returning early skipped the
    # bcrypt work, making "no such user" measurably faster than "wrong
    # password" — a free account-enumeration oracle for anyone timing the
    # endpoint.
    if user is None:
        verify_password(login_data.password, _DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={"sub": str(user.id), "email": user.email, "role": user.role}
    )
    return {"access_token": access_token, "token_type": "bearer"}


#: What "Forgot password" answers whether or not the address has an account, so the
#: answer cannot be used to find out who is registered.
FORGOT_REPLY = "If that address has an account, a reset link is on its way. It works for 30 minutes."


async def _signed_in_user(db: AsyncSession, token_user: dict[str, Any]) -> User:
    """The ``users`` row behind a session token. A token for a deleted account is a 401."""
    try:
        user_id = uuid.UUID(str(token_user.get("sub")))
    except ValueError:
        user_id = None
    user = (await db.execute(select(User).where(User.id == user_id))).scalars().first() if user_id else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChange,
    token_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> Response:
    """Replace the password, given the current one."""
    user = await _signed_in_user(db, token_user)
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Your current password is not right.")
    user.hashed_password = get_password_hash(body.new_password)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/account/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    body: AccountDelete,
    token_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
) -> Response:
    """Delete the account and everything in it, given the password.

    Every table a user owns references ``users.id`` with ``ON DELETE CASCADE``, so removing
    the row removes the profile, resumes, tracker, interviews, outreach, events saved or
    added, practice and agent memory with it.
    """
    user = await _signed_in_user(db, token_user)
    if not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That password is not right.")
    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()
    logger.info("Account deleted: id=%s", user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _send_reset_link(email: str, token: str) -> None:
    link = site_link(f"/reset-password?token={token}")
    await send_email(
        to=email,
        subject="Reset your TalentRadar password",
        text=(
            "Someone asked to reset the password for this TalentRadar account.\n\n"
            f"Choose a new password here (the link works for {RESET_TOKEN_MINUTES} minutes, once):\n"
            f"{link}\n\n"
            "If it was not you, ignore this mail: your password stays as it is."
        ),
    )


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    body: PasswordForgot,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db_dep),
) -> dict[str, str]:
    """Mail a reset link to an address that has an account. The reply is the same either way.

    The mail goes out after the response, so how long the reply takes does not say whether
    an account exists either.
    """
    if not email_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password reset by email is not set up on this server yet.",
        )
    email = _normalise_email(str(body.email))
    user = (await db.execute(select(User).where(User.email == email))).scalars().first()
    if user is not None:
        background.add_task(_send_reset_link, user.email, create_reset_token(str(user.id), user.hashed_password))
    return {"message": FORGOT_REPLY}


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(body: PasswordReset, db: AsyncSession = Depends(get_db_dep)) -> Response:
    """Set a new password from a reset link. A link works once, for 30 minutes."""
    claims = read_reset_token(body.token)
    user = None
    if claims is not None:
        try:
            user_id = uuid.UUID(claims[0])
        except ValueError:
            user_id = None
        if user_id is not None:
            user = (await db.execute(select(User).where(User.id == user_id))).scalars().first()
    # A used link names a password that has since changed, so its fingerprint no longer matches.
    if user is None or claims is None or password_fingerprint(user.hashed_password) != claims[1]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link has expired or was already used. Ask for a new one.",
        )
    user.hashed_password = get_password_hash(body.new_password)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
