"""Contrato de autenticação. JWT-first, agnóstico de provider."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class AuthError(Exception):
    """Falha de autenticação — credencial inválida, token expirado, etc."""


@dataclass(frozen=True)
class AuthSession:
    """Resultado de uma autenticação bem-sucedida."""
    user_id: str
    email: str
    access_token: str
    display_name: Optional[str] = None
    store_name: Optional[str] = None


class AuthProvider(ABC):
    @abstractmethod
    def login(self, email: str, password: str) -> AuthSession:
        """Email + senha → sessão. Levanta AuthError em falha."""

    @abstractmethod
    def register(
        self,
        email: str,
        password: str,
        *,
        display_name: Optional[str] = None,
        store_name: Optional[str] = None,
    ) -> AuthSession:
        """Cria novo usuário e retorna sessão já autenticada.

        Levanta AuthError se email já existir, senha fraca, etc.
        """

    @abstractmethod
    def verify_token(self, token: str) -> AuthSession:
        """Valida um Bearer token e devolve a sessão. Levanta AuthError se inválido."""

    # --- Reset de senha (opcional; default = não suportado) ---
    def request_password_reset(self, email: str) -> Optional[str]:
        """Cria um token de reset e dispara o e-mail. Retorna o token cru (pra
        teste) ou None se não houver conta — o chamador NUNCA deve revelar qual.
        Providers externos (Neon/Stack Auth) tratam reset por conta própria."""
        raise NotImplementedError("Reset de senha não suportado por este provider")

    def reset_password(self, token: str, new_password: str) -> AuthSession:
        """Valida o token e define a nova senha, devolvendo sessão autenticada."""
        raise NotImplementedError("Reset de senha não suportado por este provider")

    def set_password_direct(self, email: str, new_password: str) -> AuthSession:
        """Reset SIMPLES (sem e-mail): troca a senha de uma conta existente pelo
        e-mail. Interino até plugarmos SMTP (aí o fluxo passa por token)."""
        raise NotImplementedError("Reset de senha não suportado por este provider")
