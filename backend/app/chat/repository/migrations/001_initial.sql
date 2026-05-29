-- ─────────────────────────────────────────────────────────────────
-- 001_initial.sql — schema base do produto chat
-- Compatível com Neon Postgres 17.
-- Roda idempotente (IF NOT EXISTS) — pode aplicar várias vezes.
-- ─────────────────────────────────────────────────────────────────

-- Extensões necessárias
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "citext";     -- email case-insensitive

-- Schema dedicado pra isolar do default e do `neon_auth.users_sync`.
CREATE SCHEMA IF NOT EXISTS chat;

-- ─── users ─────────────────────────────────────────────────────────
-- Perfil estendido do vendedor. Quando Neon Auth estiver ligado, `id`
-- aqui referencia `neon_auth.users_sync(id)` — mas mantemos como tabela
-- própria pra suportar tanto Neon Auth quanto DevAuth (mesma estrutura).
CREATE TABLE IF NOT EXISTS chat.users (
    id              TEXT PRIMARY KEY,
    email           CITEXT NOT NULL UNIQUE,
    display_name    TEXT,
    store_name      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── chat_threads ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat.threads (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES chat.users(id) ON DELETE CASCADE,
    title           TEXT NOT NULL DEFAULT 'Nova conversa',
    archived        BOOLEAN NOT NULL DEFAULT FALSE,
    state_snapshot  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_threads_user_updated
    ON chat.threads(user_id, updated_at DESC)
    WHERE archived = FALSE;

-- ─── chat_messages ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat.messages (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL REFERENCES chat.threads(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),
    content     TEXT NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_messages_thread_time
    ON chat.messages(thread_id, created_at);

-- ─── quotes ────────────────────────────────────────────────────────
-- Cotações salvas (propostas, aprovadas, ou expiradas).
-- search_request, raw_offers e presented_payload ficam em JSONB para
-- evoluir o schema sem migration toda vez que o domínio mudar.
CREATE TABLE IF NOT EXISTS chat.quotes (
    id                  TEXT PRIMARY KEY,
    thread_id           TEXT NOT NULL REFERENCES chat.threads(id) ON DELETE CASCADE,
    user_id             TEXT NOT NULL REFERENCES chat.users(id) ON DELETE CASCADE,
    status              TEXT NOT NULL CHECK (status IN ('proposed','refining','approved','expired','cancelled')),
    search_request      JSONB NOT NULL,
    raw_offers          JSONB NOT NULL DEFAULT '[]'::jsonb,
    presented_payload   JSONB NOT NULL DEFAULT '{}'::jsonb,
    approved_offer_id   TEXT,
    pdf_path            TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_quotes_user_status_updated
    ON chat.quotes(user_id, status, updated_at DESC);

-- ─── audit_log ─────────────────────────────────────────────────────
-- Trilha de auditoria para segurança e investigação.
-- Eventos como login, busca, aprovação, refusal por guardrail.
CREATE TABLE IF NOT EXISTS chat.audit_log (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT,                       -- pode ser NULL em pre-auth (login fail)
    thread_id   TEXT,
    event       TEXT NOT NULL,              -- 'login.ok', 'guardrail.refused', 'search.run', etc.
    severity    TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info','warn','error','security')),
    detail      JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip_address  INET,
    user_agent  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_user_time ON chat.audit_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_severity_time ON chat.audit_log(severity, created_at DESC)
    WHERE severity IN ('warn','error','security');
