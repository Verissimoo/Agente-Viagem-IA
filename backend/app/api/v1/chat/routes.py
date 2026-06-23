"""Rotas HTTP do chat. Todas exigem Bearer token, exceto /auth/*.

Layout:
  POST /chat/auth/register        → cria conta (DevAuthProvider) ou erro 501 (Neon)
  POST /chat/auth/login           → email+senha (DevAuthProvider) ou erro 501 (Neon)
  GET  /chat/auth/me              → perfil do token atual

  GET  /chat/threads              → lista threads do usuário
  POST /chat/threads              → cria thread vazia
  GET  /chat/threads/{id}         → detalhes + mensagens
  DELETE /chat/threads/{id}       → soft-archive (não deleta)
  POST /chat/threads/{id}/messages
                                  → envia mensagem; roda o grafo; devolve resposta

  POST /chat/quotes/approve       → marca oferta como aprovada na thread
  GET  /chat/quotes               → lista cotações do usuário
  GET  /chat/quotes/{id}/pdf      → baixa PDF (gerando on-demand se não existe)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from backend.app.ai.agents.graph import get_graph
from backend.app.ai.agents.intake import looks_like_new_quote

# Campos de RESULTADO que NÃO podem sobreviver a uma nova cotação no mesmo
# thread — senão o router vê `presented_offers` e vai pra refinement (repetindo
# o resultado anterior). Limpos antes de invocar o grafo quando a mensagem do
# usuário parece abrir uma busca nova.
_RESULT_STATE_FIELDS = (
    "presented_offers", "presented_at", "search_results",
    "validation_report", "approved_offer_id", "quote_id",
)


def _clear_results_if_new_quote(state: dict, user_text: str) -> None:
    """Zera os campos de resultado do turno anterior se a mensagem abre uma nova
    cotação (rota nova). Mutação in-place no state que vai pro grafo."""
    if looks_like_new_quote(user_text):
        for k in _RESULT_STATE_FIELDS:
            state.pop(k, None)
from backend.app.api.v1.chat.deps import (
    AuditDep,
    RepoDep,
    SessionDep,
    _client_ip,
)
from backend.app.api.v1.chat.schemas import (
    ApproveOfferRequestDTO,
    BugReportDTO,
    CreateBugReportRequestDTO,
    CreateThreadRequestDTO,
    CreateValidationRequestDTO,
    ForgotPasswordRequestDTO,
    LoginRequestDTO,
    MarkIdealRequestDTO,
    MessageDTO,
    MessageListResponseDTO,
    MilesHealthcheckRequestDTO,
    MilesHealthcheckResponseDTO,
    ProgramHealthDTO,
    ProgramRatesDTO,
    QuoteDTO,
    QuoteListResponseDTO,
    QuoteValidationDTO,
    RankingFeedbackDTO,
    RatesResponseDTO,
    RatesUpdateRequestDTO,
    RateTierDTO,
    RegisterRequestDTO,
    ResetPasswordRequestDTO,
    SendMessageRequestDTO,
    SetPasswordRequestDTO,
    SendMessageResponseDTO,
    SessionResponseDTO,
    SimpleMessageDTO,
    ThreadDTO,
    ThreadListResponseDTO,
    ValidationStatsDTO,
)
from backend.app.chat.auth import AuthError, AuthSession, get_auth_provider
from backend.app.chat.config import settings
from backend.app.chat.domain.models import (
    BugReport,
    ChatMessage,
    ChatThread,
    MessageRole,
    Quote,
    QuoteStatus,
    QuoteValidation,
    RankingFeedback,
    ValidationKind,
)
from backend.app.chat.repository import ChatRepository
from backend.app.chat.security.audit import AuditLogger
from backend.app.chat.security.input_filter import (
    InputViolation,
    sanitize_user_message,
)
from backend.app.chat.security.content_safety import check_content_safety, refusal_message
from backend.app.chat.security.jailbreak import detect_jailbreak
from backend.app.chat.security.output_filter import sanitize_assistant_output
from backend.app.chat.security.rate_limit import RateLimitExceeded, get_rate_limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


# ─── Auth ──────────────────────────────────────────────────────────
@router.post("/auth/register", response_model=SessionResponseDTO)
def register(
    payload: RegisterRequestDTO,
    request: Request,
    audit: AuditLogger = AuditDep,
) -> SessionResponseDTO:
    try:
        session = get_auth_provider().register(
            payload.email,
            payload.password,
            display_name=payload.display_name,
            store_name=payload.store_name,
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e)
        )
    except AuthError as e:
        audit.log("register.fail", severity="warn",
                  detail={"email": payload.email, "reason": str(e)},
                  ip_address=_client_ip(request))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    audit.log("register.ok", user_id=session.user_id,
              detail={"email": session.email}, ip_address=_client_ip(request))
    return _session_to_dto(session)


@router.post("/auth/login", response_model=SessionResponseDTO)
def login(
    payload: LoginRequestDTO,
    request: Request,
    audit: AuditLogger = AuditDep,
) -> SessionResponseDTO:
    try:
        session = get_auth_provider().login(payload.email, payload.password)
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e))
    except AuthError as e:
        audit.log("login.fail", severity="warn",
                  detail={"email": payload.email, "reason": str(e)},
                  ip_address=_client_ip(request))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    audit.log("login.ok", user_id=session.user_id,
              detail={"email": session.email}, ip_address=_client_ip(request))
    return _session_to_dto(session)


@router.post("/auth/forgot-password", response_model=SimpleMessageDTO)
def forgot_password(
    payload: ForgotPasswordRequestDTO,
    request: Request,
    audit: AuditLogger = AuditDep,
) -> SimpleMessageDTO:
    """Dispara o e-mail de redefinição. Resposta SEMPRE genérica — não revela se
    o e-mail existe (evita enumeração de contas)."""
    generic = SimpleMessageDTO(
        message="Se este e-mail tiver uma conta, enviamos um link para redefinir a senha."
    )
    try:
        token = get_auth_provider().request_password_reset(payload.email)
        audit.log("password_reset.requested", severity="info",
                  detail={"email": payload.email, "sent": bool(token)},
                  ip_address=_client_ip(request))
    except NotImplementedError:
        # Provider externo (Neon/Stack Auth) trata reset por conta própria.
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Redefinição de senha é feita pelo provedor de identidade.",
        )
    except Exception as e:
        # Falha interna não pode revelar nada nem virar 500 pro usuário.
        audit.log("password_reset.error", severity="warn",
                  detail={"email": payload.email, "reason": str(e)},
                  ip_address=_client_ip(request))
    return generic


@router.post("/auth/reset-password", response_model=SessionResponseDTO)
def reset_password(
    payload: ResetPasswordRequestDTO,
    request: Request,
    audit: AuditLogger = AuditDep,
) -> SessionResponseDTO:
    """Valida o token do e-mail e grava a nova senha, devolvendo sessão pronta."""
    try:
        session = get_auth_provider().reset_password(payload.token, payload.password)
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e))
    except AuthError as e:
        audit.log("password_reset.fail", severity="warn",
                  detail={"reason": str(e)}, ip_address=_client_ip(request))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    audit.log("password_reset.ok", user_id=session.user_id,
              detail={"email": session.email}, ip_address=_client_ip(request))
    return _session_to_dto(session)


@router.post("/auth/reset-password-simple", response_model=SessionResponseDTO)
def reset_password_simple(
    payload: SetPasswordRequestDTO,
    request: Request,
    audit: AuditLogger = AuditDep,
) -> SessionResponseDTO:
    """Reset SIMPLES (sem e-mail): troca a senha pelo e-mail e já autentica.
    Interino até o SMTP entrar — aí migramos pro fluxo por token."""
    try:
        session = get_auth_provider().set_password_direct(payload.email, payload.password)
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e))
    except AuthError as e:
        audit.log("password_set.fail", severity="warn",
                  detail={"email": payload.email, "reason": str(e)},
                  ip_address=_client_ip(request))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    audit.log("password_set.ok", user_id=session.user_id,
              detail={"email": session.email}, ip_address=_client_ip(request))
    return _session_to_dto(session)


@router.get("/auth/me", response_model=SessionResponseDTO)
def me(session: AuthSession = SessionDep, repo: ChatRepository = RepoDep) -> SessionResponseDTO:
    user = repo.get_user(session.user_id)
    if user:
        return SessionResponseDTO(
            user_id=user.id, email=user.email,
            display_name=user.display_name, store_name=user.store_name,
            access_token=session.access_token,
        )
    return _session_to_dto(session)


# ─── Threads ───────────────────────────────────────────────────────
@router.get("/threads", response_model=ThreadListResponseDTO)
def list_threads(
    session: AuthSession = SessionDep,
    repo: ChatRepository = RepoDep,
) -> ThreadListResponseDTO:
    # Esconde threads que o vendedor abriu mas nunca enviou nada — não polui
    # o histórico com cliques perdidos no "Nova cotação".
    threads = repo.list_threads(
        session.user_id, only_with_user_messages=True,
    )
    return ThreadListResponseDTO(threads=[_thread_to_dto(t) for t in threads])


_WELCOME_TEXT = (
    "**Olá! Eu sou o atendente da Passagens com Desconto.**\n\n"
    "Posso te ajudar a cotar passagens aéreas, comparar opções e gerar "
    "relatório em PDF da cotação aprovada.\n\n"
    "Pra começar, me diga **qual a rota e a data** "
    "(ex.: \"São Paulo → Lisboa, ida 15 de junho, volta 30 de junho, 2 adultos\")."
)

# Títulos default que devem ser renomeados automaticamente quando o
# usuário manda a primeira mensagem real.
_DEFAULT_TITLES = {"Nova conversa", "Primeira cotação"}


def _maybe_rename_thread(
    repo: ChatRepository,
    thread: ChatThread,
    first_user_text: str,
) -> None:
    """Se a thread tem título default e essa é a primeira msg real do user,
    renomeia pra um resumo do que foi pedido (truncado em 50 chars)."""
    if thread.title not in _DEFAULT_TITLES:
        return
    title = first_user_text.strip().replace("\n", " ")
    if len(title) > 50:
        title = title[:47].rstrip() + "…"
    if not title:
        return
    thread.title = title
    try:
        repo.update_thread(thread)
    except Exception as e:
        logger.warning("Falha renomeando thread %s: %s", thread.id, e)


@router.post("/threads", response_model=ThreadDTO)
def create_thread(
    payload: CreateThreadRequestDTO,
    session: AuthSession = SessionDep,
    repo: ChatRepository = RepoDep,
) -> ThreadDTO:
    thread = ChatThread(
        user_id=session.user_id,
        title=(payload.title or "Nova conversa")[:120],
    )
    repo.create_thread(thread)
    # Persiste a mensagem de boas-vindas — garante que reapareça no histórico
    # mesmo se o vendedor recarregar antes de mandar a 1a mensagem.
    welcome = ChatMessage(
        thread_id=thread.id,
        role=MessageRole.ASSISTANT,
        content=_WELCOME_TEXT,
        metadata={"welcome": True},
    )
    try:
        repo.append_message(welcome, user_id=session.user_id)
    except Exception as e:
        logger.warning("Falha persistindo welcome msg para thread %s: %s", thread.id, e)
    return _thread_to_dto(thread)


@router.get("/threads/{thread_id}", response_model=MessageListResponseDTO)
def get_thread_messages(
    thread_id: str,
    session: AuthSession = SessionDep,
    repo: ChatRepository = RepoDep,
) -> MessageListResponseDTO:
    if not repo.get_thread(thread_id, session.user_id):
        raise HTTPException(status_code=404, detail="Thread não encontrada")
    messages = repo.list_messages(thread_id, session.user_id)
    return MessageListResponseDTO(messages=[_msg_to_dto(m) for m in messages])


@router.delete("/threads/{thread_id}")
def delete_thread(
    thread_id: str,
    session: AuthSession = SessionDep,
    repo: ChatRepository = RepoDep,
    audit: AuditLogger = AuditDep,
) -> Dict[str, Any]:
    """Deleta a thread (cascateia: messages e quotes via FK ON DELETE CASCADE)."""
    deleted = repo.delete_thread(thread_id, session.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Thread não encontrada")
    audit.log("thread.deleted", user_id=session.user_id, thread_id=thread_id)
    return {"ok": True}


# ─── Mensagem (núcleo: roda o grafo) ───────────────────────────────
@router.post("/threads/{thread_id}/messages", response_model=SendMessageResponseDTO)
def send_message(
    thread_id: str,
    payload: SendMessageRequestDTO,
    request: Request,
    session: AuthSession = SessionDep,
    repo: ChatRepository = RepoDep,
    audit: AuditLogger = AuditDep,
) -> SendMessageResponseDTO:
    # 1. Autorização
    thread = repo.get_thread(thread_id, session.user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread não encontrada")

    # 2. Rate limit por usuário
    try:
        get_rate_limiter().check_message(session.user_id)
    except RateLimitExceeded as e:
        audit.log("rate_limit.exceeded", user_id=session.user_id, thread_id=thread_id,
                  severity="warn", detail={"kind": e.kind, "retry_in_s": e.retry_in_s})
        raise HTTPException(status_code=429, detail=str(e))

    # 3. Sanitização de input
    try:
        sanitized = sanitize_user_message(payload.content)
    except InputViolation as e:
        audit.log("guardrail.input.blocked", user_id=session.user_id, thread_id=thread_id,
                  severity="warn", detail={"reason": str(e)})
        raise HTTPException(status_code=400, detail=str(e))

    # 4. Jailbreak detection
    jb = detect_jailbreak(sanitized.text)
    if jb.flagged and jb.severity == "block":
        audit.log("guardrail.jailbreak.detected",
                  user_id=session.user_id, thread_id=thread_id,
                  severity="security",
                  detail={"pattern": jb.pattern_id, "sample": jb.sample},
                  ip_address=_client_ip(request),
                  user_agent=request.headers.get("user-agent"))
        from backend.app.ai.agents.prompts import REFUSAL_JAILBREAK
        return _persist_user_and_assistant(
            repo, thread_id, session.user_id, sanitized.text, REFUSAL_JAILBREAK,
        )

    # 4b. Segurança de conteúdo (NSFW / nocivo / off-topic): recusa ANTES do
    # grafo, economizando token do LLM. Kill-switch: CHAT_CONTENT_SAFETY=0.
    if os.getenv("CHAT_CONTENT_SAFETY", "1") not in ("0", "false", "False", ""):
        cs = check_content_safety(sanitized.text)
        if cs.flagged:
            audit.log("guardrail.content.blocked",
                      user_id=session.user_id, thread_id=thread_id,
                      severity="security",
                      detail={"category": cs.category, "sample": cs.sample},
                      ip_address=_client_ip(request),
                      user_agent=request.headers.get("user-agent"))
            return _persist_user_and_assistant(
                repo, thread_id, session.user_id, sanitized.text,
                refusal_message(cs.category),
            )

    # 5. Persistir mensagem do usuário
    user_msg = ChatMessage(
        thread_id=thread_id, role=MessageRole.USER, content=sanitized.text,
    )
    repo.append_message(user_msg, user_id=session.user_id)

    # Auto-renomeia thread se ainda tá com título default
    _maybe_rename_thread(repo, thread, sanitized.text)

    # 6. Reidratar histórico para o grafo (últimas 50 msgs — limite token)
    history = repo.list_messages(thread_id, session.user_id, limit=50)
    lc_messages = _to_langchain_messages(history)

    # 7. Estado inicial: continua do snapshot armazenado, ou começa zerado
    state = dict(thread.state_snapshot or {})
    _clear_results_if_new_quote(state, sanitized.text)  # nova cotação → não repete resultado
    state.update({
        "user_id": session.user_id,
        "thread_id": thread_id,
        "messages": lc_messages,
    })

    # 8. Roda o grafo (bloqueante — síncrono — geralmente <5s)
    try:
        final_state = get_graph().invoke(state)
    except Exception as e:
        logger.exception("Grafo falhou para thread %s", thread_id)
        audit.log("graph.error", user_id=session.user_id, thread_id=thread_id,
                  severity="error", detail={"error": str(e)[:500]})
        return _persist_assistant_only(
            repo, thread_id, session.user_id, user_msg,
            "Tive um problema processando sua mensagem. Pode tentar de novo?",
        )

    # 9. Extrai a última mensagem AI emitida no turno
    assistant_text, metadata = _extract_last_assistant(final_state)
    safe_text = sanitize_assistant_output(assistant_text) if assistant_text else (
        "Posso te ajudar a cotar uma passagem — me conta a rota e a data."
    )

    # WATCHDOG (mesmo do streaming)
    offers_in_metadata = metadata.get("offers") if isinstance(metadata, dict) else None
    slots_in_state = final_state.get("slots") or thread.state_snapshot.get("slots") or {}
    has_essentials = all(
        slots_in_state.get(k) for k in
        ("origin_iata", "destination_iata", "date_start", "adults")
    )
    expected_offers = (
        has_essentials
        and not final_state.get("awaiting_field")
        and not final_state.get("search_failed_notice")  # orchestrator já avisou de forma específica
        and not slots_in_state.get("intl_awaiting_confirmation")  # Fase 1: pergunta intencional
    )
    if expected_offers and not offers_in_metadata:
        logger.warning("WATCHDOG (sync): esperava cotação sem offers. text=%r",
                       safe_text[:120])
        audit.log("watchdog.no_offers", user_id=session.user_id,
                  thread_id=thread_id, severity="warn")
        safe_text = (
            "Tive um problema entregando a cotação agora — "
            "as fontes podem ter retornado vazio ou demorado demais. "
            "Tenta reenviar a solicitação ou usar flexibilidade de datas."
        )
        metadata = {**(metadata or {}), "watchdog_triggered": True}

    # 10. Persiste resposta + snapshot
    assistant_msg = ChatMessage(
        thread_id=thread_id,
        role=MessageRole.ASSISTANT,
        content=safe_text,
        metadata=metadata,
    )
    repo.append_message(assistant_msg, user_id=session.user_id)

    # Atualiza snapshot do grafo no thread (sem messages — elas vivem na tabela)
    snapshot = _serializable_state(final_state)
    snapshot.pop("messages", None)
    thread.state_snapshot = snapshot
    repo.update_thread(thread)

    return SendMessageResponseDTO(
        thread_id=thread_id,
        user_message=_msg_to_dto(user_msg),
        assistant_message=_msg_to_dto(assistant_msg),
    )


# ─── Mensagem com streaming SSE (caixa de status atualizando) ─────
# Mapeamento: nome do nó do grafo → texto que o usuário vê.
# Evita revelar detalhes técnicos (provider, ferramenta), mas mostra
# que algo concreto está acontecendo a cada etapa.
_NODE_STATUS = {
    "intake": "Entendendo sua solicitação",
    "orchestrator": "Buscando opções em nossas fontes",
    "validator": "Validando preços e condições",
    "presenter": "Preparando o resumo da cotação",
    "refinement": "Ajustando a busca conforme seu pedido",
    "approve": "Confirmando a oferta escolhida",
}


def _sse(event: str, data: Dict[str, Any]) -> str:
    """Codifica um evento SSE com event name e payload JSON."""
    import json as _json
    return f"event: {event}\ndata: {_json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/threads/{thread_id}/messages/stream")
def send_message_stream(
    thread_id: str,
    payload: SendMessageRequestDTO,
    request: Request,
    session: AuthSession = SessionDep,
    repo: ChatRepository = RepoDep,
    audit: AuditLogger = AuditDep,
):
    """Versão streaming do send_message: emite SSE com status por nó do grafo."""
    # Mesma auth/rate/sanitize/jailbreak do endpoint normal — falha rápido.
    thread = repo.get_thread(thread_id, session.user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread não encontrada")
    try:
        get_rate_limiter().check_message(session.user_id)
    except RateLimitExceeded as e:
        audit.log("rate_limit.exceeded", user_id=session.user_id, thread_id=thread_id,
                  severity="warn", detail={"kind": e.kind, "retry_in_s": e.retry_in_s})
        raise HTTPException(status_code=429, detail=str(e))
    try:
        sanitized = sanitize_user_message(payload.content)
    except InputViolation as e:
        audit.log("guardrail.input.blocked", user_id=session.user_id, thread_id=thread_id,
                  severity="warn", detail={"reason": str(e)})
        raise HTTPException(status_code=400, detail=str(e))

    jb = detect_jailbreak(sanitized.text)
    if jb.flagged and jb.severity == "block":
        from backend.app.ai.agents.prompts import REFUSAL_JAILBREAK
        audit.log("guardrail.jailbreak.detected",
                  user_id=session.user_id, thread_id=thread_id, severity="security",
                  detail={"pattern": jb.pattern_id})
        # Resposta direta — não passa pelo grafo
        def _refusal_gen():
            user_msg = ChatMessage(thread_id=thread_id, role=MessageRole.USER, content=sanitized.text)
            repo.append_message(user_msg, user_id=session.user_id)
            assistant_msg = ChatMessage(
                thread_id=thread_id, role=MessageRole.ASSISTANT,
                content=sanitize_assistant_output(REFUSAL_JAILBREAK),
            )
            repo.append_message(assistant_msg, user_id=session.user_id)
            yield _sse("message", _msg_to_dto(assistant_msg).model_dump(mode="json"))
            yield _sse("done", {"thread_id": thread_id})
        return StreamingResponse(_refusal_gen(), media_type="text/event-stream")

    # Segurança de conteúdo: recusa ANTES do grafo (economiza token).
    if os.getenv("CHAT_CONTENT_SAFETY", "1") not in ("0", "false", "False", ""):
        cs = check_content_safety(sanitized.text)
        if cs.flagged:
            audit.log("guardrail.content.blocked",
                      user_id=session.user_id, thread_id=thread_id, severity="security",
                      detail={"category": cs.category, "sample": cs.sample})
            _refusal_text = sanitize_assistant_output(refusal_message(cs.category))

            def _content_refusal_gen():
                user_msg = ChatMessage(thread_id=thread_id, role=MessageRole.USER, content=sanitized.text)
                repo.append_message(user_msg, user_id=session.user_id)
                assistant_msg = ChatMessage(
                    thread_id=thread_id, role=MessageRole.ASSISTANT, content=_refusal_text,
                )
                repo.append_message(assistant_msg, user_id=session.user_id)
                yield _sse("message", _msg_to_dto(assistant_msg).model_dump(mode="json"))
                yield _sse("done", {"thread_id": thread_id})
            return StreamingResponse(_content_refusal_gen(), media_type="text/event-stream")

    user_msg = ChatMessage(
        thread_id=thread_id, role=MessageRole.USER, content=sanitized.text,
    )
    repo.append_message(user_msg, user_id=session.user_id)
    _maybe_rename_thread(repo, thread, sanitized.text)

    history = repo.list_messages(thread_id, session.user_id, limit=50)
    lc_messages = _to_langchain_messages(history)
    state = dict(thread.state_snapshot or {})
    _clear_results_if_new_quote(state, sanitized.text)  # nova cotação → não repete resultado
    state.update({
        "user_id": session.user_id,
        "thread_id": thread_id,
        "messages": lc_messages,
    })

    def generator():
        try:
            yield _sse("user_message", _msg_to_dto(user_msg).model_dump(mode="json"))
            yield _sse("status", {"text": "Processando", "node": None})

            final_state: Dict[str, Any] = {}
            try:
                # stream_mode duplo: "updates" (transição de nó) + "custom"
                # (progresso emitido DE DENTRO de um nó via get_stream_writer —
                # ex.: a quebra de trecho internacional, que leva minutos). Com
                # múltiplos modos, cada item vem como (modo, chunk).
                for mode, chunk in get_graph().stream(
                    state, stream_mode=["updates", "custom"]
                ):
                    if mode == "custom":
                        if isinstance(chunk, dict) and chunk.get("progress"):
                            yield _sse("status", {"text": chunk["progress"], "node": "orchestrator"})
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    for node_name, partial_state in chunk.items():
                        label = _NODE_STATUS.get(node_name, "Trabalhando")
                        logger.info("[chat stream] thread=%s node=%s", thread_id, node_name)
                        yield _sse("status", {"text": label, "node": node_name})
                        if isinstance(partial_state, dict):
                            final_state.update(partial_state)
            except Exception as e:
                logger.exception("Grafo falhou (stream) para thread %s", thread_id)
                audit.log("graph.error", user_id=session.user_id, thread_id=thread_id,
                          severity="error", detail={"error": str(e)[:500]})
                err_msg = ChatMessage(
                    thread_id=thread_id, role=MessageRole.ASSISTANT,
                    content="Tive um problema processando sua mensagem. Pode tentar de novo?",
                )
                try:
                    repo.append_message(err_msg, user_id=session.user_id)
                except Exception:
                    logger.exception("falha persistindo err_msg")
                yield _sse("message", _msg_to_dto(err_msg).model_dump(mode="json"))
                yield _sse("done", {"thread_id": thread_id, "error": True})
                return

            assistant_text, metadata = _extract_last_assistant(final_state)
            safe_text = sanitize_assistant_output(assistant_text) if assistant_text else (
                "Posso te ajudar a cotar uma passagem — me conta a rota e a data."
            )

            # ─── WATCHDOG ──────────────────────────────────────────
            # Se o vendedor já tinha tudo preenchido (esperando cotação) mas
            # nenhuma oferta apareceu na resposta, o assistente "alucinou"
            # falando do processo sem entregar resultado. Forçamos uma
            # resposta determinística pra não deixar o usuário no escuro.
            offers_in_metadata = metadata.get("offers") if isinstance(metadata, dict) else None
            slots_in_state = final_state.get("slots") or thread.state_snapshot.get("slots") or {}
            has_essentials = all(
                slots_in_state.get(k) for k in
                ("origin_iata", "destination_iata", "date_start", "adults")
            )
            expected_offers = (
                has_essentials
                and not final_state.get("awaiting_field")
                and not final_state.get("search_failed_notice")
                # Etapa de confirmação internacional (Fase 1): a resposta É uma
                # pergunta intencional ("quer a busca na melhor data?") — sem
                # offers de propósito. Não é o assistente "alucinando".
                and not slots_in_state.get("intl_awaiting_confirmation")
            )
            if expected_offers and not offers_in_metadata:
                logger.warning(
                    "[chat stream] WATCHDOG: vendedor esperava cotação mas não "
                    "veio offers. text=%r slots=%s",
                    safe_text[:120], slots_in_state,
                )
                audit.log("watchdog.no_offers", user_id=session.user_id,
                          thread_id=thread_id, severity="warn",
                          detail={"text": safe_text[:200],
                                  "slots": {k: str(v)[:50] for k, v in slots_in_state.items()}})
                safe_text = (
                    "Tive um problema entregando a cotação agora — "
                    "as fontes podem ter retornado vazio ou demorado demais. "
                    "Tenta uma das opções:\n\n"
                    "1. Reenvia a mesma solicitação (pode ter sido lentidão temporária)\n"
                    "2. Tenta com flexibilidade de datas (ex.: \"flex de 7 dias\")\n"
                    "3. Tenta destino alternativo se a rota for muito específica"
                )
                metadata = {**(metadata or {}), "watchdog_triggered": True}

            assistant_msg = ChatMessage(
                thread_id=thread_id, role=MessageRole.ASSISTANT,
                content=safe_text, metadata=metadata,
            )
            try:
                repo.append_message(assistant_msg, user_id=session.user_id)
            except Exception:
                logger.exception("falha persistindo assistant_msg final")

            try:
                snapshot = _serializable_state(final_state)
                snapshot.pop("messages", None)
                if snapshot:
                    merged = dict(thread.state_snapshot or {})
                    merged.update(snapshot)
                    thread.state_snapshot = merged
                    repo.update_thread(thread)
            except Exception:
                logger.exception("falha atualizando snapshot")

            yield _sse("message", _msg_to_dto(assistant_msg).model_dump(mode="json"))
            yield _sse("done", {"thread_id": thread_id})

        except GeneratorExit:
            logger.info("[chat stream] cliente desconectou thread=%s", thread_id)
            raise
        except Exception:
            logger.exception("[chat stream] erro nao tratado thread=%s", thread_id)
            try:
                yield _sse("done", {"thread_id": thread_id, "error": True})
            except Exception:
                pass

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ─── Quotes & PDF ──────────────────────────────────────────────────
@router.post("/quotes/approve", response_model=QuoteDTO)
def approve_offer(
    payload: ApproveOfferRequestDTO,
    session: AuthSession = SessionDep,
    repo: ChatRepository = RepoDep,
    audit: AuditLogger = AuditDep,
) -> QuoteDTO:
    thread = repo.get_thread(payload.thread_id, session.user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread não encontrada")

    snapshot = thread.state_snapshot or {}
    presented: List[Dict[str, Any]] = snapshot.get("presented_offers") or []
    selected = next((o for o in presented if o.get("offer_id") == payload.offer_id), None)

    # Fallback: se snapshot tá vazio (ex: falha de persistência por timeout do
    # DB durante o turno), procura nas MENSAGENS da thread — o metadata da
    # última msg assistant contém as ofertas apresentadas.
    if not selected:
        try:
            history = repo.list_messages(payload.thread_id, session.user_id, limit=200)
            for msg in reversed(history):
                if msg.role.value != "assistant" if hasattr(msg.role, "value") else msg.role != "assistant":
                    continue
                offers_meta = (msg.metadata or {}).get("offers") or []
                for o in offers_meta:
                    if isinstance(o, dict) and o.get("offer_id") == payload.offer_id:
                        selected = o
                        # Reaproveita também pra preencher search_request se faltar
                        if not snapshot.get("slots"):
                            snapshot["slots"] = {}
                        break
                if selected:
                    break
        except Exception as e:
            logger.warning("approve fallback (msgs) falhou: %s", e)

    if not selected:
        raise HTTPException(
            status_code=400,
            detail="Oferta não encontrada — recarregue a conversa ou refaça a busca.",
        )

    # Persiste o nome do cliente dentro do presented_payload pra o PDF usar.
    payload_for_pdf: Dict[str, Any] = {"offer": selected}
    if payload.client_name and payload.client_name.strip():
        payload_for_pdf["client_name"] = payload.client_name.strip()
    quote = Quote(
        thread_id=payload.thread_id,
        user_id=session.user_id,
        status=QuoteStatus.APPROVED,
        search_request=snapshot.get("slots") or {},
        raw_offers=presented,
        presented_payload=payload_for_pdf,
        approved_offer_id=payload.offer_id,
    )
    repo.create_quote(quote)
    audit.log("quote.approved", user_id=session.user_id, thread_id=payload.thread_id,
              detail={"quote_id": quote.id, "offer_id": payload.offer_id})
    return _quote_to_dto(quote)


@router.get("/quotes", response_model=QuoteListResponseDTO)
def list_quotes(
    session: AuthSession = SessionDep,
    repo: ChatRepository = RepoDep,
) -> QuoteListResponseDTO:
    return QuoteListResponseDTO(
        quotes=[_quote_to_dto(q) for q in repo.list_quotes(session.user_id)]
    )


@router.get("/quotes/{quote_id}/pdf")
def download_quote_pdf(
    quote_id: str,
    session: AuthSession = SessionDep,
    repo: ChatRepository = RepoDep,
    audit: AuditLogger = AuditDep,
) -> Response:
    quote = repo.get_quote(quote_id, session.user_id)
    if not quote:
        raise HTTPException(status_code=404, detail="Cotação não encontrada")

    user = repo.get_user(session.user_id)
    if not user:
        raise HTTPException(status_code=400, detail="Perfil de usuário ausente")

    offer = (quote.presented_payload or {}).get("offer")
    if not offer:
        raise HTTPException(status_code=400, detail="Cotação sem oferta associada")

    from backend.app.chat.report import generate_quote_pdf
    try:
        pdf_bytes = generate_quote_pdf(quote, user, offer)
    except Exception as e:
        logger.exception("Falha gerando PDF de %s", quote_id)
        audit.log("quote.pdf.failed", user_id=session.user_id,
                  severity="error", detail={"quote_id": quote_id, "error": str(e)[:300]})
        raise HTTPException(status_code=500, detail="Falha ao gerar relatório")

    audit.log("quote.pdf.generated", user_id=session.user_id,
              detail={"quote_id": quote_id, "bytes": len(pdf_bytes)})

    filename = f"cotacao-{quote.id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─── Settings: tabela de tarifas (milhas) ──────────────────────────
@router.get("/settings/rates", response_model=RatesResponseDTO)
def get_rates(
    session: AuthSession = SessionDep,
    audit: AuditLogger = AuditDep,
) -> RatesResponseDTO:
    """Devolve a tabela atual de tarifas (BRL por milha)."""
    from backend.app.services.conversion import get_rates_snapshot
    snap = get_rates_snapshot()
    programs = [
        ProgramRatesDTO(
            program=name,
            tiers=[RateTierDTO(**t) for t in tiers],
        )
        for name, tiers in (snap.get("programs") or {}).items()
    ]
    return RatesResponseDTO(
        programs=programs,
        international_fallback_rate=float(snap.get("international_fallback_rate", 0.05)),
        skiplagged_estimation_program=str(snap.get("skiplagged_estimation_program", "GOL")),
    )


@router.put("/settings/rates", response_model=RatesResponseDTO)
def update_rates(
    payload: RatesUpdateRequestDTO,
    session: AuthSession = SessionDep,
    audit: AuditLogger = AuditDep,
) -> RatesResponseDTO:
    """Atualiza a tabela de tarifas — persiste no rates.json e invalida cache.

    Validações extras:
    - max_miles crescente dentro de cada programa
    - Última faixa pode (ou deve) ser null (sem limite)
    """
    # Validação de ordem das faixas
    for prog in payload.programs:
        last_max = -1
        for tier in prog.tiers[:-1]:   # todas menos a última
            if tier.max_miles is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{prog.program}: só a ÚLTIMA faixa pode ter max_miles=null. "
                        "Coloque a faixa sem limite por último."
                    ),
                )
            if tier.max_miles <= last_max:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{prog.program}: max_miles deve crescer entre faixas "
                        f"(encontrei {tier.max_miles} após {last_max})"
                    ),
                )
            last_max = tier.max_miles

    # Usa o helper existente do services/conversion (já faz backup + reload cache)
    from backend.app.services.conversion import update_rates as conv_update_rates
    try:
        conv_update_rates({
            "programs": {
                p.program: [t.model_dump() for t in p.tiers] for p in payload.programs
            },
            "international_fallback_rate": payload.international_fallback_rate,
            "skiplagged_estimation_program": payload.skiplagged_estimation_program,
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("falha salvando rates")
        raise HTTPException(status_code=500, detail=f"Falha salvando: {e}")

    audit.log(
        "settings.rates.updated",
        user_id=session.user_id,
        detail={
            "programs_count": len(payload.programs),
            "fallback_rate": payload.international_fallback_rate,
        },
    )
    return get_rates(session=session, audit=audit)


# ─── Mappers ───────────────────────────────────────────────────────
def _session_to_dto(s: AuthSession) -> SessionResponseDTO:
    return SessionResponseDTO(
        user_id=s.user_id, email=s.email,
        display_name=s.display_name, store_name=s.store_name,
        access_token=s.access_token,
    )


def _thread_to_dto(t: ChatThread) -> ThreadDTO:
    return ThreadDTO(
        id=t.id, title=t.title,
        created_at=t.created_at, updated_at=t.updated_at,
        archived=t.archived,
    )


def _msg_to_dto(m: ChatMessage) -> MessageDTO:
    role = m.role.value if isinstance(m.role, MessageRole) else str(m.role)
    return MessageDTO(
        id=m.id, role=role, content=m.content,
        metadata=m.metadata or {}, created_at=m.created_at,
    )


def _quote_to_dto(q: Quote) -> QuoteDTO:
    status_str = q.status.value if isinstance(q.status, QuoteStatus) else str(q.status)
    return QuoteDTO(
        id=q.id, thread_id=q.thread_id, status=status_str,
        approved_offer_id=q.approved_offer_id, pdf_path=q.pdf_path,
        created_at=q.created_at, updated_at=q.updated_at,
    )


def _to_langchain_messages(messages: List[ChatMessage]) -> List[Any]:
    """Converte mensagens persistidas em BaseMessage (sem mensagens system —
    o system prompt é injetado por cada nó)."""
    out: List[Any] = []
    for m in messages:
        role = m.role.value if isinstance(m.role, MessageRole) else m.role
        if role == "user":
            out.append(HumanMessage(content=m.content))
        elif role == "assistant":
            out.append(AIMessage(content=m.content))
        # system/tool: ignoramos no histórico (recriados a cada turno)
    return out


def _extract_last_assistant(state: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    messages = state.get("messages") or []
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            metadata: Dict[str, Any] = dict(msg.additional_kwargs or {})
            # presented_offers vai pro metadata pra UI renderizar cards
            if "offers" not in metadata and state.get("presented_offers"):
                metadata["offers"] = state["presented_offers"]
            return content, metadata
    return "", {}


def _serializable_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Serializa o state para guardar no JSONB do thread.

    Remove campos não-JSON (BaseMessage instances).
    """
    out: Dict[str, Any] = {}
    for k, v in state.items():
        if k == "messages":
            continue
        try:
            import json
            json.dumps(v, default=str)
            out[k] = v
        except Exception:
            out[k] = str(v)
    return out


def _persist_user_and_assistant(
    repo: ChatRepository,
    thread_id: str,
    user_id: str,
    user_text: str,
    assistant_text: str,
) -> SendMessageResponseDTO:
    user_msg = ChatMessage(thread_id=thread_id, role=MessageRole.USER, content=user_text)
    repo.append_message(user_msg, user_id=user_id)
    safe_text = sanitize_assistant_output(assistant_text)
    assistant_msg = ChatMessage(
        thread_id=thread_id, role=MessageRole.ASSISTANT, content=safe_text,
    )
    repo.append_message(assistant_msg, user_id=user_id)
    return SendMessageResponseDTO(
        thread_id=thread_id,
        user_message=_msg_to_dto(user_msg),
        assistant_message=_msg_to_dto(assistant_msg),
    )


def _persist_assistant_only(
    repo: ChatRepository,
    thread_id: str,
    user_id: str,
    user_msg: ChatMessage,
    assistant_text: str,
) -> SendMessageResponseDTO:
    safe_text = sanitize_assistant_output(assistant_text)
    assistant_msg = ChatMessage(
        thread_id=thread_id, role=MessageRole.ASSISTANT, content=safe_text,
    )
    repo.append_message(assistant_msg, user_id=user_id)
    return SendMessageResponseDTO(
        thread_id=thread_id,
        user_message=_msg_to_dto(user_msg),
        assistant_message=_msg_to_dto(assistant_msg),
    )


@router.post("/diagnostics/miles-healthcheck", response_model=MilesHealthcheckResponseDTO)
def miles_healthcheck_endpoint(
    payload: MilesHealthcheckRequestDTO | None = None,
    session: AuthSession = SessionDep,
    audit: AuditLogger = AuditDep,
) -> MilesHealthcheckResponseDTO:
    """Diagnóstico INTERNO do vendedor logado: testa cada programa de MILHAS em
    rotas-canário (busca real, hoje+30, só-ida). Expõe nomes de programa de
    propósito (não passa pelo output_filter do chat). Auth + rate-limit baixo
    (faz N chamadas reais às APIs de milhas)."""
    from dataclasses import asdict
    from datetime import datetime, timezone
    import time as _time

    try:
        get_rate_limiter().check_miles_healthcheck(session.user_id)
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Muitos testes seguidos — aguarde {int(e.retry_in_s)}s e tente de novo.",
        )

    programs = payload.programs if payload else None
    audit.log("miles_healthcheck.run", user_id=session.user_id,
              detail={"programs": programs or "all"})

    from backend.app.services.miles_healthcheck import run_miles_healthcheck
    t0 = _time.perf_counter()
    try:
        results = run_miles_healthcheck(programs)
    except Exception as e:  # pipeline nunca derruba — mas o endpoint não pode 500 cru
        logger.exception("miles_healthcheck falhou")
        raise HTTPException(status_code=500, detail=f"Falha no health-check: {e}")
    total_ms = round((_time.perf_counter() - t0) * 1000.0, 1)

    return MilesHealthcheckResponseDTO(
        results=[ProgramHealthDTO(**asdict(r)) for r in results],
        ran_at=datetime.now(timezone.utc).isoformat(),
        total_ms=total_ms,
        ok_count=sum(1 for r in results if r.status == "ok"),
        empty_count=sum(1 for r in results if r.status == "empty"),
        error_count=sum(1 for r in results if r.status in ("error", "timeout")),
    )


# ─── Validação interna (sistema vs. manual) + bug reports ───────────
# CRUD puro: INSERT/SELECT diretos, sem LLM/provider. Zero impacto no pipeline
# de busca. thread_id sem FK cascade → registros sobrevivem ao delete da thread.

def _validation_to_dto(v: QuoteValidation) -> QuoteValidationDTO:
    return QuoteValidationDTO(
        id=v.id, thread_id=v.thread_id, message_id=v.message_id, offer_id=v.offer_id,
        kind=v.kind.value if isinstance(v.kind, ValidationKind) else v.kind,
        system_offer=v.system_offer, found_airline=v.found_airline,
        found_program=v.found_program, emission_method=v.emission_method,
        found_value_brl=v.found_value_brl, found_miles=v.found_miles,
        observations=v.observations, created_at=v.created_at.isoformat(),
    )


@router.post("/validations", response_model=QuoteValidationDTO)
def create_validation_endpoint(
    payload: CreateValidationRequestDTO,
    session: AuthSession = SessionDep,
    repo: ChatRepository = RepoDep,
    audit: AuditLogger = AuditDep,
) -> QuoteValidationDTO:
    """Registra que a cotação do sistema foi VALIDADA (acertou) ou CORRIGIDA
    (vendedor achou melhor). Autossuficiente (snapshot system_offer)."""
    kind = ValidationKind(payload.kind)
    if kind == ValidationKind.CORRECTED and not (payload.found_value_brl or payload.found_miles):
        raise HTTPException(status_code=400, detail="Correção exige valor (R$) ou milhas.")

    # Confere posse da thread — mas NÃO falha se já foi deletada (snapshot basta).
    try:
        if repo.get_thread(payload.thread_id, session.user_id) is None:
            logger.warning("validation: thread %s ausente/deletada — aceitando assim mesmo", payload.thread_id)
    except Exception:
        pass

    obs = payload.observations
    if obs:
        obs = sanitize_user_message(obs, max_chars=2000).text

    v = QuoteValidation(
        user_id=session.user_id, thread_id=payload.thread_id, message_id=payload.message_id,
        offer_id=payload.offer_id, kind=kind, system_offer=payload.system_offer or {},
        found_airline=payload.found_airline if kind == ValidationKind.CORRECTED else None,
        found_program=payload.found_program if kind == ValidationKind.CORRECTED else None,
        emission_method=payload.emission_method if kind == ValidationKind.CORRECTED else None,
        found_value_brl=payload.found_value_brl if kind == ValidationKind.CORRECTED else None,
        found_miles=payload.found_miles if kind == ValidationKind.CORRECTED else None,
        observations=obs if kind == ValidationKind.CORRECTED else None,
    )
    saved = repo.create_validation(v)
    audit.log("validation.create", user_id=session.user_id,
              detail={"kind": payload.kind, "offer_id": payload.offer_id})
    return _validation_to_dto(saved)


@router.get("/validations/stats", response_model=ValidationStatsDTO)
def validation_stats_endpoint(
    session: AuthSession = SessionDep, repo: ChatRepository = RepoDep,
) -> ValidationStatsDTO:
    return ValidationStatsDTO(**repo.validation_stats(session.user_id))


@router.get("/validations/export")
def validation_export_endpoint(
    session: AuthSession = SessionDep, repo: ChatRepository = RepoDep,
) -> StreamingResponse:
    """CSV da tabela comparativa (pra análise fora do app)."""
    cols = ["created_at", "kind", "route", "system_airline", "system_value_brl",
            "found_airline", "found_program", "emission_method", "found_value_brl",
            "found_miles", "delta_brl", "observations"]

    def _gen():
        import csv, io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        yield buf.getvalue(); buf.seek(0); buf.truncate(0)
        for v in repo.list_validations(session.user_id, limit=5000):
            so = v.system_offer or {}
            sys_val = so.get("equivalent_brl") or so.get("price_brl")
            delta = (float(sys_val) - float(v.found_value_brl)) if (sys_val and v.found_value_brl) else ""
            w.writerow([
                v.created_at.isoformat(),
                v.kind.value if isinstance(v.kind, ValidationKind) else v.kind,
                so.get("route", ""), so.get("airline", ""), sys_val if sys_val is not None else "",
                v.found_airline or "", v.found_program or "", v.emission_method or "",
                v.found_value_brl if v.found_value_brl is not None else "",
                v.found_miles if v.found_miles is not None else "",
                round(delta, 2) if delta != "" else "", (v.observations or "").replace("\n", " "),
            ])
            yield buf.getvalue(); buf.seek(0); buf.truncate(0)

    return StreamingResponse(
        _gen(), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=validacoes.csv"},
    )


@router.get("/validations", response_model=List[QuoteValidationDTO])
def list_validations_endpoint(
    kind: Optional[str] = None, limit: int = 200, offset: int = 0,
    session: AuthSession = SessionDep, repo: ChatRepository = RepoDep,
) -> List[QuoteValidationDTO]:
    k = ValidationKind(kind) if kind in ("validated", "corrected") else None
    items = repo.list_validations(session.user_id, kind=k, limit=min(limit, 500), offset=offset)
    return [_validation_to_dto(v) for v in items]


@router.get("/threads/{thread_id}/validations", response_model=List[QuoteValidationDTO])
def thread_validations_endpoint(
    thread_id: str, session: AuthSession = SessionDep, repo: ChatRepository = RepoDep,
) -> List[QuoteValidationDTO]:
    """Pra a UI marcar os cards já validados/corrigidos ao reabrir a thread."""
    return [_validation_to_dto(v) for v in repo.list_validations_by_thread(thread_id, session.user_id)]


# ─── Ranking feedback ("cotação ideal") — rótulo de treino learn-to-rank ───
@router.post("/ranking/ideal", response_model=RankingFeedbackDTO)
def mark_ideal_endpoint(
    payload: MarkIdealRequestDTO,
    session: AuthSession = SessionDep,
    repo: ChatRepository = RepoDep,
    audit: AuditLogger = AuditDep,
) -> RankingFeedbackDTO:
    """Vendedor marca, entre as ofertas apresentadas, qual era a IDEAL. Guarda o
    conjunto de candidatos (snapshot) + a escolhida → semente de treino de ranking.
    Idempotente por turno (único user+thread+message)."""
    thread = repo.get_thread(payload.thread_id, session.user_id)
    candidates: List[Dict[str, Any]] = []
    search_req: Dict[str, Any] = {}
    try:
        msgs = repo.list_messages(payload.thread_id, session.user_id, limit=200)
        msg = next((m for m in msgs if m.id == payload.message_id), None)
        if msg:
            candidates = (msg.metadata or {}).get("offers") or []
        if thread:
            search_req = (thread.state_snapshot or {}).get("slots") or {}
    except Exception as e:
        logger.warning("mark_ideal: falha lendo candidatos: %s", e)

    fb = RankingFeedback(
        user_id=session.user_id, thread_id=payload.thread_id,
        message_id=payload.message_id, ideal_offer_id=payload.offer_id,
        candidates=candidates, search_request=search_req,
    )
    saved = repo.upsert_ranking_feedback(fb)
    audit.log("ranking.ideal", user_id=session.user_id, thread_id=payload.thread_id,
              detail={"offer_id": payload.offer_id, "n_candidates": len(candidates)})
    return RankingFeedbackDTO(
        id=saved.id, thread_id=saved.thread_id, message_id=saved.message_id,
        ideal_offer_id=saved.ideal_offer_id, created_at=saved.created_at.isoformat(),
    )


@router.get("/threads/{thread_id}/ranking", response_model=List[RankingFeedbackDTO])
def thread_ranking_endpoint(
    thread_id: str, session: AuthSession = SessionDep, repo: ChatRepository = RepoDep,
) -> List[RankingFeedbackDTO]:
    """Pra a UI marcar quais ofertas já foram apontadas como ideais ao reabrir a thread."""
    return [
        RankingFeedbackDTO(
            id=f.id, thread_id=f.thread_id, message_id=f.message_id,
            ideal_offer_id=f.ideal_offer_id, created_at=f.created_at.isoformat(),
        )
        for f in repo.list_ranking_feedback_by_thread(thread_id, session.user_id)
    ]


@router.post("/bug-reports", response_model=BugReportDTO)
def create_bug_report_endpoint(
    payload: CreateBugReportRequestDTO,
    session: AuthSession = SessionDep,
    repo: ChatRepository = RepoDep,
    audit: AuditLogger = AuditDep,
) -> BugReportDTO:
    desc = sanitize_user_message(payload.description, max_chars=2000).text
    context = dict(payload.context or {})
    # Completa com a última mensagem do assistente (consulta leve), se não veio.
    if "last_assistant_message_id" not in context:
        try:
            msgs = repo.list_messages(payload.thread_id, session.user_id, limit=20)
            last_ai = next((m for m in reversed(msgs) if m.role == MessageRole.ASSISTANT), None)
            if last_ai:
                context["last_assistant_message_id"] = last_ai.id
        except Exception:
            pass
    b = BugReport(user_id=session.user_id, thread_id=payload.thread_id,
                  description=desc, context=context)
    saved = repo.create_bug_report(b)
    audit.log("bug_report.create", user_id=session.user_id, detail={"thread_id": payload.thread_id})
    return BugReportDTO(id=saved.id, thread_id=saved.thread_id, description=saved.description,
                        context=saved.context, status=saved.status,
                        created_at=saved.created_at.isoformat())


@router.get("/bug-reports", response_model=List[BugReportDTO])
def list_bug_reports_endpoint(
    status: Optional[str] = None, session: AuthSession = SessionDep,
    repo: ChatRepository = RepoDep,
) -> List[BugReportDTO]:
    items = repo.list_bug_reports(session.user_id, status=status)
    return [BugReportDTO(id=b.id, thread_id=b.thread_id, description=b.description,
                         context=b.context, status=b.status, created_at=b.created_at.isoformat())
            for b in items]
