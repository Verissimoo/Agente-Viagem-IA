"""Refinement — detecta pedido de mudança e roteia de volta pro Intake/Orchestrator.

Acionado quando já apresentamos ofertas e o vendedor envia uma nova mensagem
que sugere ajuste (data, classe, passageiros, bagagem, voo direto, etc.).

Distingue 3 casos:
1. Aprovação → marca `next_node="approve"`.
2. Refinamento → resseta slots relevantes + roteia pra `intake`.
3. Off-topic / dúvida → resposta curta via LLM.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict

from langchain_core.messages import AIMessage, HumanMessage

from backend.app.ai.agents.state import ChatState

logger = logging.getLogger(__name__)


_APPROVE_PATTERNS = [
    r"\b(fechar?|fech[ao]u|fechamos|reserv[ao]|aprovar?|aprov[ao]u|escolho|escolhi|essa mesma|essa opc[aã]o|pode emitir)\b",
    r"\b(pdf|relat[oó]rio|gerar pdf|gera o pdf)\b",
]

# Padrões que indicam REAL mudança de parâmetro da busca
# (data, classe, bagagem, passageiros, voo direto) — exige passar pelo Intake
# pra atualizar os slots e confirmar.
_PARAM_CHANGE_PATTERNS = [
    r"\b(outra data|outro dia|fim de semana|semana que vem|antes|depois|posterga|adianta)\b",
    r"\b(direto|sem escala|com escala)\b",
    r"\b(executiva|business|primeira classe|econ[oô]mica)\b",
    r"\b(com bagagem|sem bagagem|bagagem despachada)\b",
    r"\b(\d+ passageiros|\d+ adultos?|crian[cç]a)\b",
    # Datas absolutas tipo "15/06", "15 de junho"
    r"\b\d{1,2}[\/\-]\d{1,2}([\/\-]\d{2,4})?\b",
    r"\b\d{1,2}\s+de\s+(janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\b",
]

# Padrões que sugerem RE-BUSCA com filtro/foco (sem mudar params) —
# vai direto pro orchestrator + presenter, sem re-confirmar.
_RESEARCH_PATTERNS = [
    r"\b(split|quebra de trecho|trecho dividido)\b",
    r"\b(hidden city|skiplagged|rota alternativa|rota otimizada)\b",
    r"\b(milhas|cash|dinheiro)\b",
    r"\b(mais barato|melhor pre[çc]o|outras? op[cç][oõ]es|alternativ)\b",
    r"\b(refazer|refaz|nova busca|busca novamente|tem mais|outras)\b",
    r"\b(latam|gol|azul|tap|iberia|american|copa|smiles|tudoazul|latam\s+pass)\b",
    r"\b(existe|existem|tem|encontra)\b.*\b(op[cç][oõ]es?|alternativ|outra|outro)\b",
]


def _classify(text: str) -> str:
    """Retorna "approve", "param_change", "research" ou "chat"."""
    if not text:
        return "chat"
    lower = text.lower()
    for p in _APPROVE_PATTERNS:
        if re.search(p, lower):
            return "approve"
    # Param-change tem precedência sobre research — se tem ambos
    # (ex.: "split em outra data"), trata como mudança de param.
    for p in _PARAM_CHANGE_PATTERNS:
        if re.search(p, lower):
            return "param_change"
    for p in _RESEARCH_PATTERNS:
        if re.search(p, lower):
            return "research"
    return "chat"


def refinement_node(state: ChatState) -> ChatState:
    messages = state.get("messages", [])
    last_user_msg = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            last_user_msg = msg.content if isinstance(msg.content, str) else str(msg.content)
            break

    intent = _classify(last_user_msg)
    logger.info("refinement: intent=%s text=%r", intent, last_user_msg[:80])

    if intent == "approve":
        return {**state, "next_node": "approve"}

    if intent == "param_change":
        # Mudou data/classe/etc → reabre intake pra confirmar params atualizados.
        return {
            **state,
            "intake_complete": False,
            "awaiting_field": None,
            "next_node": "intake",
        }

    if intent == "research":
        # Sem mudança de param — só quer ver os resultados de novo (ou
        # filtrados em algum tipo). Vai DIRETO pro orchestrator + presenter,
        # sem voltar pra intake/confirmação.
        return {
            **state,
            # Limpa snapshot anterior pra forçar nova execução
            "presented_offers": None,
            "intake_complete": True,
            "next_node": "orchestrator",
        }

    # Conversa genérica — pergunta de esclarecimento, dúvida tarifária, etc.
    return {
        **state,
        "next_node": "end",
        "messages": [AIMessage(content=(
            "Posso refinar a busca (datas, classe, bagagem, voo direto) ou fechar "
            "alguma das opções que mostrei. O que prefere?"
        ))],
    }
