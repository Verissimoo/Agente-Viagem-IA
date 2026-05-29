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

        with self._lock:
            if email_norm in self._accounts:
                raise AuthError("Email já cadastrado")
            user_id = uuid4().hex
            self._accounts[email_norm] = {
                "id": user_id,
                "password_hash": _hash_password(password),
                "display_name": display_name,
                "store_name": store_name,
            }
            self._persist()
            token = self._issue_token(user_id, email_norm)

        # Provisiona perfil no repositório
        get_repository().upsert_user(
            User(
                id=user_id,
                email=email_norm,
                display_name=display_name,
                store_name=store_name,
            )
        )

        return AuthSession(
            user_id=user_id,
            email=email_norm,
            access_token=token,
            display_name=display_name,
            store_name=store_name,
        )

    def verify_token(self, token: str) -> AuthSession:
        payload = _JWT.verify(token, self._secret)
        email = payload.get("email", "")
        user_id = payload.get("sub", "")
        if not user_id:
            raise AuthError("Token sem identidade")
        return AuthSession(user_id=user_id, email=email, access_token=token)
