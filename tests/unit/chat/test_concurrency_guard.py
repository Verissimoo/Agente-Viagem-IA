"""Guarda de concorrência por thread — uma cotação por vez na mesma conversa.

Regressão do bug do André: 2 requests na mesma thread rodavam em paralelo, a
busca que terminava depois escrevia estado obsoleto (hub internacional aleatório)
por cima do turno atual. A guarda impede a 2ª busca enquanto a 1ª está rodando.
"""
import time
import unittest

from backend.app.api.v1.chat import routes


class TestConcurrencyGuard(unittest.TestCase):
    def setUp(self) -> None:
        routes._INFLIGHT.clear()

    def tearDown(self) -> None:
        routes._INFLIGHT.clear()

    def test_segunda_cotacao_na_mesma_thread_e_bloqueada(self):
        self.assertTrue(routes._acquire_thread_slot("t1"))
        # 2ª tentativa enquanto a 1ª "roda" → bloqueada
        self.assertFalse(routes._acquire_thread_slot("t1"))

    def test_release_libera_a_thread(self):
        routes._acquire_thread_slot("t1")
        routes._release_thread_slot("t1")
        self.assertTrue(routes._acquire_thread_slot("t1"), "após release deve liberar")

    def test_threads_diferentes_nao_se_bloqueiam(self):
        self.assertTrue(routes._acquire_thread_slot("t1"))
        self.assertTrue(routes._acquire_thread_slot("t2"), "thread diferente é independente")

    def test_flag_expira_por_ttl(self):
        """Flag presa (ex.: exceção não tratada) não trava a thread pra sempre."""
        # Simula um início bem antigo (além do TTL).
        routes._INFLIGHT["t1"] = time.monotonic() - (routes._INFLIGHT_TTL_S + 5)
        self.assertTrue(routes._acquire_thread_slot("t1"), "flag expirada deve liberar")

    def test_release_idempotente(self):
        routes._release_thread_slot("inexistente")  # não levanta
        routes._acquire_thread_slot("t1")
        routes._release_thread_slot("t1")
        routes._release_thread_slot("t1")  # 2x não levanta
        self.assertTrue(routes._acquire_thread_slot("t1"))


if __name__ == "__main__":
    unittest.main()
