"""Fluxo do DevAuthProvider — register, login, verify."""
import unittest

from backend.app.chat.auth.dev import DevAuthProvider
from backend.app.chat.auth.interface import AuthError


class TestDevAuthProvider(unittest.TestCase):
    def setUp(self) -> None:
        # Isola estado entre testes: sem arquivo persistido e repo zerado.
        import os
        os.environ["CHAT_DEV_AUTH_FILE"] = ""
        from backend.app.chat.repository import factory as repo_factory
        from backend.app.chat.repository.memory import InMemoryRepository
        repo_factory.get_repository.cache_clear()
        # Força fábrica a recriar com repo zerado (sem DATABASE_URL no test).
        self.auth = DevAuthProvider()

    def test_register_and_login(self):
        session = self.auth.register("vendedor@pcd.com", "senha-forte-123")
        self.assertEqual(session.email, "vendedor@pcd.com")
        self.assertTrue(session.access_token)

        login_session = self.auth.login("vendedor@pcd.com", "senha-forte-123")
        self.assertEqual(login_session.user_id, session.user_id)

    def test_login_wrong_password(self):
        self.auth.register("a@b.com", "abc12345")
        with self.assertRaises(AuthError):
            self.auth.login("a@b.com", "wrong-password")

    def test_register_duplicate(self):
        self.auth.register("a@b.com", "abc12345")
        with self.assertRaises(AuthError):
            self.auth.register("a@b.com", "abc12345")

    def test_short_password_rejected(self):
        with self.assertRaises(AuthError):
            self.auth.register("a@b.com", "short")

    def test_verify_token_roundtrip(self):
        s = self.auth.register("test@x.com", "abcdef12345")
        verified = self.auth.verify_token(s.access_token)
        self.assertEqual(verified.user_id, s.user_id)

    def test_verify_garbage_token(self):
        with self.assertRaises(AuthError):
            self.auth.verify_token("garbage")


if __name__ == "__main__":
    unittest.main()
