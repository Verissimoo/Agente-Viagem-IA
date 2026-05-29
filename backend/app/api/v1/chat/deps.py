"""Dependências FastAPI do chat: autenticação, repo, audit.

Tudo aqui é stateless — cada request resolve via factory + token.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status

from backend.app.chat.auth import AuthError, AuthSession, get_auth_provider
from backend.app.chat.repository import ChatRepository, get_repository
from backend.app.chat.security.audit import AuditLogger, get_audit_logger


def get_repo() -> ChatRepository:
    return get_repository()


def get_audit() -> AuditLogger:
    return get_audit_logger()


def get_current_session(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> AuthSession:
    """Resolve a sessão a partir do Bearer token. 401 se ausente/inválido."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token ausente",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token vazio",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return get_auth_provider().verify_token(token)
    except AuthError as e:
        # Audit do token inválido — útil pra detectar varredura/abuso
        try:
            get_audit_logger().log(
                "auth.token.invalid",
                severity="security",
                ip_address=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                detail={"reason": str(e)[:200]},
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _client_ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if fwd:
        return fwd
    if request.client:
        return request.client.host
    return None


# Tipos prontos pra Depends
SessionDep = Depends(get_current_session)
RepoDep = Depends(get_repo)
AuditDep = Depends(get_audit)
