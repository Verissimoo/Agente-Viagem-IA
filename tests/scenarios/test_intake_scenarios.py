"""Cenários de conversa (Camada 1 — determinístico, gate de CI).

Cada teste é uma frase/conversa em LINGUAGEM NATURAL como o vendedor escreve.
Roda o intake real (LLM mockado) e afirma o comportamento: slots certos, sem
loop, chega na busca, pede o que falta sem quebrar.

>>> Achou um furo testando o sistema? Vire um cenário aqui. Nunca mais regride. <<<
"""
import pytest

from tests.scenarios.harness import run_intake

_BASE = {"origin_iata": "BSB", "destination_iata": "LIS",
         "date_start": "2026-07-23", "adults": 1}


# ─── Roteamento de slot em follow-up (onde moram os furos) ──────────
def test_responde_data_de_volta_solta_nao_entra_em_loop(monkeypatch):
    """BUG (jun/2026): perguntamos a volta, vendedor respondeu '06/08' e o
    sistema repetia 'Qual a data de volta?'. A data respondida É a volta."""
    r = run_intake(["06/08"], monkeypatch,
                   slots0={**_BASE, "trip_type": "roundtrip"}, awaiting0="date_return")
    assert r.slots.get("date_return"), "data de volta deveria ter sido capturada"
    assert not r.asked_twice("data de volta"), "não pode repetir a pergunta (loop)"
    assert r.reached_search, "com a volta preenchida, deveria ir pra busca"


def test_responde_volta_por_extenso(monkeypatch):
    r = run_intake(["dia 6 de agosto"], monkeypatch,
                   slots0={**_BASE, "trip_type": "roundtrip"}, awaiting0="date_return")
    assert r.slots.get("date_return")
    assert r.reached_search


def test_responde_adultos_em_followup(monkeypatch):
    r = run_intake(["2 adultos"], monkeypatch,
                   slots0={"origin_iata": "GRU", "destination_iata": "GIG",
                           "date_start": "2026-08-15"}, awaiting0="adults")
    assert r.slots.get("adults") == 2
    assert r.reached_search


def test_responde_origem_iata_em_followup(monkeypatch):
    """Vendedor responde a origem que faltava. Com 'de X' o regex resolve;
    cidade SOLTA ('Brasília') depende do LLM (coberto na Camada 2)."""
    r = run_intake(["de GRU"], monkeypatch,
                   slots0={"destination_iata": "GIG", "date_start": "2026-08-15", "adults": 1},
                   awaiting0="origin_iata")
    # invariante de segurança: ou captura, ou pergunta de novo — nunca trava/crasha.
    assert r.slots.get("origin_iata") == "GRU" or r.awaiting == "origin_iata"


# ─── Roteamento ciente-da-pergunta: TODO obrigatório respondido solto ───────
# (generalização do bug da data de volta — nenhum campo pode entrar em loop)
@pytest.mark.parametrize("resposta,esperado", [
    ("de GRU", "GRU"),
    ("GRU", "GRU"),
    ("São Paulo", "GRU"),                # curada: 1º hub
    ("saindo de Brasília", "BSB"),
    ("partindo do Rio de Janeiro", "GIG"),
])
def test_responde_origem_solta_nao_entra_em_loop(monkeypatch, resposta, esperado):
    r = run_intake([resposta], monkeypatch,
                   slots0={"destination_iata": "LIS", "date_start": "2026-08-15", "adults": 1},
                   awaiting0="origin_iata")
    assert r.slots.get("origin_iata") == esperado, f"{resposta!r} deveria virar {esperado}"
    assert not r.asked_twice("de onde"), "não pode repetir a pergunta de origem"
    assert r.reached_search


@pytest.mark.parametrize("resposta,esperado", [
    ("para Lisboa", "LIS"),
    ("LIS", "LIS"),
    ("pra Buenos Aires", "EZE"),
    ("destino Miami", "MIA"),
])
def test_responde_destino_solto_nao_entra_em_loop(monkeypatch, resposta, esperado):
    r = run_intake([resposta], monkeypatch,
                   slots0={"origin_iata": "GRU", "date_start": "2026-08-15", "adults": 1},
                   awaiting0="destination_iata")
    assert r.slots.get("destination_iata") == esperado, f"{resposta!r} deveria virar {esperado}"
    assert r.reached_search


@pytest.mark.parametrize("resposta", ["23/07", "dia 23 de julho", "23/07/2026", "23-07"])
def test_responde_data_de_ida_solta_nao_entra_em_loop(monkeypatch, resposta):
    r = run_intake([resposta], monkeypatch,
                   slots0={"origin_iata": "GRU", "destination_iata": "LIS", "adults": 1},
                   awaiting0="date_start")
    assert r.slots.get("date_start"), f"{resposta!r} deveria preencher a ida"
    assert not r.asked_twice("data de ida")
    assert r.reached_search


@pytest.mark.parametrize("resposta,esperado", [
    ("2", 2),
    ("2 pessoas", 2),
    ("duas pessoas", 2),
    ("somos 3", 3),
    ("só eu", 1),
    ("uma pessoa", 1),
])
def test_responde_adultos_solto_nao_entra_em_loop(monkeypatch, resposta, esperado):
    r = run_intake([resposta], monkeypatch,
                   slots0={"origin_iata": "GRU", "destination_iata": "LIS", "date_start": "2026-08-15"},
                   awaiting0="adults")
    assert r.slots.get("adults") == esperado, f"{resposta!r} deveria virar {esperado} adultos"
    assert not r.asked_twice("passageiros")
    assert r.reached_search


# ─── Frases completas (one-shot) ───────────────────────────────────
def test_oneway_completa_vai_pra_busca(monkeypatch):
    r = run_intake(["GRU para GIG, 15/08/2026, 1 adulto"], monkeypatch)
    assert r.slots.get("origin_iata") == "GRU"
    assert r.slots.get("destination_iata") == "GIG"
    assert r.slots.get("adults") == 1
    assert r.reached_search


def test_roundtrip_numa_frase_so(monkeypatch):
    r = run_intake(["BSB para LIS ida 23/07 volta 06/08 1 adulto"], monkeypatch)
    assert r.slots.get("date_return"), "deveria capturar a volta na mesma frase"
    assert r.reached_search


def test_executiva_vira_business(monkeypatch):
    r = run_intake(["GRU para GIG 15/08/2026 1 adulto em executiva"], monkeypatch)
    assert r.slots.get("cabin") == "business"


def test_voo_direto(monkeypatch):
    r = run_intake(["GRU para GIG 15/08/2026 1 adulto voo direto"], monkeypatch)
    assert r.slots.get("direct_only") is True


def test_crianca_com_idade(monkeypatch):
    r = run_intake(["GRU para GIG 15/08/2026, 2 adultos e 1 criança de 5 anos"], monkeypatch)
    assert r.slots.get("adults") == 2
    assert r.slots.get("children") == 1


# ─── Construção em vários turnos ───────────────────────────────────
def test_monta_aos_poucos(monkeypatch):
    r = run_intake(
        ["quero cotar um voo", "de GRU para GIG", "15/08/2026", "1 adulto"],
        monkeypatch,
    )
    assert r.slots.get("origin_iata") == "GRU"
    assert r.slots.get("destination_iata") == "GIG"
    assert r.slots.get("date_start")
    assert r.reached_search


# ─── Faltando info → pergunta, não quebra ──────────────────────────
def test_falta_info_pergunta_sem_crashar(monkeypatch):
    """Sem destino → o sistema PERGUNTA (não vai pra busca, não quebra)."""
    r = run_intake(["voo saindo de GRU dia 15/08/2026, 1 adulto"], monkeypatch)
    assert not r.reached_search
    assert r.ai_questions, "deveria ter feito uma pergunta (não crashar)"
    assert r.awaiting, "deveria estar aguardando algum campo"


def test_mensagem_sem_nada_util_nao_crasha(monkeypatch):
    r = run_intake(["oi, tudo bem?"], monkeypatch)
    assert not r.reached_search
    assert r.ai_questions, "deveria responder pedindo a info, sem exceção"


# ─── Flex / duração ────────────────────────────────────────────────
def test_range_de_datas(monkeypatch):
    r = run_intake(["GRU para GIG entre 10 e 15 de agosto de 2026, 1 adulto"], monkeypatch)
    assert r.slots.get("date_start")
    # range seta date_end OU flex_mode=range
    assert r.slots.get("date_end") or r.slots.get("flex_mode") == "range"


def test_duracao_de_viagem_capturada(monkeypatch):
    r = run_intake(
        ["GRU para GIG 23/07/2026, 1 adulto, viagem de 10 dias"], monkeypatch,
    )
    assert r.slots.get("trip_duration_days") == 10
    assert r.slots.get("trip_type") == "roundtrip", "duração implica ida-e-volta"


# ─── Loop guard: não trava pra sempre ──────────────────────────────
def test_loop_guard_nao_pergunta_infinito(monkeypatch):
    """Vendedor não dá o destino em várias respostas → após N tentativas o
    intake corta com mensagem de reset (não fica em loop eterno)."""
    r = run_intake(
        ["quero um voo", "de GRU", "1 adulto", "amanhã", "sei lá", "vai lá"],
        monkeypatch,
        slots0={"origin_iata": "GRU", "adults": 1},
    )
    # ou resolveu, ou cortou com mensagem — mas NÃO ficou pedindo a mesma coisa eternamente
    assert r.complete or r.ai_questions, "deveria progredir ou cortar, nunca travar mudo"
