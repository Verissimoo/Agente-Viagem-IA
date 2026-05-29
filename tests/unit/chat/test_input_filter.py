"""Sanitização de input do usuário."""
import unittest

from backend.app.chat.security.input_filter import (
    InputViolation,
    sanitize_user_message,
)


class TestInputFilter(unittest.TestCase):
    def test_normal_message_passes(self):
        result = sanitize_user_message("Quero cotar GRU → LIS em 15 de junho")
        self.assertIn("GRU", result.text)
        self.assertEqual(result.original_length, len("Quero cotar GRU → LIS em 15 de junho"))

    def test_strips_control_chars(self):
        # NULL byte + escape sequence
        result = sanitize_user_message("Cotar\x00 GRU\x1b[31m → LIS")
        self.assertNotIn("\x00", result.text)
        self.assertNotIn("\x1b", result.text)

    def test_empty_raises(self):
        with self.assertRaises(InputViolation):
            sanitize_user_message("")
        with self.assertRaises(InputViolation):
            sanitize_user_message("   \n\t   ")

    def test_extremely_long_raises(self):
        huge = "a" * 100_000
        with self.assertRaises(InputViolation):
            sanitize_user_message(huge, max_chars=2000)

    def test_long_but_below_4x_truncates(self):
        text = "a" * 5000
        result = sanitize_user_message(text, max_chars=2000)
        self.assertLessEqual(len(result.text), 2000)


if __name__ == "__main__":
    unittest.main()
