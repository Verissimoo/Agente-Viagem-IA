"""Repository — abstrai persistência de usuários, threads, mensagens e cotações.

Mantém os agentes e a API independentes do backend de armazenamento. O factory
em `get_repository()` decide entre `InMemoryRepository` (default) e
`SupabaseRepository` (quando as credenciais estiverem disponíveis).
"""
from backend.app.chat.repository.factory import get_repository
from backend.app.chat.repository.interface import ChatRepository

__all__ = ["ChatRepository", "get_repository"]
