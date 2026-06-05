"""ChatState — estado tipado compartilhado entre os nós do grafo.

LangGraph espera um TypedDict (ou dataclass) como state. Mantemos os campos
"opcionais até preenchidos" — cada nó sabe que valores podem estar ausentes.

Critério de pertencimento ao state:
- Sim, se mais de um nó lê/escreve OU se precisa sobreviver entre turnos.
- Não, se for cálculo intermediário interno do nó (esse fica em variável local).
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class IntakeSlots(TypedDict, total=False):
    """Slots que o Intake extrai do diálogo. Todos opcionais até confirmados."""
    origin_city: str
    origin_iata: str
    destination_city: str
    destination_iata: str
    date_start: str          # ISO YYYY-MM-DD
    date_end: Optional[str]  # ISO — fim do range de IDA (quando flex_mode=range)
    date_return: Optional[str]
    # Janela de VOLTA própria (ex.: "voltando entre 25 e 27"). Quando presente,
    # o orchestrator faz cross-product janela-ida × janela-volta via radar Kayak.
    return_from: Optional[str]   # ISO — início da janela de volta
    return_to: Optional[str]     # ISO — fim da janela de volta
    trip_type: Literal["oneway", "roundtrip"]
    adults: int
    children: int                    # 2-11 anos
    children_ages: List[int]         # idades específicas (afeta política tarifária)
    infants: int                     # <2 anos
    cabin: Literal["economy", "business", "first"]
    direct_only: bool
    baggage_checked: bool
    # plus = só pra frente, minus = só pra trás, plusminus = ambos.
    # Pipeline interno trata todos como plusminus por ora; o slot preserva
    # a preferência pra futuras refinements e pro vendedor saber.
    flex_mode: Literal["none", "plus", "minus", "plusminus", "range"]
    flex_days: int
    # Duração da viagem (dias). Usado quando vendedor diz "viagem de 3 dias"
    # dentro de um range de datas — orchestrator gera combinações ida+volta.
    trip_duration_days: int
    # Preferência de horário (manha/tarde/noite/madrugada) — preferência SUAVE
    # aplicada na apresentação (prioriza, não exclui).
    time_preference: str
    notes: str               # observações livres do vendedor (cliente VIP, etc.)


# Routes possíveis decididas pelo router.
NodeName = Literal[
    "intake",
    "orchestrator",
    "validator",
    "presenter",
    "refinement",
    "approve",
    "end",
]


class ChatState(TypedDict, total=False):
    # Identidade e contexto
    user_id: str
    thread_id: str

    # Histórico de mensagens (gerenciado pelo LangGraph via reducer)
    messages: Annotated[List[BaseMessage], add_messages]

    # Estado do diálogo
    slots: IntakeSlots
    awaiting_field: Optional[str]          # próximo slot que estamos perguntando
    intake_complete: bool
    # Quantas vezes o intake já pediu info pro vendedor sem progredir.
    # Após N tentativas (loop guard) força orchestrator pra não travar.
    intake_attempts: int

    # Resultado da busca
    search_results: Optional[Dict[str, Any]]   # raw scenarios + offers (pré-sanitização)
    validation_report: Optional[Dict[str, Any]]

    # Apresentação ao usuário
    presented_offers: Optional[List[Dict[str, Any]]]   # sanitizado (sem provider names)
    presented_at: Optional[str]                        # timestamp ISO

    # Aprovação
    approved_offer_id: Optional[str]
    quote_id: Optional[str]

    # Próximo nó (decisão do router/cada nó)
    next_node: Optional[NodeName]

    # Orchestrator já emitiu um aviso específico de "sem tarifas" (ex.: flex
    # roundtrip sem resultados) — suprime o watchdog genérico.
    search_failed_notice: bool

    # Erros não fatais acumulados (mostrados no debug, não no output)
    errors: List[str]
