"""Factory de AuthProvider.

- Neon Auth configurado  → NeonAuthProvider (produção)
- caso contrário         → DevAuthProvider (email+senha local, dev)
"""
from __future__ import annotations

import logging
from functools import lru_cache

from backend.app.chat.auth.interface import AuthProvider
from backend.app.chat.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_auth_provider() -> AuthProvider:
    if settings.use_neon_auth:
        from backend.app.chat.auth.neon import NeonAuthProvider
        logger.info("AuthProvider: usando NeonAuthProvider (Stack Auth)")
        return NeonAuthProvider(
            jwks_url=settings.neon_auth_jwks_url,
            project_id=settings.neon_auth_project_id,
            secret_key=settings.neon_auth_secret_key,
        )
    from backend.app.chat.auth.dev import DevAuthProvider
    logger.info("AuthProvider: usando DevAuthProvider (email+senha local)")
    return DevAuthProvider()
