"""Interface única de persistência do produto chat.

Todos os métodos exigem `user_id` para enforcement de isolamento — nenhuma
query lê thread/mensagem/cotação de outro usuário. Isso é defesa em
profundidade contra bugs de autorização em camadas superiores.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from backend.app.chat.domain.models import (
    BugReport,
    ChatMessage,
    ChatThread,
    Quote,
    QuoteStatus,
    QuoteValidation,
    ValidationKind,
    User,
)


class ChatRepository(ABC):
    # --- Users ---
    @abstractmethod
    def upsert_user(self, user: User) -> User: ...

    @abstractmethod
    def get_user(self, user_id: str) -> Optional[User]: ...

    # --- Threads ---
    @abstractmethod
    def create_thread(self, thread: ChatThread) -> ChatThread: ...

    @abstractmethod
    def get_thread(self, thread_id: str, user_id: str) -> Optional[ChatThread]: ...

    @abstractmethod
    def list_threads(
        self, user_id: str, *,
        include_archived: bool = False,
        only_with_user_messages: bool = False,
    ) -> List[ChatThread]: ...

    @abstractmethod
    def update_thread(self, thread: ChatThread) -> ChatThread: ...

    @abstractmethod
    def delete_thread(self, thread_id: str, user_id: str) -> bool: ...

    # --- Messages ---
    @abstractmethod
    def append_message(self, message: ChatMessage, *, user_id: str) -> ChatMessage:
        """Insere mensagem. `user_id` é checado contra `thread.user_id`."""

    @abstractmethod
    def list_messages(self, thread_id: str, user_id: str, *, limit: int = 200) -> List[ChatMessage]: ...

    # --- Quotes ---
    @abstractmethod
    def create_quote(self, quote: Quote) -> Quote: ...

    @abstractmethod
    def get_quote(self, quote_id: str, user_id: str) -> Optional[Quote]: ...

    @abstractmethod
    def update_quote_status(
        self,
        quote_id: str,
        user_id: str,
        status: QuoteStatus,
        *,
        approved_offer_id: Optional[str] = None,
        pdf_path: Optional[str] = None,
    ) -> Optional[Quote]: ...

    @abstractmethod
    def list_quotes(self, user_id: str, *, status: Optional[QuoteStatus] = None) -> List[Quote]: ...

    # --- Validações internas (sistema vs. manual) ---
    @abstractmethod
    def create_validation(self, validation: QuoteValidation) -> QuoteValidation: ...

    @abstractmethod
    def list_validations(
        self, user_id: str, *, kind: Optional[ValidationKind] = None,
        limit: int = 200, offset: int = 0,
    ) -> List[QuoteValidation]: ...

    @abstractmethod
    def list_validations_by_thread(self, thread_id: str, user_id: str) -> List[QuoteValidation]: ...

    @abstractmethod
    def validation_stats(self, user_id: str) -> Dict[str, Any]: ...

    # --- Bug reports ---
    @abstractmethod
    def create_bug_report(self, report: BugReport) -> BugReport: ...

    @abstractmethod
    def list_bug_reports(
        self, user_id: str, *, status: Optional[str] = None, limit: int = 200,
    ) -> List[BugReport]: ...
