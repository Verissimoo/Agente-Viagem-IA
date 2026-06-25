"""Cancelamento cooperativo de cotação (item C)."""
import unittest

from backend.app.ai.agents import cancellation as c


class TestCancellation(unittest.TestCase):
    def setUp(self) -> None:
        c._CANCELLED.clear()

    def tearDown(self) -> None:
        c._CANCELLED.clear()

    def test_request_e_is_cancelled(self):
        self.assertFalse(c.is_cancelled("t1"))
        c.request_cancel("t1")
        self.assertTrue(c.is_cancelled("t1"))

    def test_clear_cancel(self):
        c.request_cancel("t1")
        c.clear_cancel("t1")
        self.assertFalse(c.is_cancelled("t1"))

    def test_threads_independentes(self):
        c.request_cancel("t1")
        self.assertTrue(c.is_cancelled("t1"))
        self.assertFalse(c.is_cancelled("t2"))

    def test_none_e_vazio_nao_quebram(self):
        self.assertFalse(c.is_cancelled(None))
        self.assertFalse(c.is_cancelled(""))
        c.request_cancel("")      # no-op
        c.clear_cancel(None)      # no-op

    def test_should_cancel_current_via_contextvar(self):
        token = c.current_thread.set("t1")
        try:
            self.assertFalse(c.should_cancel_current())
            c.request_cancel("t1")
            self.assertTrue(c.should_cancel_current())
        finally:
            c.current_thread.reset(token)


if __name__ == "__main__":
    unittest.main()
