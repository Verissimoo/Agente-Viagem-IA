"""Implementação in-memory do ChatRepository — dev/local only.

NÃO USE EM PRODUÇÃO. Não persiste entre restarts; não é thread-safe além
de um lock simples; não escala horizontalmente. Existe para destravar
desenvolvimento enquanto credenciais Supabase não chegam.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.app.chat.domain.models import (
    BugReport,
    ChatMessage,
    ChatThread,
    Quote,
    QuoteStatus,
    QuoteValidation,
    RankingFeedback,
    ValidationKind,
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
        self._validations: List[QuoteValidation] = []
        self._ranking: List[RankingFeedback] = []
        self._bug_reports: List[BugReport] = []
        # email → {id, email, password_hash, display_name, store_name}
        self._auth: Dict[str, dict] = {}

    # --- Users ---
    def upsert_user(self, user: User) -> User:
        with self._lock:
            self._users[user.id] = user
            return user

    def get_user(self, user_id: str) -> Optional[User]:
        with self._lock:
            return self._users.get(user_id)

    # --- Auth ---
    def get_user_by_email(self, email: str) -> Optional[User]:
        with self._lock:
            e = (email or "").strip().lower()
            for u in self._users.values():
                if (u.email or "").strip().lower() == e:
                    return u
            acct = self._auth.get(e)
            return User(id=acct["id"], email=acct["email"],
                        display_name=acct.get("display_name"),
                        store_name=acct.get("store_name")) if acct else None

    def get_auth_account(self, email: str) -> Optional[dict]:
        with self._lock:
            acct = self._auth.get((email or "").strip().lower())
            return dict(acct) if acct and acct.get("password_hash") else None

    def upsert_auth_account(self, *, user_id: str, email: str, password_hash: str,
                            display_name: Optional[str] = None,
                            store_name: Optional[str] = None) -> None:
        with self._lock:
            self._auth[(email or "").strip().lower()] = {
                "id": user_id, "email": email, "password_hash": password_hash,
                "display_name": display_name, "store_name": store_name,
            }

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

    # --- Validações internas ---
    def create_validation(self, validation: QuoteValidation) -> QuoteValidation:
        with self._lock:
            # Idempotência: mesmo user+offer+kind → devolve o existente.
            if validation.offer_id:
                for v in self._validations:
                    if (v.user_id == validation.user_id and v.offer_id == validation.offer_id
                            and v.kind == validation.kind):
                        return v
            self._validations.append(validation)
            return validation

    def list_validations(
        self, user_id: str, *, kind: Optional[ValidationKind] = None,
        limit: int = 200, offset: int = 0,
    ) -> List[QuoteValidation]:
        with self._lock:
            out = [v for v in self._validations if v.user_id == user_id]
            if kind is not None:
                out = [v for v in out if v.kind == kind]
            out.sort(key=lambda v: v.created_at, reverse=True)
            return out[offset:offset + limit]

    def list_validations_by_thread(self, thread_id: str, user_id: str) -> List[QuoteValidation]:
        with self._lock:
            return [v for v in self._validations
                    if v.thread_id == thread_id and v.user_id == user_id]

    def validation_stats(self, user_id: str) -> Dict[str, Any]:
        with self._lock:
            vs = [v for v in self._validations if v.user_id == user_id]
        return _compute_validation_stats(vs)

    # --- Ranking feedback ("cotação ideal") ---
    def upsert_ranking_feedback(self, feedback: RankingFeedback) -> RankingFeedback:
        with self._lock:
            self._ranking = [
                f for f in self._ranking
                if not (f.user_id == feedback.user_id and f.thread_id == feedback.thread_id
                        and f.message_id == feedback.message_id)
            ]
            self._ranking.append(feedback)
            return feedback

    def list_ranking_feedback_by_thread(
        self, thread_id: str, user_id: str,
    ) -> List[RankingFeedback]:
        with self._lock:
            return [f for f in self._ranking
                    if f.thread_id == thread_id and f.user_id == user_id]

    # --- Bug reports ---
    def create_bug_report(self, report: BugReport) -> BugReport:
        with self._lock:
            self._bug_reports.append(report)
            return report

    def list_bug_reports(
        self, user_id: str, *, status: Optional[str] = None, limit: int = 200,
    ) -> List[BugReport]:
        with self._lock:
            out = [b for b in self._bug_reports if b.user_id == user_id]
            if status is not None:
                out = [b for b in out if b.status == status]
            out.sort(key=lambda b: b.created_at, reverse=True)
            return out[:limit]


def _compute_validation_stats(vs: List[QuoteValidation]) -> Dict[str, Any]:
    """Agregado em Python (usado pelo memory; o postgres faz via SQL).
    accuracy = validated / (validated+corrected); delta médio = sistema − manual."""
    total = len(vs)
    validated = sum(1 for v in vs if v.kind == ValidationKind.VALIDATED)
    corrected = total - validated
    denom = validated + corrected
    accuracy = round(100.0 * validated / denom, 1) if denom else 0.0

    deltas, by_method, by_airline = [], {}, {}
    for v in vs:
        if v.kind != ValidationKind.CORRECTED:
            continue
        sys_val = (v.system_offer or {}).get("equivalent_brl") or (v.system_offer or {}).get("price_brl")
        if sys_val is not None and v.found_value_brl is not None:
            deltas.append(float(sys_val) - float(v.found_value_brl))
        if v.emission_method:
            by_method[v.emission_method] = by_method.get(v.emission_method, 0) + 1
        if v.found_airline:
            by_airline[v.found_airline] = by_airline.get(v.found_airline, 0) + 1

    return {
        "total": total,
        "validated_count": validated,
        "corrected_count": corrected,
        "accuracy_pct": accuracy,
        "avg_delta_brl": round(sum(deltas) / len(deltas), 2) if deltas else None,
        "by_method": by_method,
        "by_airline": by_airline,
    }
