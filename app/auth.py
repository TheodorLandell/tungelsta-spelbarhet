"""
Lösenordsskydd och session-hantering.

APP_PASSWORD läses från miljövariabel och hashas vid uppstart.
Sessions signeras med itsdangerous och håller i 30 dagar.
"""

import bcrypt
from itsdangerous import BadSignature, URLSafeTimedSerializer
from fastapi import Cookie, HTTPException

from app.config import settings

_MAX_AGE_SECONDS = 30 * 24 * 3600

_app_hash: bytes = bcrypt.hashpw(settings.app_password.encode(), bcrypt.gensalt())
_signer = URLSafeTimedSerializer(settings.app_password, salt="tungelsta-session")


def verify_password(submitted: str) -> bool:
    return bcrypt.checkpw(submitted.encode(), _app_hash)


def create_session_token() -> str:
    return _signer.dumps("ok")


def verify_session_token(token: str) -> bool:
    try:
        _signer.loads(token, max_age=_MAX_AGE_SECONDS)
        return True
    except BadSignature:
        return False


def require_session(session: str | None = Cookie(default=None)) -> None:
    if not session or not verify_session_token(session):
        raise HTTPException(status_code=401, detail="Inte inloggad")
