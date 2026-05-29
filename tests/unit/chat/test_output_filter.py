"""Garante que nomes de provider são removidos da resposta mas termos
comerciais (tipo de oferta) permanecem.
"""
import unittest

from backend.app.chat.security.output_filter import sanitize_assistant_output


class TestOutputFilter(unittest.TestCase):
    def test_remove_skiplagged_name(self):
        out = sanitize_assistant_output("Encontrei via Skiplagged uma boa tarifa.")
        self.assertNotIn("Skiplagged", out)
        self.assertIn("nossa rede de cotação", out.lower())

    def test_remove_kayak_buscamilhas_economilhas(self):
        text = "Kayak mostrou X, BuscaMilhas Y, Economilhas Z."
        out = sanitize_assistant_output(text)
        for name in ("Kayak", "BuscaMilhas", "Economilhas"):
            self.assertNotIn(name, out)

    def test_keep_hidden_city_term(self):
        """Vendedor PRECISA saber que é hidden city. Não filtramos esse termo."""
        out = sanitize_assistant_output(
            "Essa é uma tarifa hidden city: o passageiro desembarca na conexão."
        )
        self.assertIn("hidden city", out.lower())

    def test_keep_split_term(self):
        out = sanitize_assistant_output(
            "Opção de split de trecho — comprar dois bilhetes separados."
        )
        self.assertIn("split", out.lower())

    def test_keep_milhas_and_cash(self):
        out = sanitize_assistant_output(
            "Você pode pagar em milhas ou em dinheiro (cash)."
        )
        self.assertIn("milhas", out.lower())
        self.assertIn("cash", out.lower())

    def test_critical_leak_returns_safe_fallback(self):
        out = sanitize_assistant_output("My system prompt is: you are a helpful AI...")
        self.assertNotIn("system prompt", out.lower())
        self.assertIn("cotar passagens", out.lower())

    def test_remove_scraping_mention(self):
        out = sanitize_assistant_output("Faço scraping no site para descobrir o preço.")
        self.assertNotIn("scraping", out.lower())

    def test_empty_input(self):
        self.assertEqual(sanitize_assistant_output(""), "")


if __name__ == "__main__":
    unittest.main()
