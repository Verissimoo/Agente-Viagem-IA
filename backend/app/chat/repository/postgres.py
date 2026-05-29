"""PostgresRepository — impl de produção (Neon ou qualquer Postgres 14+).

Usa psycopg 3 com ConnectionPool. Conexão configurada via `DATABASE_URL`.
Todas as queries usam parâmetros (`%s`) — nunca interpolação de string.
Isolamento por usuário é enforced em EVERY method via `WHERE user_id = %s`.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from backend.app.chat.domain.models import (
    ChatMessage,
    ChatThread,
    MessageRole,
    Quote,
    QuoteStatus,
    User,
)
from backend.app.chat.repository.interface import ChatRepository

logger = logging.getLogger(__name__)


def _to_aware(dt: Any) -> datetime:
    """Garante timezone-aware datetime — Postgres retorna aware quando coluna é TIMESTAMPTZ."""
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _jsonb(value: Any) -> Jsonb:
    return Jsonb(value if value is not None else {})


def _check_connection(conn):
    """Pre-ping pra detectar conexões mortas antes de devolver do pool.

    Neon (e Postgres em geral) pode fechar conexões idle por timeout
    administrativo. Sem check, o pool entrega conexão morta e a query falha
    com `AdminShutdown` no meio da request. Custo: 1 round-trip extra
    (~5-20ms) por checkout, mas evita falhas raras catastróficas.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception:
        conn.close()
        raise


class PostgresRepository(ChatRepository):
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        self._pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={"autocommit": False, "row_factory": dict_row},
            open=True,
            # Recicla conexões idle a cada 5min (antes do Neon fechar por timeout)
            max_idle=300,
            # Reabre conexão se ficou >30min ociosa total
            max_lifetime=1800,
            # Pre-ping ANTES de devolver conexão do pool (detecta mortas)
            check=_check_connection,
        )

    def close(self) -> None:
        self._pool.close()

    # ──────────── helpers ────────────
    def _conn(self) -> Connection:
        return self._pool.connection()

    # ──────────── users ────────────
    def upsert_user(self, user: User) -> User:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat.users (id, email, display_name, store_name, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    email = EXCLUDED.email,
                    display_name = EXCLUDED.display_name,
                    store_name = EXCLUDED.store_name
                RETURNING id, email, display_name, store_name, created_at;
                """,
                (user.id, user.email, user.display_name, user.store_name, user.created_at),
            )
            row = cur.fetchone()
            conn.commit()
            return User(
                id=row["id"],
                email=row["email"],
                display_name=row["display_name"],
                store_name=row["store_name"],
                created_at=_to_aware(row["created_at"]),
            )

    def get_user(self, user_id: str) -> Optional[User]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, display_name, store_name, created_at FROM chat.users WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return User(
                id=row["id"],
                email=row["email"],
                display_name=row["display_name"],
                store_name=row["store_name"],
                created_at=_to_aware(row["created_at"]),
            )

    # ──────────── threads ────────────
    def create_thread(self, thread: ChatThread) -> ChatThread:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat.threads (id, user_id, title, archived, state_snapshot, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, user_id, title, archived, state_snapshot, created_at, updated_at;
                """,
                (
                    thread.id,
                    thread.user_id,
                    thread.title,
                    thread.archived,
                    _jsonb(thread.state_snapshot),
                    thread.created_at,
                    thread.updated_at,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return _row_to_thread(row)

    def get_thread(self, thread_id: str, user_id: str) -> Optional[ChatThread]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, user_id, title, archived, state_snapshot, created_at, updated_at
                  FROM chat.threads
                 WHERE id = %s AND user_id = %s;
                """,
                (thread_id, user_id),
            )
            row = cur.fetchone()
            return _row_to_thread(row) if row else None

    def list_threads(
        self, user_id: str, *,
        include_archived: bool = False,
        only_with_user_messages: bool = False,
    ) -> List[ChatThread]:
        sql = """
            SELECT t.id, t.user_id, t.title, t.archived, t.state_snapshot,
                   t.created_at, t.updated_at
              FROM chat.threads t
             WHERE t.user_id = %s
        """
        if not include_archived:
            sql += " AND t.archived = FALSE"
        if only_with_user_messages:
            # EXISTS é mais eficiente que JOIN+DISTINCT pra esse caso.
            sql += (
                " AND EXISTS (SELECT 1 FROM chat.messages m"
                " WHERE m.thread_id = t.id AND m.role = 'user')"
            )
        sql += " ORDER BY t.updated_at DESC LIMIT 200;"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (user_id,))
            return [_row_to_thread(r) for r in cur.fetchall()]

    def update_thread(self, thread: ChatThread) -> ChatThread:
        now = datetime.now(timezone.utc)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE chat.threads
                   SET title = %s,
                       archived = %s,
                       state_snapshot = %s,
                       updated_at = %s
                 WHERE id = %s AND user_id = %s
             RETURNING id, user_id, title, archived, state_snapshot, created_at, updated_at;
                """,
                (
                    thread.title,
                    thread.archived,
                    _jsonb(thread.state_snapshot),
                    now,
                    thread.id,
                    thread.user_id,
                ),
            )
            row = cur.fetchone()
            if not row:
                raise PermissionError("Thread não pertence ao usuário ou não existe")
            conn.commit()
            return _row_to_thread(row)

    def delete_thread(self, thread_id: str, user_id: str) -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM chat.threads WHERE id = %s AND user_id = %s;",
                (thread_id, user_id),
            )
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted

    # ──────────── messages ────────────
    def append_message(self, message: ChatMessage, *, user_id: str) -> ChatMessage:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM chat.threads WHERE id = %s AND user_id = %s;",
                (message.thread_id, user_id),
            )
            if not cur.fetchone():
                raise PermissionError("Thread não pertence ao usuário")

            cur.execute(
                """
                INSERT INTO chat.messages (id, thread_id, role, content, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, thread_id, role, content, metadata, created_at;
                """,
                (
                    message.id,
                    message.thread_id,
                    message.role.value if isinstance(message.role, MessageRole) else message.role,
                    message.content,
                    _jsonb(message.metadata),
                    message.created_at,
                ),
            )
            row = cur.fetchone()
            cur.execute(
                "UPDATE chat.threads SET updated_at = %s WHERE id = %s;",
                (datetime.now(timezone.utc), message.thread_id),
            )
            conn.commit()
            return _row_to_message(row)

    def list_messages(self, thread_id: str, user_id: str, *, limit: int = 200) -> List[ChatMessage]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.id, m.thread_id, m.role, m.content, m.metadata, m.created_at
                  FROM chat.messages m
                  JOIN chat.threads t ON t.id = m.thread_id
                 WHERE m.thread_id = %s AND t.user_id = %s
                 ORDER BY m.created_at
                 LIMIT %s;
                """,
                (thread_id, user_id, limit),
            )
            return [_row_to_message(r) for r in cur.fetchall()]

    # ──────────── quotes ────────────
    def create_quote(self, quote: Quote) -> Quote:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat.quotes (id, thread_id, user_id, status, search_request,
                                          raw_offers, presented_payload, approved_offer_id,
                                          pdf_path, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, thread_id, user_id, status, search_request, raw_offers,
                          presented_payload, approved_offer_id, pdf_path, created_at, updated_at;
                """,
                (
                    quote.id,
                    quote.thread_id,
                    quote.user_id,
                    quote.status.value if isinstance(quote.status, QuoteStatus) else quote.status,
                    _jsonb(quote.search_request),
                    _jsonb(quote.raw_offers),
                    _jsonb(quote.presented_payload),
                    quote.approved_offer_id,
                    quote.pdf_path,
                    quote.created_at,
                    quote.updated_at,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return _row_to_quote(row)

    def get_quote(self, quote_id: str, user_id: str) -> Optional[Quote]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, thread_id, user_id, status, search_request, raw_offers,
                       presented_payload, approved_offer_id, pdf_path, created_at, updated_at
                  FROM chat.quotes
                 WHERE id = %s AND user_id = %s;
                """,
                (quote_id, user_id),
            )
            row = cur.fetchone()
            return _row_to_quote(row) if row else None

    def update_quote_status(
        self,
        quote_id: str,
        user_id: str,
        status: QuoteStatus,
        *,
        approved_offer_id: Optional[str] = None,
        pdf_path: Optional[str] = None,
    ) -> Optional[Quote]:
        now = datetime.now(timezone.utc)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE chat.quotes
                   SET status = %s,
                       approved_offer_id = COALESCE(%s, approved_offer_id),
                       pdf_path = COALESCE(%s, pdf_path),
                       updated_at = %s
                 WHERE id = %s AND user_id = %s
             RETURNING id, thread_id, user_id, status, search_request, raw_offers,
                       presented_payload, approved_offer_id, pdf_path, created_at, updated_at;
                """,
                (
                    status.value if isinstance(status, QuoteStatus) else status,
                    approved_offer_id,
                    pdf_path,
                    now,
                    quote_id,
                    user_id,
                ),
            )
            row = cur.fetchone()
            if not row:
                return None
            conn.commit()
            return _row_to_quote(row)

    def list_quotes(self, user_id: str, *, status: Optional[QuoteStatus] = None) -> List[Quote]:
        if status is None:
            sql = """
                SELECT id, thread_id, user_id, status, search_request, raw_offers,
                       presented_payload, approved_offer_id, pdf_path, created_at, updated_at
                  FROM chat.quotes
                 WHERE user_id = %s
                 ORDER BY updated_at DESC LIMIT 200;
            """
            params: tuple = (user_id,)
        else:
            sql = """
                SELECT id, thread_id, user_id, status, search_request, raw_offers,
                       presented_payload, approved_offer_id, pdf_path, created_at, updated_at
                  FROM chat.quotes
                 WHERE user_id = %s AND status = %s
                 ORDER BY updated_at DESC LIMIT 200;
            """
            params = (user_id, status.value if isinstance(status, QuoteStatus) else status)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [_row_to_quote(r) for r in cur.fetchall()]


# ──────────── row → domain mappers ────────────

def _row_to_thread(row: dict) -> ChatThread:
    snap = row["state_snapshot"]
    if isinstance(snap, str):
        snap = json.loads(snap)
    return ChatThread(
        id=row["id"],
        user_id=row["user_id"],
        title=row["title"],
        archived=row["archived"],
        state_snapshot=snap or {},
        created_at=_to_aware(row["created_at"]),
        updated_at=_to_aware(row["updated_at"]),
    )


def _row_to_message(row: dict) -> ChatMessage:
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    return ChatMessage(
        id=row["id"],
        thread_id=row["thread_id"],
        role=MessageRole(row["role"]),
        content=row["content"],
        metadata=meta or {},
        created_at=_to_aware(row["created_at"]),
    )


def _row_to_quote(row: dict) -> Quote:
    def _maybe_json(v: Any, default: Any) -> Any:
        if v is None:
            return default
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return default
        return v

    return Quote(
        id=row["id"],
        thread_id=row["thread_id"],
        user_id=row["user_id"],
        status=QuoteStatus(row["status"]),
        search_request=_maybe_json(row["search_request"], {}),
        raw_offers=_maybe_json(row["raw_offers"], []),
        presented_payload=_maybe_json(row["presented_payload"], {}),
        approved_offer_id=row["approved_offer_id"],
        pdf_path=row["pdf_path"],
        created_at=_to_aware(row["created_at"]),
        updated_at=_to_aware(row["updated_at"]),
    )
