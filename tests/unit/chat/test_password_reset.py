"""Fluxo de reset de senha por e-mail (DevAuthProvider + repo in-memory).

Sem SMTP configurado, o e-mail só loga o link — o token é devolvido por
`request_password_reset` pra simular o clique no link.
"""
import os
import unittest

from backend.app.chat.auth.dev import DevAuthProvider
from backend.app.chat.auth.interface import AuthError


class TestPasswordReset(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["CHAT_DEV_AUTH_FILE"] = ""
        os.environ.pop("PASSWORD_RESET_URL_BASE", None)
        from backend.app.chat.repository import factory as repo_factory
        repo_factory.get_repository.cache_clear()
        self.auth = DevAuthProvider()
        self.auth.register("vendedor@pcd.com", "senha-antiga-123")

    def test_reset_troca_a_senha(self):
        token = self.auth.request_password_reset("vendedor@pcd.com")
        self.assertTrue(token, "deveria gerar token pra conta existente")

        session = self.auth.reset_password(token, "senha-nova-456")
        self.assertEqual(session.email, "vendedor@pcd.com")

        # Senha nova funciona; a antiga não.
        self.assertTrue(self.auth.login("vendedor@pcd.com", "senha-nova-456").access_token)
        with self.assertRaises(AuthError):
            self.auth.login("vendedor@pcd.com", "senha-antiga-123")

    def test_email_inexistente_nao_vaza(self):
        # Não levanta nem gera token — caller responde genérico mesmo assim.
        self.assertIsNone(self.auth.request_password_reset("ninguem@lugar.com"))

    def test_token_e_uso_unico(self):
        token = self.auth.request_password_reset("vendedor@pcd.com")
        self.auth.reset_password(token, "outra-senha-789")
        with self.assertRaises(AuthError):
            self.auth.reset_password(token, "mais-uma-senha-000")

    def test_token_invalido_rejeitado(self):
        with self.assertRaises(AuthError):
            self.auth.reset_password("token-que-nao-existe", "senha-valida-123")

    def test_senha_curta_rejeitada(self):
        token = self.auth.request_password_reset("vendedor@pcd.com")
        with self.assertRaises(AuthError):
            self.auth.reset_password(token, "curta")

    # ─── Reset SIMPLES (sem e-mail) ────────────────────────────────
    def test_set_password_direct_troca_e_autentica(self):
        session = self.auth.set_password_direct("vendedor@pcd.com", "senha-direta-123")
        self.assertEqual(session.email, "vendedor@pcd.com")
        self.assertTrue(session.access_token)
        self.assertTrue(self.auth.login("vendedor@pcd.com", "senha-direta-123").access_token)
        with self.assertRaises(AuthError):
            self.auth.login("vendedor@pcd.com", "senha-antiga-123")

    def test_set_password_direct_conta_inexistente(self):
        with self.assertRaises(AuthError):
            self.auth.set_password_direct("ninguem@lugar.com", "senha-valida-123")

    def test_set_password_direct_senha_curta(self):
        with self.assertRaises(AuthError):
            self.auth.set_password_direct("vendedor@pcd.com", "curta")


if __name__ == "__main__":
    unittest.main()
