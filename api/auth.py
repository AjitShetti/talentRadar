"""
api/auth.py
~~~~~~~~~~~
JWT authentication middleware for the TalentRadar API.

Provides:
- Token generation (login endpoint)
- Token validation middleware
- Role-based access control helpers
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import hashlib

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import get_settings

# Bearer token scheme
security = HTTPBearer()

settings = get_settings()

def _prehash(password: str) -> bytes:
    """
    SHA-256 pre-hash the password before bcrypt.

    This sidesteps bcrypt's 72-byte input limit (longer passwords are silently
    truncated by the underlying C library) and is widely recommended when using
    bcrypt for password storage.
    """
    return hashlib.sha256(password.encode()).hexdigest().encode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its bcrypt hash."""
    return bcrypt.checkpw(_prehash(plain_password), hashed_password.encode())


def get_password_hash(password: str) -> str:
    """Hash a password with bcrypt (SHA-256 prehash to handle >72-byte inputs)."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(_prehash(password), salt).decode()


def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> str:
    """
    Create a JWT access token.

    Parameters
    ----------
    data : dict
        Payload data to encode (typically user_id, role, etc.)
    expires_delta : timedelta | None
        Token expiry duration. Defaults to settings.jwt_expiry_minutes.

    Returns
    -------
    str
        Encoded JWT token.
    """
    to_encode = data.copy()
    expire = datetime.now(tz=timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_expiry_minutes)
    )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return encoded_jwt


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT access token.

    Parameters
    ----------
    token : str
        The JWT token to decode.

    Returns
    -------
    dict
        Decoded token payload.

    Raises
    ------
    HTTPException
        If the token is invalid or expired.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (jwt.PyJWTError, jwt.InvalidTokenError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict[str, Any]:
    """
    FastAPI dependency to extract the current authenticated user.

    Use this in route handlers that require authentication:
        @router.get("/protected", dependencies=[Depends(get_current_user)])
    """
    return decode_access_token(credentials.credentials)


def require_role(required_role: str):
    """
    Dependency factory requiring a specific user role.

    A plain function, not a coroutine function: as ``async def`` this returned
    a *coroutine object* rather than the inner callable, so
    ``Depends(require_role("admin"))`` handed FastAPI something it could not
    call and the guard could never be used on a route.

    Rule: allow when the user holds the exact role, or is an admin (admins
    pass everywhere).

    Usage:
        @router.post("/admin", dependencies=[Depends(require_role("admin"))])
    """
    async def _check_role(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        user_role = user.get("role", "user")
        if user_role != required_role and user_role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _check_role
