"""NeonAuthProvider — valida JWTs emitidos pelo Stack Auth (Neon Auth).

Operação:
- Frontend faz login/register via SDK do Stack Auth (`@stackframe/stack` no Next).
- Cliente envia o `accessToken` no header `Authorization: Bearer <jwt>`.
- Backend chama `verify_token(jwt)` aqui — valida assinatura via JWKS público
  do projeto Stack Auth, checa `exp` e `iss`, e devolve `AuthSession`.
- Não fazemos login/register no backend: o Stack Auth gerencia.

Implementação de `login`/`register` levanta NotImplementedError de propósito
— rotas do FastAPI não devem expor esses fluxos; o cliente fala direto com
o Stack Auth para evitar manuseio de senha no nosso servidor.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from backend.app.chat.auth.interface import AuthError, AuthProvider, AuthSession
from backend.app.chat.domain.models import User
from backend.app.chat.repository import get_repository

logger = logging.getLogger(__name__)

_JWKS_CACHE: Dict[str, Any] = {}
_JWKS_CACHE_TTL_S = 60 * 60  # 1h


class NeonAuthProvider(AuthProvider):
    """Aceita JWKS URL direto, OU project_id (deriva a JWKS URL).

    O backend não precisa das publishable/secret keys — só verifica JWTs
    contra o JWKS público. As keys são usadas apenas pelo frontend (SDK).
    """

    def __init__(
        self,
        *,
        jwks_url: str = "",
        project_id: str = "",
        secret_key: str = "",
    ) -> None:
        self._project_id = project_id
        self._secret_key = secret_key
        if jwks_url:
            self._jwks_url = jwks_url
        elif project_id:
            self._jwks_url = (
                f"https://api.stack-auth.com/api/v1/projects/{project_id}"
                "/.well-known/jwks.json"
            )
        else:
            raise ValueError(
                "NeonAuthProvider precisa de jwks_url ou project_id"
            )

    def login(self, email: str, password: str) -> AuthSession:
        raise NotImplementedError(
            "Login/register são feitos via Stack Auth SDK no frontend. "
            "Backend só valida tokens — chame verify_token."
        )

    def register(
        self,
        email: str,
        password: str,
        *,
        display_name: Optional[str] = None,
        store_name: Optional[str] = None,
    ) -> AuthSession:
        raise NotImplementedError(
            "Login/register são feitos via Stack Auth SDK no frontend."
        )

    def verify_token(self, token: str) -> AuthSession:
        try:
            import jwt
            from jwt import PyJWKClient
        except ImportError as e:
            raise AuthError("pyjwt não instalado — rode pip install pyjwt[crypto]") from e

        # Cache do JWKS client por TTL para evitar request por requisição.
        now = time.time()
        cached = _JWKS_CACHE.get(self._jwks_url)
        if cached and now - cached["ts"] < _JWKS_CACHE_TTL_S:
            jwks_client = cached["client"]
        else:
            jwks_client = PyJWKClient(self._jwks_url)
            _JWKS_CACHE[self._jwks_url] = {"client": jwks_client, "ts": now}

        try:
            signing_key = jwks_client.get_signing_key_from_jwt(token).key
            payload = jwt.decode(
                token,
                signing_key,
                # Neon Data API usa EdDSA (Ed25519); Stack Auth usa RS256/ES256.
                # Mantemos os 3 ativos para suportar qualquer um dos serviços.
                algorithms=["EdDSA", "ES256", "RS256"],
                options={"verify_aud": False},
            )
        except Exception as e:
            raise AuthError(f"Token inválido: {e}") from e

        user_id = payload.get("sub")
        if not user_id:
            raise AuthError("Token sem subject")
        # Neon Data API usa `email` direto; Stack Auth usa `primary_email`.
        email = payload.get("email") or payload.get("primary_email") or ""
        display_name = (
            payload.get("name")
            or payload.get("display_name")
            or payload.get("user_metadata", {}).get("name")
        )

        # Garante que o perfil existe no nosso schema chat.users.
        # Stack Auth também sincroniza em neon_auth.users_sync, mas mantemos
        # nossa tabela própria para campos custom (store_name, etc.).
        repo = get_repository()
        if repo.get_user(user_id) is None:
            repo.upsert_user(
                User(
                    id=user_id,
                    email=email or f"{user_id}@unknown.local",
                    display_name=display_name,
                )
            )

        return AuthSession(
            user_id=user_id,
            email=email,
            access_token=token,
            display_name=display_name,
        )
