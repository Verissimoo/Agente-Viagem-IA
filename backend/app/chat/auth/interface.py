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
