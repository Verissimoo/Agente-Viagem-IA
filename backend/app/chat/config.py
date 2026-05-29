"""Configurações do produto chat — separadas do backend gerencial.

Todas as flags lêem env vars com defaults seguros. Para Supabase ainda não
temos credenciais reais, então o factory de Repository/Auth detecta ausência
e cai pro stub in-memory.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ChatSettings:
    # Feature flag mestre. Se 0, o router /chat não é registrado.
    enabled: bool = os.getenv("CHAT_ENABLED", "1") not in ("0", "false", "False", "")

    # Modelo principal (raciocínio: Intake, Validator, Presenter).
    primary_model: str = os.getenv("CHAT_PRIMARY_MODEL", "claude-sonnet-4-6")

    # Modelo barato/rápido para tarefas triviais (classificação, sanitização).
    cheap_model: str = os.getenv("CHAT_CHEAP_MODEL", "claude-haiku-4-5-20251001")

    # Provider do modelo: "anthropic" | "groq" | "openai"
    model_provider: str = os.getenv("CHAT_MODEL_PROVIDER", "anthropic")

    # Limites pra defesa básica
    max_message_chars: int = int(os.getenv("CHAT_MAX_MESSAGE_CHARS", "2000"))
    max_messages_per_thread: int = int(os.getenv("CHAT_MAX_MSGS_PER_THREAD", "200"))
    rate_limit_per_minute: int = int(os.getenv("CHAT_RATE_LIMIT_PER_MIN", "30"))
    rate_limit_searches_per_hour: int = int(os.getenv("CHAT_RATE_LIMIT_SEARCH_PER_HOUR", "60"))

    # Neon Postgres (connection string completa) — vazio = repo in-memory
    database_url: str = os.getenv("DATABASE_URL", "")

    # Neon Auth (Stack Auth) — vazio = usa DevAuthProvider
    # Mínimo necessário no backend: a JWKS URL (pra validar JWTs).
    # PROJECT_ID e keys são opcionais — o frontend que usa as keys.
    neon_auth_jwks_url: str = os.getenv("NEON_AUTH_JWKS_URL", "")
    neon_auth_project_id: str = os.getenv("NEON_AUTH_PROJECT_ID", "")
    neon_auth_publishable_key: str = os.getenv("NEON_AUTH_PUBLISHABLE_KEY", "")
    neon_auth_secret_key: str = os.getenv("NEON_AUTH_SECRET_KEY", "")

    # Identidade da empresa exposta ao usuário
    company_name: str = "Passagens com Desconto"
    assistant_name: str = "Atendente Passagens com Desconto"

    @property
    def use_postgres(self) -> bool:
        return bool(self.database_url)

    @property
    def use_neon_auth(self) -> bool:
        # Para ativar Neon Auth de verdade precisamos das 3 peças:
        # - JWKS URL (backend valida tokens)
        # - publishable key (frontend SDK)
        # - secret key (frontend server-side / fallback)
        # Sem todas, ficamos no DevAuthProvider para login/registro funcionar.
        return bool(
            self.neon_auth_jwks_url
            and self.neon_auth_publishable_key
            and self.neon_auth_secret_key
        )


settings = ChatSettings()
