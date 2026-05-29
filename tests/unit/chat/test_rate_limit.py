"""Rate limit por usuário."""
import os
import unittest


class TestRateLimit(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["CHAT_RATE_LIMIT_PER_MIN"] = "3"
        os.environ["CHAT_RATE_LIMIT_SEARCH_PER_HOUR"] = "2"
        # Recarrega o módulo de config para reler env vars,
        # depois recarrega rate_limit para reimportar o singleton settings.
        import importlib
        from backend.app.chat import config as cfg_module
        importlib.reload(cfg_module)
        from backend.app.chat.security import rate_limit as rl_module
        importlib.reload(rl_module)
        self.limiter = rl_module.get_rate_limiter()

    def test_message_limit(self):
        from backend.app.chat.security.rate_limit import RateLimitExceeded
        for _ in range(3):
            self.limiter.check_message("u1")
        with self.assertRaises(RateLimitExceeded):
            self.limiter.check_message("u1")

    def test_per_user_isolated(self):
        for _ in range(3):
            self.limiter.check_message("u1")
        # u2 ainda pode
        self.limiter.check_message("u2")

    def test_search_limit_separate_from_message(self):
        from backend.app.chat.security.rate_limit import RateLimitExceeded
        # 3 mensagens não consomem orçamento de search
        for _ in range(3):
            self.limiter.check_message("u1")
        self.limiter.check_search("u1")
        self.limiter.check_search("u1")
        with self.assertRaises(RateLimitExceeded):
            self.limiter.check_search("u1")


if __name__ == "__main__":
    unittest.main()
