"""Agentes do produto chat — orquestração via LangGraph.

Pipeline conceitual:

    user_msg → Intake → (slots completos?)
                ├─ não → ask_more (loop)
                └─ sim → Orchestrator → Validator → Presenter → done
                                                 ↓
                                            (refinement loop)

Cada nó é stateless do ponto de vista do Python — leem/escrevem
`ChatState`. Persistência fica fora (Repository). LLM é injetado no
nó como dependência opcional pra facilitar testes.

Pra usar de fora:
    from backend.app.ai.agents import build_graph, ChatState
    graph = build_graph()
    final_state = graph.invoke({"messages": [...], "user_id": "u1"})
"""
from backend.app.ai.agents.graph import build_graph
from backend.app.ai.agents.state import ChatState, IntakeSlots

__all__ = ["ChatState", "IntakeSlots", "build_graph"]
