"""Harness de cenários de conversa — Camada 1 (determinístico).

Dirige o `intake_node` turno-a-turno com o interpretador LLM **mockado** (força
o caminho regex/determinístico → rápido e reproduzível, serve de gate no CI).
Cada "furo" achado em teste real vira um cenário aqui e nunca mais regride.

Dois formatos de cenário:
  • do zero: lista de mensagens em linguagem natural (turns).
  • meio de conversa: slots0 + awaiting0 já preenchidos (isola roteamento de
    slot — ex.: o vendedor respondendo só "06/08" quando perguntamos a volta).

A Camada 2 (LLM real + LLM-as-judge) vive em `live_eval.py` (não roda no CI).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage

from backend.app.ai.agents.intake import intake_node


class ConversationResult:
    """Resultado de uma conversa simulada — facilita asserts legíveis."""

    def __init__(self, state: Dict[str, Any], turns: List[Dict[str, Any]]) -> None:
        self.state = state
        self.slots: Dict[str, Any] = state.get("slots") or {}
        self.awaiting: Optional[str] = state.get("awaiting_field")
        self.complete: bool = bool(state.get("intake_complete"))
        self.next_node: Optional[str] = state.get("next_node")
        self.turns = turns  # [{user, ai, awaiting, complete}]

    @property
    def ai_questions(self) -> List[str]:
        return [t["ai"] for t in self.turns if t["ai"]]

    @property
    def reached_search(self) -> bool:
        return self.complete and self.next_node == "orchestrator"

    def asked_twice(self, substring: str) -> bool:
        """True se a IA fez a MESMA pergunta (contendo `substring`) 2+ vezes —
        sintoma de loop (não capturou a resposta do vendedor)."""
        hits = [q for q in self.ai_questions if substring.lower() in (q or "").lower()]
        return len(hits) >= 2


def run_intake(
    turns: List[str],
    monkeypatch,
    *,
    slots0: Optional[Dict[str, Any]] = None,
    awaiting0: Optional[str] = None,
) -> ConversationResult:
    """Roda a conversa pelo intake_node (LLM mockado → determinístico).

    `slots0`/`awaiting0` simulam um meio de conversa (slots já coletados +
    o campo que estávamos perguntando)."""
    # Força o caminho regex (sem chamada de rede ao LLM interpretador).
    monkeypatch.setattr(
        "backend.app.ai.agents.interpreter.interpret", lambda *a, **k: None
    )

    messages: List[Any] = []
    state: Dict[str, Any] = {
        "slots": dict(slots0 or {}),
        "awaiting_field": awaiting0,
        "intake_attempts": 0,
    }
    history: List[Dict[str, Any]] = []
    for text in turns:
        messages.append(HumanMessage(content=text))
        state = intake_node({**state, "messages": list(messages)})
        ai = [m.content for m in (state.get("messages") or []) if isinstance(m, AIMessage)]
        history.append({
            "user": text,
            "ai": ai[-1] if ai else None,
            "awaiting": state.get("awaiting_field"),
            "complete": bool(state.get("intake_complete")),
        })
    return ConversationResult(state, history)
