from typing import Optional

from fastapi import Header, HTTPException, status

from app.core.auth import decode_token
from app.core.database import get_sqlalchemy_session


def get_db():
    yield from get_sqlalchemy_session()


def get_current_admin(
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> dict:
    """Extract and validate admin JWT token.

    Checks that the token is valid and that 'reklamace' is in allowed_apps.
    Returns user data dict with keys: sub, username, role, allowed_apps.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization[7:]
    user_data = decode_token(token)

    if not user_data:
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
