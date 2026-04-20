"""HTTP Basic Auth para o painel admin (uso interno, 1 operador)."""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ..config import get_settings

_security = HTTPBasic()


def require_admin(
    credentials: HTTPBasicCredentials = Depends(_security),
) -> str:
    settings = get_settings()
    user_ok = secrets.compare_digest(credentials.username, settings.admin_user)
    pass_ok = secrets.compare_digest(credentials.password, settings.admin_pass)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
