import logging
from typing import Optional

from jose import jwt, JWTError

from app.core.config import settings

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"


def decode_token(token: str) -> Optional[dict]:
    """Decode a JWT token issued by admin_proteinaco.

    Returns dict with keys: sub, username, role, allowed_apps, exp
    Returns None if token is invalid or expired.
    """
    try:
        data = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        data["sub"] = int(data["sub"])
        return data
    except JWTError as e:
        logger.warning(f"JWT decode failed: {e}")
        return None
