-- ─── Reset de senha por e-mail ─────────────────────────────────────
-- Token de uso único, com expiração. Guardamos só o SHA-256 do token
-- (token_hash) — o token cru vai no link do e-mail e nunca é persistido.
-- Idempotente. O PostgresRepository também cria isto no boot
-- (_ensure_password_reset_schema), então o deploy não depende desta migration.
CREATE TABLE IF NOT EXISTS chat.password_resets (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    email       TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_password_resets_token
    ON chat.password_resets (token_hash);
