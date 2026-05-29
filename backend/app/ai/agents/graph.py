"""Construção do grafo LangGraph que orquestra o chat.

Topologia (★ = ponto onde a UI espera output e o grafo "para"):

           ┌──────────┐
   start → │  router  │ ──► intake ──► (★)
           └──────────┘        │
                ▲              ▼
                │         orchestrator
                │              │
                │              ▼
                │          validator
                │              │
                │              ▼
                │          presenter ──► (★)
                │              │
                │              ▼
                └─── refinement ─► approve ──► (★)

O grafo é stateful e re-entra a cada nova mensagem do usuário. Persistimos
o `state_snapshot` em Postgres entre turnos (resumability). O ponto de entrada
em cada chamada é decidido olhando o state: se `intake_complete=False`, vai
pro intake; se temos `presented_offers`, vai pro refinement; etc.
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from backend.app.ai.agents.intake import intake_node
from backend.app.ai.agents.orchestrator import orchestrator_node
from backend.app.ai.agents.presenter import presenter_node
from backend.app.ai.agents.refinement import refinement_node
from backend.app.ai.agents.state import ChatState
from backend.app.ai.agents.validator import validator_node

logger = logging.getLogger(__name__)


def _router(state: ChatState) -> str:
    """Decide o primeiro nó baseado no state atual.

    Não-LLM, puro Python. Crítico pra latência e auditabilidade.
    """
    if state.get("approved_offer_id"):
        return "end"
    if state.get("presented_offers"):
        return "refinement"
    if state.get("intake_complete"):
        return "orchestrator"
    return "intake"


def _route_after_node(state: ChatState) -> str:
    """Lê `next_node` do state. Default END (entrega resposta ao usuário)."""
    nxt = state.get("next_node")
    if nxt and nxt != "end":
        return nxt
    return END


def _approve_node(state: ChatState) -> ChatState:
    """Stub do approve — marca pra ROTA emitir o PDF.

    A geração do PDF rola fora do grafo (endpoint dedicado) porque precisa do
    contexto HTTP (user/store info) e fica mais simples assim. Aqui só sinaliza
    que o estado mudou e responde ao usuário.
    """
    from langchain_core.messages import AIMessage

    return {
        **state,
        "next_node": "end",
        "messages": [AIMessage(content=(
            "Perfeito. Vou gerar o relatório em PDF da cotação aprovada e te "
            "envio aqui. Um momento."
        ))],
    }


def build_graph() -> Any:
    """Constrói e compila o grafo. Compila uma vez por processo."""
    sg = StateGraph(ChatState)

    sg.add_node("intake", intake_node)
    sg.add_node("orchestrator", orchestrator_node)
    sg.add_node("validator", validator_node)
    sg.add_node("presenter", presenter_node)
    sg.add_node("refinement", refinement_node)
    sg.add_node("approve", _approve_node)

    sg.set_conditional_entry_point(
        _router,
        {
            "intake": "intake",
            "orchestrator": "orchestrator",
            "refinement": "refinement",
            "end": END,
        },
    )

    for src in ("intake", "orchestrator", "validator", "presenter", "refinement", "approve"):
        sg.add_conditional_edges(
            src,
            _route_after_node,
            {
                "intake": "intake",
                "orchestrator": "orchestrator",
                "validator": "validator",
                "presenter": "presenter",
                "refinement": "refinement",
                "approve": "approve",
                END: END,
            },
        )

    return sg.compile()


_compiled_graph = None


def get_graph() -> Any:
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph
