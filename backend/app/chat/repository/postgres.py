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
    BugReport,
    ChatMessage,
    ChatThread,
    MessageRole,
    Quote,
    QuoteStatus,
    QuoteValidation,
    ValidationKind,
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
        self._ensure_auth_schema()

    def _ensure_auth_schema(self) -> None:
        """Garante a coluna de credencial (DevAuth) sem depender de migration
        manual. As migrations não rodam no deploy do Railway (Procfile só sobe o
        uvicorn); sem isso, login/registro persistente quebraria em produção.
        Idempotente (IF NOT EXISTS)."""
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "ALTER TABLE chat.users ADD COLUMN IF NOT EXISTS password_hash TEXT"
                )
                conn.commit()
        except Exception as e:
            logger.warning("ensure_auth_schema falhou (segue sem): %s", e)

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

    # ──────────── auth (DevAuth persistente) ────────────
    def get_user_by_email(self, email: str) -> Optional[User]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, display_name, store_name, created_at FROM chat.users WHERE email = %s",
                (email,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return User(
                id=row["id"], email=row["email"],
                display_name=row["display_name"], store_name=row["store_name"],
                created_at=_to_aware(row["created_at"]),
            )

    def get_auth_account(self, email: str) -> Optional[dict]:
        """Credencial pra login: só retorna se houver senha definida (password_hash
        não-nulo). Perfil legado sem senha (criado antes da persistência) volta None
        aqui — o registro reaproveita o id pra preservar threads/quotes."""
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, password_hash, display_name, store_name "
                "FROM chat.users WHERE email = %s AND password_hash IS NOT NULL",
                (email,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row["id"], "email": row["email"],
                "password_hash": row["password_hash"],
                "display_name": row["display_name"], "store_name": row["store_name"],
            }

    def upsert_auth_account(self, *, user_id: str, email: str, password_hash: str,
                            display_name: Optional[str] = None,
                            store_name: Optional[str] = None) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat.users (id, email, display_name, store_name, password_hash, created_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    email = EXCLUDED.email,
                    display_name = COALESCE(EXCLUDED.display_name, chat.users.display_name),
                    store_name = COALESCE(EXCLUDED.store_name, chat.users.store_name),
                    password_hash = EXCLUDED.password_hash;
                """,
                (user_id, email, display_name, store_name, password_hash),
            )
            conn.commit()

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

    # ──────────── validações internas ────────────
    def create_validation(self, validation: QuoteValidation) -> QuoteValidation:
        v = validation
        kind = v.kind.value if isinstance(v.kind, ValidationKind) else v.kind
        with self._conn() as conn, conn.cursor() as cur:
            # Idempotência: mesmo user+offer+kind → devolve o existente (ON CONFLICT).
            cur.execute(
                """
                INSERT INTO chat.quote_validations
                    (id, user_id, thread_id, message_id, offer_id, kind, system_offer,
                     found_airline, found_program, emission_method, found_value_brl,
                     found_miles, observations, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (user_id, offer_id, kind) WHERE offer_id IS NOT NULL
                DO NOTHING
                RETURNING *;
                """,
                (v.id, v.user_id, v.thread_id, v.message_id, v.offer_id, kind,
                 _jsonb(v.system_offer), v.found_airline, v.found_program,
                 v.emission_method, v.found_value_brl, v.found_miles, v.observations,
                 v.created_at),
            )
            row = cur.fetchone()
            conn.commit()
            if row:
                return _row_to_validation(row)
            # Conflito → já existia; busca e devolve.
            cur.execute(
                "SELECT * FROM chat.quote_validations "
                "WHERE user_id=%s AND offer_id=%s AND kind=%s LIMIT 1;",
                (v.user_id, v.offer_id, kind),
            )
            existing = cur.fetchone()
            return _row_to_validation(existing) if existing else v

    def list_validations(
        self, user_id: str, *, kind: Optional[ValidationKind] = None,
        limit: int = 200, offset: int = 0,
    ) -> List[QuoteValidation]:
        sql = "SELECT * FROM chat.quote_validations WHERE user_id=%s"
        params: list = [user_id]
        if kind is not None:
            sql += " AND kind=%s"
            params.append(kind.value if isinstance(kind, ValidationKind) else kind)
        sql += " ORDER BY created_at DESC LIMIT %s OFFSET %s;"
        params += [limit, offset]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return [_row_to_validation(r) for r in cur.fetchall()]

    def list_validations_by_thread(self, thread_id: str, user_id: str) -> List[QuoteValidation]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM chat.quote_validations "
                "WHERE thread_id=%s AND user_id=%s ORDER BY created_at DESC;",
                (thread_id, user_id),
            )
            return [_row_to_validation(r) for r in cur.fetchall()]

    def validation_stats(self, user_id: str) -> dict:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  COUNT(*)                                              AS total,
                  COUNT(*) FILTER (WHERE kind='validated')              AS validated,
                  COUNT(*) FILTER (WHERE kind='corrected')              AS corrected,
                  AVG(COALESCE((system_offer->>'equivalent_brl')::numeric,
                               (system_offer->>'price_brl')::numeric) - found_value_brl)
                    FILTER (WHERE kind='corrected' AND found_value_brl IS NOT NULL)
                                                                        AS avg_delta
                  FROM chat.quote_validations WHERE user_id=%s;
                """,
                (user_id,),
            )
            agg = cur.fetchone() or {}
            cur.execute(
                "SELECT emission_method AS k, COUNT(*) AS n FROM chat.quote_validations "
                "WHERE user_id=%s AND kind='corrected' AND emission_method IS NOT NULL "
                "GROUP BY emission_method;",
                (user_id,),
            )
            by_method = {r["k"]: int(r["n"]) for r in cur.fetchall()}
            cur.execute(
                "SELECT found_airline AS k, COUNT(*) AS n FROM chat.quote_validations "
                "WHERE user_id=%s AND kind='corrected' AND found_airline IS NOT NULL "
                "GROUP BY found_airline;",
                (user_id,),
            )
            by_airline = {r["k"]: int(r["n"]) for r in cur.fetchall()}
        validated = int(agg.get("validated") or 0)
        corrected = int(agg.get("corrected") or 0)
        denom = validated + corrected
        avg_delta = agg.get("avg_delta")
        return {
            "total": int(agg.get("total") or 0),
            "validated_count": validated,
            "corrected_count": corrected,
            "accuracy_pct": round(100.0 * validated / denom, 1) if denom else 0.0,
            "avg_delta_brl": round(float(avg_delta), 2) if avg_delta is not None else None,
            "by_method": by_method,
            "by_airline": by_airline,
        }

    # ──────────── bug reports ────────────
    def create_bug_report(self, report: BugReport) -> BugReport:
        b = report
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat.bug_reports
                    (id, user_id, thread_id, description, context, status, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *;
                """,
                (b.id, b.user_id, b.thread_id, b.description, _jsonb(b.context),
                 b.status, b.created_at),
            )
            row = cur.fetchone()
            conn.commit()
            return _row_to_bug(row) if row else b

    def list_bug_reports(
        self, user_id: str, *, status: Optional[str] = None, limit: int = 200,
    ) -> List[BugReport]:
        sql = "SELECT * FROM chat.bug_reports WHERE user_id=%s"
        params: list = [user_id]
        if status is not None:
            sql += " AND status=%s"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT %s;"
        params.append(limit)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return [_row_to_bug(r) for r in cur.fetchall()]


# ──────────── row → domain mappers ────────────

def _row_to_validation(row: dict) -> QuoteValidation:
    so = row.get("system_offer")
    if isinstance(so, str):
        so = json.loads(so)
    return QuoteValidation(
        id=row["id"], user_id=row["user_id"], thread_id=row["thread_id"],
        message_id=row.get("message_id"), offer_id=row.get("offer_id"),
        kind=ValidationKind(row["kind"]), system_offer=so or {},
        found_airline=row.get("found_airline"), found_program=row.get("found_program"),
        emission_method=row.get("emission_method"),
        found_value_brl=float(row["found_value_brl"]) if row.get("found_value_brl") is not None else None,
        found_miles=row.get("found_miles"), observations=row.get("observations"),
        created_at=_to_aware(row["created_at"]),
    )


def _row_to_bug(row: dict) -> BugReport:
    ctx = row.get("context")
    if isinstance(ctx, str):
        ctx = json.loads(ctx)
    return BugReport(
        id=row["id"], user_id=row["user_id"], thread_id=row["thread_id"],
        description=row["description"], context=ctx or {}, status=row["status"],
        created_at=_to_aware(row["created_at"]),
    )

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
