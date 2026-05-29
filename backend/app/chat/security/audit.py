"""Audit log — trilha de segurança e investigação.

Escreve direto na tabela `chat.audit_log` quando Postgres estiver configurado;
em fallback, loga via `logging.warning` para Render/Railway/CloudWatch capturarem.

Eventos esperados (não exaustivo):
- `login.ok`, `login.fail`
- `register.ok`, `register.fail`
- `guardrail.input.blocked` (input filter)
- `guardrail.jailbreak.detected` (jailbreak detector)
- `guardrail.output.sanitized` (output filter trocou algo crítico)
- `rate_limit.exceeded`
- `search.run`, `search.refused`
- `quote.approved`, `quote.pdf.generated`
- `auth.token.invalid`

Severidades:
- `info` — operação normal (login, busca).
- `warn` — algo suspeito mas seguro (output sanitizado).
- `error` — falha técnica.
- `security` — tentativa hostil detectada (jailbreak, exfil, abuso).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, Optional

from backend.app.chat.config import settings
from backend.app.chat.repository.factory import get_repository

logger = logging.getLogger("chat.audit")


class AuditLogger:
    def __init__(self) -> None:
        self._use_db = settings.use_postgres

    def log(
        self,
        event: str,
        *,
        user_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        severity: str = "info",
        detail: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        detail = detail or {}
        if self._use_db:
            try:
                self._write_db(
                    event=event,
                    user_id=user_id,
                    thread_id=thread_id,
                    severity=severity,
                    detail=detail,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                return
            except Exception as e:
                logger.warning("audit DB failed (%s) — falling back to stderr", e)

        # Fallback: stderr estruturado.
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "user_id": user_id,
            "thread_id": thread_id,
            "severity": severity,
            "detail": detail,
            "ip": ip_address,
        }
        level_map = {"info": logging.INFO, "warn": logging.WARNING,
                     "error": logging.ERROR, "security": logging.WARNING}
        logger.log(level_map.get(severity, logging.INFO), "audit %s", json.dumps(record))

    def _write_db(
        self,
        *,
        event: str,
        user_id: Optional[str],
        thread_id: Optional[str],
        severity: str,
        detail: Dict[str, Any],
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> None:
        from psycopg.types.json import Jsonb

        repo = get_repository()
        # Acessamos o pool direto — repo expõe via duck typing
        pool = getattr(repo, "_pool", None)
        if pool is None:
            raise RuntimeError("Repositório atual não suporta audit em DB")
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat.audit_log
                    (user_id, thread_id, event, severity, detail, ip_address, user_agent)
                VALUES (%s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    user_id, thread_id, event, severity,
                    Jsonb(detail), ip_address, user_agent,
                ),
            )
            conn.commit()


@lru_cache(maxsize=1)
def get_audit_logger() -> AuditLogger:
    return AuditLogger()
