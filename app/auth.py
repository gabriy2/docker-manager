"""Autenticazione opzionale a sessione singola (utente + password via env)."""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

COOKIE_NAME = "dm_session"
_serializer = URLSafeTimedSerializer(settings.secret_key, salt="docker-manager-session")


def _max_age() -> int:
    return settings.session_days * 24 * 3600


def verify_credentials(username: str, password: str) -> bool:
    if not settings.auth_enabled:
        return True
    user_ok = secrets.compare_digest(username or "", settings.auth_user)
    pass_ok = secrets.compare_digest(password or "", settings.auth_password)
    return user_ok and pass_ok


def issue_session(response: Response, username: str, *, secure: bool) -> None:
    token = _serializer.dumps({"u": username})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=_max_age(),
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def current_user(request: Request) -> str | None:
    if not settings.auth_enabled:
        return "anonimo"
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=_max_age())
    except (BadSignature, SignatureExpired):
        return None
    return data.get("u")


def require_user(request: Request) -> str:
    user = current_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="autenticazione richiesta",
        )
    return user


AuthDep = Depends(require_user)
