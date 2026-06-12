-- ─────────────────────────────────────────────────────────────────
-- 002_validations.sql — validação interna (sistema vs. manual) + bug reports
-- Compatível com Neon Postgres. Idempotente (IF NOT EXISTS).
-- Aplicar: python -m backend.app.chat.repository.migrations.run
--
-- thread_id PROPOSITALMENTE sem REFERENCES/cascade: os registros de acurácia e
-- os bug reports SOBREVIVEM ao delete da thread (o histórico é o que importa).
-- system_offer carrega o snapshot da oferta do sistema → tabela autossuficiente.
-- ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chat.quote_validations (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    thread_id       TEXT NOT NULL,
    message_id      TEXT,
    offer_id        TEXT,
    kind            TEXT NOT NULL,
    system_offer    JSONB NOT NULL DEFAULT '{}',
    found_airline   TEXT,
    found_program   TEXT,
    emission_method TEXT,
    found_value_brl NUMERIC,
    found_miles     INTEGER,
    observations    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_qval_user_created
    ON chat.quote_validations (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qval_thread
    ON chat.quote_validations (thread_id);
-- Idempotência do clique "validado" (user + offer + kind únicos).
CREATE UNIQUE INDEX IF NOT EXISTS uq_qval_user_offer_kind
    ON chat.quote_validations (user_id, offer_id, kind)
    WHERE offer_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS chat.bug_reports (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    thread_id   TEXT NOT NULL,
    description TEXT NOT NULL,
    context     JSONB NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bug_user_created
    ON chat.bug_reports (user_id, created_at DESC);
