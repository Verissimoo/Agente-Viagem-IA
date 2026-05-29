"""Domínio do chat — tipos puros, sem I/O ou framework."""
from backend.app.chat.domain.models import (
    ChatMessage,
    ChatThread,
    MessageRole,
    Quote,
    QuoteStatus,
    User,
)

__all__ = [
    "ChatMessage",
    "ChatThread",
    "MessageRole",
    "Quote",
    "QuoteStatus",
    "User",
]
