"""Implementação in-memory do ChatRepository — dev/local only.

NÃO USE EM PRODUÇÃO. Não persiste entre restarts; não é thread-safe além
de um lock simples; não escala horizontalmente. Existe para destravar
desenvolvimento enquanto credenciais Supabase não chegam.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

from backend.app.chat.domain.models import (
    ChatMessage,
    ChatThread,
    Quote,
    QuoteStatus,
    User,
)
from backend.app.chat.repository.interface import ChatRepository


class InMemoryRepository(ChatRepository):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._users: Dict[str, User] = {}
        self._threads: Dict[str, ChatThread] = {}
        self._messages: Dict[str, List[ChatMessage]] = {}
        self._quotes: Dict[str, Quote] = {}

    # --- Users ---
    def upsert_user(self, user: User) -> User:
        with self._lock:
            self._users[user.id] = user
            return user

    def get_user(self, user_id: str) -> Optional[User]:
        with self._lock:
            return self._users.get(user_id)

    # --- Threads ---
    def create_thread(self, thread: ChatThread) -> ChatThread:
        with self._lock:
            self._threads[thread.id] = thread
            self._messages.setdefault(thread.id, [])
            return thread

    def get_thread(self, thread_id: str, user_id: str) -> Optional[ChatThread]:
        with self._lock:
            thread = self._threads.get(thread_id)
            if thread and thread.user_id == user_id:
                return thread
            return None

    def list_threads(
        self, user_id: str, *,
        include_archived: bool = False,
        only_with_user_messages: bool = False,
    ) -> List[ChatThread]:
        with self._lock:
            out = [
                t for t in self._threads.values()
                if t.user_id == user_id and (include_archived or not t.archived)
            ]
            if only_with_user_messages:
                msgs_by_thread = self._messages
                out = [
                    t for t in out
                    if any(
                        (m.role.value if hasattr(m.role, "value") else m.role) == "user"
                        for m in msgs_by_thread.get(t.id, [])
                    )
                ]
            out.sort(key=lambda t: t.updated_at, reverse=True)
            return out

    def update_thread(self, thread: ChatThread) -> ChatThread:
        with self._lock:
            existing = self._threads.get(thread.id)
            if existing is None or existing.user_id != thread.user_id:
                raise PermissionError("Thread não pertence ao usuário")
            thread.updated_at = datetime.now(timezone.utc)
            self._threads[thread.id] = thread
            return thread

    def delete_thread(self, thread_id: str, user_id: str) -> bool:
        with self._lock:
            thread = self._threads.get(thread_id)
            if thread is None or thread.user_id != user_id:
                return False
            del self._threads[thread_id]
            self._messages.pop(thread_id, None)
            return True

    # --- Messages ---
    def append_message(self, message: ChatMessage, *, user_id: str) -> ChatMessage:
        with self._lock:
            thread = self._threads.get(message.thread_id)
            if thread is None or thread.user_id != user_id:
                raise PermissionError("Thread não pertence ao usuário")
            self._messages.setdefault(message.thread_id, []).append(message)
            thread.updated_at = datetime.now(timezone.utc)
            return message

    def list_messages(self, thread_id: str, user_id: str, *, limit: int = 200) -> List[ChatMessage]:
        with self._lock:
            thread = self._threads.get(thread_id)
            if thread is None or thread.user_id != user_id:
                return []
            msgs = self._messages.get(thread_id, [])
            return msgs[-limit:]

    # --- Quotes ---
    def create_quote(self, quote: Quote) -> Quote:
        with self._lock:
            self._quotes[quote.id] = quote
            return quote

    def get_quote(self, quote_id: str, user_id: str) -> Optional[Quote]:
        with self._lock:
            q = self._quotes.get(quote_id)
            if q and q.user_id == user_id:
                return q
            return None

    def update_quote_status(
        self,
        quote_id: str,
        user_id: str,
        status: QuoteStatus,
        *,
        approved_offer_id: Optional[str] = None,
        pdf_path: Optional[str] = None,
    ) -> Optional[Quote]:
        with self._lock:
            q = self._quotes.get(quote_id)
            if q is None or q.user_id != user_id:
                return None
            q.status = status
            q.updated_at = datetime.now(timezone.utc)
            if approved_offer_id is not None:
                q.approved_offer_id = approved_offer_id
            if pdf_path is not None:
                q.pdf_path = pdf_path
            self._quotes[quote_id] = q
            return q

    def list_quotes(self, user_id: str, *, status: Optional[QuoteStatus] = None) -> List[Quote]:
        with self._lock:
            out = [q for q in self._quotes.values() if q.user_id == user_id]
            if status is not None:
                out = [q for q in out if q.status == status]
            out.sort(key=lambda q: q.updated_at, reverse=True)
            return out
