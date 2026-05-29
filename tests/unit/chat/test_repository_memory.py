"""Cobertura do InMemoryRepository — em particular isolamento por usuário."""
import unittest

from backend.app.chat.domain.models import (
    ChatMessage,
    ChatThread,
    MessageRole,
    Quote,
    QuoteStatus,
    User,
)
from backend.app.chat.repository.memory import InMemoryRepository


class TestInMemoryRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = InMemoryRepository()
        self.repo.upsert_user(User(id="u1", email="a@b.com"))
        self.repo.upsert_user(User(id="u2", email="c@d.com"))

    def test_isolation_thread_get(self):
        t = self.repo.create_thread(ChatThread(user_id="u1", title="x"))
        self.assertIsNotNone(self.repo.get_thread(t.id, "u1"))
        # u2 NÃO vê a thread de u1
        self.assertIsNone(self.repo.get_thread(t.id, "u2"))

    def test_isolation_message_append_blocked(self):
        t = self.repo.create_thread(ChatThread(user_id="u1", title="x"))
        msg = ChatMessage(thread_id=t.id, role=MessageRole.USER, content="oi")
        with self.assertRaises(PermissionError):
            self.repo.append_message(msg, user_id="u2")

    def test_isolation_message_list_returns_empty_for_wrong_user(self):
        t = self.repo.create_thread(ChatThread(user_id="u1", title="x"))
        self.repo.append_message(
            ChatMessage(thread_id=t.id, role=MessageRole.USER, content="oi"),
            user_id="u1",
        )
        self.assertEqual(self.repo.list_messages(t.id, "u2"), [])

    def test_quote_status_update(self):
        t = self.repo.create_thread(ChatThread(user_id="u1", title="x"))
        q = self.repo.create_quote(Quote(
            thread_id=t.id, user_id="u1", search_request={},
        ))
        updated = self.repo.update_quote_status(
            q.id, "u1", QuoteStatus.APPROVED, approved_offer_id="o_abc", pdf_path="/tmp/x.pdf",
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, QuoteStatus.APPROVED)
        self.assertEqual(updated.approved_offer_id, "o_abc")

    def test_quote_isolation(self):
        t = self.repo.create_thread(ChatThread(user_id="u1", title="x"))
        q = self.repo.create_quote(Quote(
            thread_id=t.id, user_id="u1", search_request={},
        ))
        self.assertIsNone(self.repo.get_quote(q.id, "u2"))


if __name__ == "__main__":
    unittest.main()
