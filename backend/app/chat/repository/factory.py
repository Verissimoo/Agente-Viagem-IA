"""Factory — escolhe o repositório baseado em config.

- `DATABASE_URL` setado  → PostgresRepository (Neon ou outro Postgres)
- vazio                  → InMemoryRepository (dev/teste apenas)
"""
from __future__ import annotations

import logging
from functools import lru_cache

from backend.app.chat.config import settings
from backend.app.chat.repository.interface import ChatRepository
from backend.app.chat.repository.memory import InMemoryRepository

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_repository() -> ChatRepository:
    if settings.use_postgres:
        try:
            from backend.app.chat.repository.postgres import PostgresRepository
            logger.info("ChatRepository: usando PostgresRepository (Neon)")
            return PostgresRepository(settings.database_url)
        except Exception as e:
            logger.warning(
                "PostgresRepository falhou (%s). Caindo para InMemoryRepository — "
                "histórico NÃO será persistido. Verifique DATABASE_URL.", e,
            )
    logger.info("ChatRepository: usando InMemoryRepository (dev — não persiste)")
    return InMemoryRepository()
