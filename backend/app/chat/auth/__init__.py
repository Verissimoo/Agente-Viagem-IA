"""Autenticação do produto chat.

Interface única (`AuthProvider`) tem duas implementações:
- `DevAuthProvider`: login via email/senha em memória. Default em dev.
- `SupabaseAuthProvider`: valida JWT emitido pelo Supabase. Ativado quando
  `SUPABASE_JWT_SECRET` estiver presente.

Token format (qualquer impl): JWT Bearer com `sub` = user_id.
"""
from backend.app.chat.auth.factory import get_auth_provider
from backend.app.chat.auth.interface import AuthError, AuthProvider, AuthSession

__all__ = ["AuthError", "AuthProvider", "AuthSession", "get_auth_provider"]
