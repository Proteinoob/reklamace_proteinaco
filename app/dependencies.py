from typing import Optional

from fastapi import Header, HTTPException, status

from app.core.auth import decode_token
from app.core.config import settings
from app.core.database import get_sqlalchemy_session


def get_db():
    yield from get_sqlalchemy_session()


_DEBUG_ADMIN = {
    "sub": 0,
    "username": "test-admin",
    "role": "admin",
    "allowed_apps": ["reklamace"],
}


def get_current_admin(
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> dict:
    """Extract and validate admin JWT token.

    Checks that the token is valid and that 'reklamace' is in allowed_apps.
    Returns user data dict with keys: sub, username, role, allowed_apps.

    In DEBUG mode, returns a mock admin when no token is provided.
    """
    # Extract token (may be missing or empty)
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip() or None

    # DEBUG mode: skip auth when no valid token
    if not token and settings.DEBUG:
        return _DEBUG_ADMIN

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing or empty",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_data = decode_token(token)

    if not user_data:
        if settings.DEBUG:
            return _DEBUG_ADMIN
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check allowed apps
    allowed_apps = user_data.get("allowed_apps", [])
    if "reklamace" not in allowed_apps:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access to reklamace not permitted",
        )

    return user_data
