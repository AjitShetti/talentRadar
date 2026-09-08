"""
api/routers/auth.py
~~~~~~~~~~~~~~~~~~~
Signup and login.

Both endpoints sit behind the strict credential budget in
``api/rate_limit.py`` (``AUTH_PATHS``), so an attacker cannot mount an online
password-guessing attack against them.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api.auth import create_access_token, get_password_hash, verify_password
from api.schemas.auth import Token, UserCreate, UserLogin, UserResponse
from storage.database import get_db_dep
from storage.models import User

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
