"""DTOs HTTP do produto chat — separados do domínio interno.

Mantém transport (`api/v1/chat`) desacoplado de `chat/domain/models.py`.
Quando o domínio mudar, mexemos só nos mappers, não em todos os DTOs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# Re-export pra rotas reusarem
__all__ = [
    "LoginRequestDTO", "RegisterRequestDTO", "SessionResponseDTO",
    "ForgotPasswordRequestDTO", "ResetPasswordRequestDTO",
    "SetPasswordRequestDTO", "SimpleMessageDTO",
    "ThreadDTO", "ThreadListResponseDTO", "CreateThreadRequestDTO",
    "MessageDTO", "MessageListResponseDTO",
    "SendMessageRequestDTO", "SendMessageResponseDTO",
    "ApproveOfferRequestDTO", "QuoteDTO", "QuoteListResponseDTO",
    "RateTierDTO", "ProgramRatesDTO", "RatesResponseDTO", "RatesUpdateRequestDTO",
]


# ─── Auth ──────────────────────────────────────────────────────────
class LoginRequestDTO(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=1, max_length=200)


class RegisterRequestDTO(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=8, max_length=200)
    display_name: Optional[str] = Field(None, max_length=120)
    store_name: Optional[str] = Field(None, max_length=120)


class SessionResponseDTO(BaseModel):
    user_id: str
    email: str
    display_name: Optional[str] = None
    store_name: Optional[str] = None
    access_token: str


class ForgotPasswordRequestDTO(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)


class ResetPasswordRequestDTO(BaseModel):
    token: str = Field(..., min_length=8, max_length=400)
    password: str = Field(..., min_length=8, max_length=200)


class SetPasswordRequestDTO(BaseModel):
    """Reset simples (sem e-mail): troca a senha pelo e-mail da conta."""
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=8, max_length=200)


class SimpleMessageDTO(BaseModel):
    message: str


# ─── Threads ───────────────────────────────────────────────────────
class ThreadDTO(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    archived: bool = False


class ThreadListResponseDTO(BaseModel):
    threads: List[ThreadDTO]


class CreateThreadRequestDTO(BaseModel):
    title: Optional[str] = Field(None, max_length=120)


# ─── Messages ──────────────────────────────────────────────────────
class MessageDTO(BaseModel):
    id: str
    role: str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MessageListResponseDTO(BaseModel):
    messages: List[MessageDTO]


class SendMessageRequestDTO(BaseModel):
    content: str = Field(..., min_length=1, max_length=8000)


class SendMessageResponseDTO(BaseModel):
    """Resposta SEM streaming — útil pra clientes simples e testes.

    O endpoint streaming retorna SSE em vez deste DTO.
    """
    thread_id: str
    user_message: MessageDTO
    assistant_message: MessageDTO


# ─── Quotes & PDF ──────────────────────────────────────────────────
class ApproveOfferRequestDTO(BaseModel):
    thread_id: str
    offer_id: str
    # Nome do cliente final (passageiro) — opcional; aparece personalizado no PDF.
    client_name: Optional[str] = Field(None, max_length=120)


class QuoteDTO(BaseModel):
    id: str
    thread_id: str
    status: str
    approved_offer_id: Optional[str]
    pdf_path: Optional[str]
    created_at: datetime
    updated_at: datetime


class QuoteListResponseDTO(BaseModel):
    quotes: List[QuoteDTO]


# ─── Settings: tabela de milhas ────────────────────────────────────
class RateTierDTO(BaseModel):
    """Uma faixa de preço: 'até X milhas custa Y BRL por milha'."""
    max_miles: Optional[int] = Field(
        None,
        description="Limite superior da faixa (inclusivo). null = sem limite (faixa topo).",
    )
    rate: float = Field(
        ..., gt=0, le=1.0,
        description="BRL por milha (ex.: 0.025 = R$ 25 por mil milhas).",
    )


class ProgramRatesDTO(BaseModel):
    """Conjunto de faixas de um programa (LATAM, GOL, etc)."""
    program: str = Field(..., min_length=2, max_length=60)
    tiers: List[RateTierDTO] = Field(..., min_length=1)


class RatesResponseDTO(BaseModel):
    programs: List[ProgramRatesDTO]
    international_fallback_rate: float = Field(..., gt=0, le=1.0)
    skiplagged_estimation_program: str
    updated_at: Optional[datetime] = None


class RatesUpdateRequestDTO(BaseModel):
    programs: List[ProgramRatesDTO] = Field(..., min_length=1)
    international_fallback_rate: float = Field(..., gt=0, le=1.0)
    skiplagged_estimation_program: str = Field(..., min_length=2)


# ─── Health-check de programas de milhas (diagnóstico interno) ───────
class ProgramHealthDTO(BaseModel):
    program: str
    label: str
    source_type: str
    status: str                       # ok | empty | error | timeout
    offers_count: int
    latency_ms: float
    route: str                        # "GRU→MIA"
    error_kind: Optional[str] = None
    error_detail: Optional[str] = None
    checked_at: str


class MilesHealthcheckRequestDTO(BaseModel):
    programs: Optional[List[str]] = None   # vazio/None = todos os programas


class MilesHealthcheckResponseDTO(BaseModel):
    results: List[ProgramHealthDTO]
    ran_at: str
    total_ms: float
    ok_count: int
    empty_count: int
    error_count: int


# ─── Validações internas (sistema vs. manual) ───────────────────────
class CreateValidationRequestDTO(BaseModel):
    thread_id: str = Field(..., max_length=64)
    message_id: Optional[str] = Field(None, max_length=64)
    offer_id: Optional[str] = Field(None, max_length=120)
    kind: str = Field(..., pattern="^(validated|corrected)$")
    system_offer: Dict[str, Any] = Field(default_factory=dict)
    found_airline: Optional[str] = Field(None, max_length=120)
    found_program: Optional[str] = Field(None, max_length=120)
    emission_method: Optional[str] = Field(None, max_length=40)
    found_value_brl: Optional[float] = None
    found_miles: Optional[int] = None
    observations: Optional[str] = Field(None, max_length=2000)


class QuoteValidationDTO(BaseModel):
    id: str
    thread_id: str
    message_id: Optional[str] = None
    offer_id: Optional[str] = None
    kind: str
    system_offer: Dict[str, Any] = Field(default_factory=dict)
    found_airline: Optional[str] = None
    found_program: Optional[str] = None
    emission_method: Optional[str] = None
    found_value_brl: Optional[float] = None
    found_miles: Optional[int] = None
    observations: Optional[str] = None
    created_at: str


class ValidationStatsDTO(BaseModel):
    total: int
    validated_count: int
    corrected_count: int
    accuracy_pct: float
    avg_delta_brl: Optional[float] = None
    by_method: Dict[str, int] = Field(default_factory=dict)
    by_airline: Dict[str, int] = Field(default_factory=dict)


class CreateBugReportRequestDTO(BaseModel):
    thread_id: str = Field(..., max_length=64)
    description: str = Field(..., min_length=1, max_length=2000)
    context: Dict[str, Any] = Field(default_factory=dict)


class BugReportDTO(BaseModel):
    id: str
    thread_id: str
    description: str
    context: Dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: str


# ─── Ranking feedback ("cotação ideal") ─────────────────────────────
class MarkIdealRequestDTO(BaseModel):
    thread_id: str = Field(..., max_length=64)
    message_id: str = Field(..., max_length=64)   # mensagem do assistente com os cards
    offer_id: str = Field(..., max_length=128)    # oferta marcada como ideal


class RankingFeedbackDTO(BaseModel):
    id: str
    thread_id: str
    message_id: Optional[str] = None
    ideal_offer_id: str
    created_at: str
