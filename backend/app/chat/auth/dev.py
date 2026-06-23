"""DevAuthProvider — login/registro local com hash, sem dependência externa.

Existe pra destravar UX completa de auth (login/registro/sessão) enquanto
Supabase não está plugado. Salva usuários em memória + arquivo JSON opcional
controlado por `CHAT_DEV_AUTH_FILE`. NUNCA usar em produção.

Tokens são JWTs HS256 assinados por `CHAT_DEV_AUTH_SECRET` (default randômico
por boot — ou seja, restart invalida sessões — comportamento desejado em dev).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Dict, Optional
from uuid import uuid4

from backend.app.chat.auth.interface import AuthError, AuthProvider, AuthSession
from backend.app.chat.domain.models import User
from backend.app.chat.repository import get_repository


def _b64url_encode(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return urlsafe_b64decode(s + pad)


def _hash_password(password: str, *, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"{_b64url_encode(salt)}.{_b64url_encode(digest)}"


def _verify_password(password: str, hashed: str) -> bool:
    try:
        salt_b64, digest_b64 = hashed.split(".", 1)
        salt = _b64url_decode(salt_b64)
        expected = _b64url_decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
        return hmac.compare_digest(expected, actual)
    except Exception:
        return False


class _JWT:
    """Mini-JWT HS256. Evita dependência de pyjwt no boot mínimo."""

    @staticmethod
    def sign(payload: dict, secret: str) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{h}.{p}".encode("ascii")
        sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        return f"{h}.{p}.{_b64url_encode(sig)}"

    @staticmethod
    def verify(token: str, secret: str) -> dict:
        try:
            h, p, s = token.split(".")
        except ValueError as e:
            raise AuthError("Token malformado") from e
        signing_input = f"{h}.{p}".encode("ascii")
        expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(s)):
            raise AuthError("Assinatura inválida")
        payload = json.loads(_b64url_decode(p))
        if payload.get("exp", 0) < int(time.time()):
            raise AuthError("Token expirado")
        return payload


class DevAuthProvider(AuthProvider):
    _TOKEN_TTL_S = 60 * 60 * 24 * 7  # 7 dias

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._secret = os.getenv("CHAT_DEV_AUTH_SECRET") or secrets.token_urlsafe(32)
        # email → {"id", "password_hash", "display_name", "store_name"}
        self._accounts: Dict[str, dict] = {}
        self._file = os.getenv("CHAT_DEV_AUTH_FILE", "")
        # user_ids já garantidos em chat.users (evita SELECT por request).
        self._provisioned: set = set()
        self._load()

    def _load(self) -> None:
        if not self._file or not os.path.exists(self._file):
            return
        try:
            with open(self._file, encoding="utf-8") as fh:
                self._accounts = json.load(fh)
        except Exception:
            self._accounts = {}

    def _persist(self) -> None:
        if not self._file:
            return
        try:
            with open(self._file, "w", encoding="utf-8") as fh:
                json.dump(self._accounts, fh)
        except Exception:
            pass

    def _issue_token(self, user_id: str, email: str) -> str:
        now = int(time.time())
        payload = {
            "sub": user_id,
            "email": email,
            "iat": now,
            "exp": now + self._TOKEN_TTL_S,
        }
        return _JWT.sign(payload, self._secret)

    def login(self, email: str, password: str) -> AuthSession:
        email_norm = (email or "").strip().lower()
        # FONTE DE VERDADE: banco (Postgres). O arquivo/memória é só fallback de
        # compat (contas locais antigas) — em produção o Railway tem FS efêmero,
        # então a credencial PRECISA estar no banco pra sobreviver a redeploy.
        acct = None
        try:
            acct = get_repository().get_auth_account(email_norm)
        except Exception:
            acct = None
        if acct is None:
            with self._lock:
                acct = self._accounts.get(email_norm)
        if not acct or not _verify_password(password, acct["password_hash"]):
            raise AuthError("Credenciais inválidas")
        token = self._issue_token(acct["id"], email_norm)
        return AuthSession(
            user_id=acct["id"],
            email=email_norm,
            access_token=token,
            display_name=acct.get("display_name"),
            store_name=acct.get("store_name"),
        )

    def register(
        self,
        email: str,
        password: str,
        *,
        display_name: Optional[str] = None,
        store_name: Optional[str] = None,
    ) -> AuthSession:
        email_norm = (email or "").strip().lower()
        if "@" not in email_norm or len(email_norm) < 5:
            raise AuthError("Email inválido")
        if not password or len(password) < 8:
            raise AuthError("Senha precisa ter pelo menos 8 caracteres")

        repo = get_repository()

        # Já existe credencial com senha? Conflito real.
        try:
            if repo.get_auth_account(email_norm) is not None:
                raise AuthError("Email já cadastrado")
        except AuthError:
            raise
        except Exception:
            pass  # banco indisponível → segue no fallback de arquivo abaixo

        with self._lock:
            if email_norm in self._accounts and not self._db_ok():
                raise AuthError("Email já cadastrado")

        # RECUPERAÇÃO: se já existe um PERFIL com esse email (criado antes da
        # persistência de senha — FS efêmero do Railway apagou a credencial),
        # reaproveita o MESMO user_id pra preservar as threads/cotações dele.
        user_id = uuid4().hex
        try:
            legacy = repo.get_user_by_email(email_norm)
            if legacy is not None:
                user_id = legacy.id
        except Exception:
            pass

        password_hash = _hash_password(password)

        # Persiste a credencial no BANCO (sobrevive a redeploy) + perfil.
        try:
            repo.upsert_auth_account(
                user_id=user_id, email=email_norm, password_hash=password_hash,
                display_name=display_name, store_name=store_name,
            )
        except Exception as e:
            # Sem banco (dev local sem DATABASE_URL): cai no arquivo.
            from backend.app.chat.auth.interface import AuthError as _AE
            if not self._file:
                raise _AE("Cadastro indisponível (sem banco). Configure DATABASE_URL.") from e

        # Espelha em memória/arquivo (compat local + cache no mesmo processo).
        with self._lock:
            self._accounts[email_norm] = {
                "id": user_id, "password_hash": password_hash,
                "display_name": display_name, "store_name": store_name,
            }
            self._persist()

        token = self._issue_token(user_id, email_norm)
        self._provisioned.add(user_id)
        return AuthSession(
            user_id=user_id,
            email=email_norm,
            access_token=token,
            display_name=display_name,
            store_name=store_name,
        )

    def _db_ok(self) -> bool:
        """True se o repositório responde (tem banco). Decide se o conflito de
        email do arquivo local deve bloquear o registro."""
        try:
            get_repository().get_auth_account("__healthcheck__@none.local")
            return True
        except Exception:
            return False

    # ──────────── reset de senha (token por e-mail) ────────────
    _RESET_TTL_S = 60 * 60  # 1 hora

    def request_password_reset(self, email: str) -> Optional[str]:
        """Gera token de reset, persiste o HASH e envia o link por e-mail.

        Retorna o token cru (usado nos testes); em produção o chamador descarta
        e responde genérico — nunca revela se o e-mail existe. Se não houver
        perfil pra esse e-mail, retorna None (e nada é enviado)."""
        from datetime import datetime, timedelta, timezone

        from backend.app.chat.notify.email import send_password_reset_email

        email_norm = (email or "").strip().lower()
        repo = get_repository()
        user = None
        try:
            user = repo.get_user_by_email(email_norm)
        except Exception:
            user = None
        if user is None:
            return None  # sem conta → caller responde genérico mesmo assim

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._RESET_TTL_S)
        try:
            repo.create_password_reset(
                reset_id=uuid4().hex, user_id=user.id, email=email_norm,
                token_hash=token_hash, expires_at=expires_at,
            )
        except Exception:
            return None

        base = os.getenv("PASSWORD_RESET_URL_BASE", "").strip().rstrip("/")
        reset_url = f"{base}?token={token}" if base else f"/reset-password?token={token}"
        send_password_reset_email(email_norm, reset_url)
        return token

    def reset_password(self, token: str, new_password: str) -> AuthSession:
        from datetime import datetime, timezone

        if not new_password or len(new_password) < 8:
            raise AuthError("Senha precisa ter pelo menos 8 caracteres")
        if not token:
            raise AuthError("Token de reset ausente")

        repo = get_repository()
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        rec = None
        try:
            rec = repo.get_password_reset(token_hash)
        except Exception:
            rec = None
        if not rec:
            raise AuthError("Link de reset inválido")
        if rec.get("used_at"):
            raise AuthError("Este link de reset já foi usado")
        exp = rec.get("expires_at")
        if isinstance(exp, datetime) and exp < datetime.now(timezone.utc):
            raise AuthError("Link de reset expirado — peça um novo")

        email_norm = (rec.get("email") or "").strip().lower()
        user_id = rec["user_id"]
        password_hash = _hash_password(new_password)
        repo.upsert_auth_account(
            user_id=user_id, email=email_norm, password_hash=password_hash,
        )
        try:
            repo.mark_password_reset_used(rec["id"])
        except Exception:
            pass

        with self._lock:
            existing = self._accounts.get(email_norm, {})
            existing.update({"id": user_id, "email": email_norm, "password_hash": password_hash})
            self._accounts[email_norm] = existing
            self._persist()

        token_jwt = self._issue_token(user_id, email_norm)
        self._provisioned.add(user_id)
        return AuthSession(
            user_id=user_id, email=email_norm, access_token=token_jwt,
            display_name=existing.get("display_name"),
            store_name=existing.get("store_name"),
        )

    def set_password_direct(self, email: str, new_password: str) -> AuthSession:
        """Reset SIMPLES (sem e-mail/token): define a senha de uma conta pelo
        e-mail e devolve sessão autenticada. Interino até o SMTP entrar.

        Aceita inclusive contas legadas com password_hash NULL (criadas antes da
        persistência de senha) — é justamente o que destrava o login delas."""
        email_norm = (email or "").strip().lower()
        if not new_password or len(new_password) < 8:
            raise AuthError("Senha precisa ter pelo menos 8 caracteres")

        repo = get_repository()
        user = None
        try:
            user = repo.get_user_by_email(email_norm)
        except Exception:
            user = None
        if user is None:
            raise AuthError("Conta não encontrada")

        password_hash = _hash_password(new_password)
        repo.upsert_auth_account(
            user_id=user.id, email=email_norm, password_hash=password_hash,
            display_name=user.display_name, store_name=user.store_name,
        )
        with self._lock:
            existing = self._accounts.get(email_norm, {})
            existing.update({"id": user.id, "email": email_norm, "password_hash": password_hash})
            self._accounts[email_norm] = existing
            self._persist()

        token = self._issue_token(user.id, email_norm)
        self._provisioned.add(user.id)
        return AuthSession(
            user_id=user.id, email=email_norm, access_token=token,
            display_name=user.display_name, store_name=user.store_name,
        )

    def verify_token(self, token: str) -> AuthSession:
        payload = _JWT.verify(token, self._secret)
        email = payload.get("email", "")
        user_id = payload.get("sub", "")
        if not user_id:
            raise AuthError("Token sem identidade")
        # Garante o perfil em chat.users (igual ao neon.py). Sem isso, um token
        # válido cujo usuário sumiu do banco (reset/migração, FS efêmero do
        # Railway) quebrava o create_thread com ForeignKeyViolation.
        self._ensure_user(user_id, email)
        return AuthSession(user_id=user_id, email=email, access_token=token)

    def _ensure_user(self, user_id: str, email: str) -> None:
        if user_id in self._provisioned:
            return
        try:
            repo = get_repository()
            if repo.get_user(user_id) is None:
                repo.upsert_user(User(id=user_id, email=email or f"{user_id}@unknown.local"))
            self._provisioned.add(user_id)
        except Exception:
            # Nunca derruba a auth por causa do provisionamento; tenta de novo
            # no próximo request (não marca como provisionado).
            pass
