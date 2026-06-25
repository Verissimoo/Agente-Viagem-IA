"""Higiene de estado entre cotações (item D).

Bug: depois de cotar BSB→LUX, mandar "voo de brasília, ida 25/08, 1 adulto"
(sem destino) refazia BSB→LUX em silêncio — porque `intake_complete` e o destino
antigo sobreviviam no estado e o grafo pulava o intake. A nova cotação deve
reabrir o intake e perguntar o destino.
"""
import unittest

from backend.app.api.v1.chat.routes import _clear_results_if_new_quote


def _prev_state() -> dict:
    """Estado de uma cotação BSB→LUX já concluída."""
    return {
        "intake_complete": True,
        "awaiting_field": None,
        "presented_offers": [{"offer_id": "x"}],
        "search_results": [{"offer_id": "x"}],
        "slots": {
            "origin_iata": "BSB", "destination_iata": "LUX",
            "destination_city": "Luxemburgo", "date_start": "2026-08-25",
            "adults": 1, "cabin": "economy", "trip_type": "oneway",
        },
    }


class TestStateHygiene(unittest.TestCase):
    def test_nova_cotacao_sem_destino_reabre_intake_e_limpa_destino(self):
        st = _prev_state()
        _clear_results_if_new_quote(st, "voo de brasília, ida 25/08, 1 adulto")
        # destino antigo NÃO pode sobreviver
        self.assertIsNone(st["slots"].get("destination_iata"))
        self.assertIsNone(st["slots"].get("destination_city"))
        # intake reaberto (não pula pro orchestrator com slots velhos)
        self.assertFalse(st.get("intake_complete"))
        # resultados zerados
        self.assertNotIn("presented_offers", st)
        # origem + passageiros + cabine PERMANECEM (valem pro mesmo atendimento)
        self.assertEqual(st["slots"].get("origin_iata"), "BSB")
        self.assertEqual(st["slots"].get("adults"), 1)
        self.assertEqual(st["slots"].get("cabin"), "economy")

    def test_resposta_a_pergunta_nao_limpa_estado(self):
        """'luxemburgo' sozinho é resposta a uma pergunta, não nova cotação."""
        st = _prev_state()
        st["slots"]["destination_iata"] = None  # estava perguntando o destino
        _clear_results_if_new_quote(st, "luxemburgo")
        # não dispara limpeza (não parece nova cotação) → não mexe em nada
        self.assertEqual(st["slots"].get("origin_iata"), "BSB")

    def test_nova_cotacao_completa_limpa_destino_para_reextrair(self):
        st = _prev_state()
        _clear_results_if_new_quote(st, "voo de brasília para rio, ida 25/08")
        # destino antigo some; o intake/parse re-extrai o novo (rio) depois
        self.assertIsNone(st["slots"].get("destination_iata"))
        self.assertFalse(st.get("intake_complete"))


if __name__ == "__main__":
    unittest.main()
