-- ─── DevAuth: credencial persistente ───────────────────────────────
-- Antes, a senha (password_hash) vivia só num arquivo JSON local
-- (CHAT_DEV_AUTH_FILE). No Railway o filesystem é EFÊMERO → todo redeploy
-- apagava o arquivo e as contas antigas "sumiam" (login dava 401 mesmo com
-- a senha certa). Agora a credencial vive aqui, junto do perfil.
-- Idempotente. O PostgresRepository também roda este ALTER no boot
-- (_ensure_auth_schema), então o deploy não depende de migration manual.
ALTER TABLE chat.users ADD COLUMN IF NOT EXISTS password_hash TEXT;
