"""Modelos de domínio do produto chat.

Independentes do FastAPI e do Supabase. O Repository converte para/de
linhas de banco; a API converte para/de DTOs HTTP.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid4().hex


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class QuoteStatus(str, Enum):
    """Estado da cotação dentro de uma thread.

    PROPOSED   — Agente devolveu opções, vendedor ainda não decidiu.
    REFINING   — Vendedor pediu refinamento (novas datas, classe, etc.).
    APPROVED   — Vendedor aceitou alguma opção. PDF pode ser emitido.
    EXPIRED    — Captura ficou velha (preço volátil) e precisa refresh.
    CANCELLED  — Vendedor abandonou.
    """
    PROPOSED = "proposed"
    REFINING = "refining"
    APPROVED = "approved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class User(BaseModel):
    """Vendedor autenticado. ID vem do Supabase Auth quando ligar."""
    model_config = ConfigDict(use_enum_values=False)

    id: str
    email: str
    display_name: Optional[str] = None
    store_name: Optional[str] = Field(None, description="Loja/agência do vendedor — sai no rodapé do PDF")
    created_at: datetime = Field(default_factory=_utcnow)


class ChatMessage(BaseModel):
    """Mensagem individual em uma thread.

    `metadata` carrega payloads estruturados que a UI usa para renderizar
    cards de oferta, avisos, etc. — distintos do `content` que é texto livre.
    Mantemos `metadata` deliberadamente livre (dict) porque cada agente
    produz formatos diferentes; validação semântica fica no presenter.
    """
    model_config = ConfigDict(use_enum_values=False)

    id: str = Field(default_factory=_new_id)
    thread_id: str
    role: MessageRole
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class ChatThread(BaseModel):
    """Conversa entre um vendedor e o assistente.

    O `state_snapshot` guarda o último estado serializado do LangGraph para
    permitir continuação da conversa em outra sessão (resumability).
    """
    model_config = ConfigDict(use_enum_values=False)

    id: str = Field(default_factory=_new_id)
    user_id: str
    title: str = "Nova conversa"
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    state_snapshot: Dict[str, Any] = Field(default_factory=dict)
    archived: bool = False


class ValidationKind(str, Enum):
    """Resultado da avaliação interna de uma cotação do sistema."""
    VALIDATED = "validated"        # o sistema acertou (bateu com a realidade)
    CORRECTED = "corrected"        # o vendedor achou algo melhor manualmente


# Métodos de emissão (string livre vinda do front — não enum rígido).
EMISSION_METHODS = ("milhas", "cash_cia", "ota", "hidden_city", "split", "outro")


class QuoteValidation(BaseModel):
    """Registro de validação/correção de uma cotação — base da tabela comparativa
    sistema vs. manual. AUTOSSUFICIENTE: carrega o snapshot da oferta do sistema,
    então sobrevive mesmo se a thread for apagada (sem FK cascade)."""
    model_config = ConfigDict(use_enum_values=False)

    id: str = Field(default_factory=_new_id)
    user_id: str
    thread_id: str
    message_id: Optional[str] = None     # mensagem do assistente com o card
    offer_id: Optional[str] = None       # offer_id do card validado/corrigido
    kind: ValidationKind
    system_offer: Dict[str, Any] = Field(default_factory=dict)  # snapshot do sistema
    # Correção manual (só quando kind=CORRECTED):
    found_airline: Optional[str] = None
    found_program: Optional[str] = None  # programa de milhas (Smiles, LATAM Pass…)
    emission_method: Optional[str] = None
    found_value_brl: Optional[float] = None
    found_miles: Optional[int] = None
    observations: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


class BugReport(BaseModel):
    """Relato de bug do vendedor — thread_id + descrição + contexto mínimo."""
    model_config = ConfigDict(use_enum_values=False)

    id: str = Field(default_factory=_new_id)
    user_id: str
    thread_id: str
    description: str
    context: Dict[str, Any] = Field(default_factory=dict)
    status: str = "open"
    created_at: datetime = Field(default_factory=_utcnow)


class Quote(BaseModel):
    """Cotação salva — pode ter sido aprovada (PDF emitido) ou só proposta.

    Armazena o `SearchRequest` que originou e a lista de ofertas selecionadas.
    Nunca expõe nomes de providers para o vendedor; o `presented_payload` é
    o snapshot já sanitizado que foi mostrado/aprovado.
    """
    model_config = ConfigDict(use_enum_values=False)

    id: str = Field(default_factory=_new_id)
    thread_id: str
    user_id: str
    status: QuoteStatus = QuoteStatus.PROPOSED
    search_request: Dict[str, Any]
    raw_offers: List[Dict[str, Any]] = Field(default_factory=list)
    presented_payload: Dict[str, Any] = Field(default_factory=dict)
    approved_offer_id: Optional[str] = None
    pdf_path: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
