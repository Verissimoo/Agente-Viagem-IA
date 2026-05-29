"""FastAPI entry point.

Run locally with:
    uvicorn backend.app.main:app --reload --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.v1.routes import (
    health,
    miles_match,
    nlp,
    rates,
    search,
    smart_quote,
    split,
    validate,
)

app = FastAPI(
    title="Agente Viagem IA",
    description=(
        "API de busca inteligente de passagens aéreas com integração "
        "Skiplagged (hidden-city + split cash) somada aos provedores "
        "de milhas (BuscaMilhas, MCP Award, Economilhas) e cash (Kayak)."
    ),
    version="1.0.0",
)

# CORS aberto em dev. Em produção, restringir ao host do frontend.
# allow_credentials=True com origins=["*"] é spec-inválido — o browser rejeita
# preflights POST com JSON e devolve "Status 0 / Unknown Error" para o app.
# Não usamos cookies/auth header cross-origin, então credentials=False.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_V1_PREFIX = "/api/v1"
app.include_router(health.router, prefix=API_V1_PREFIX)
app.include_router(nlp.router, prefix=API_V1_PREFIX)
app.include_router(rates.router, prefix=API_V1_PREFIX)
app.include_router(search.router, prefix=API_V1_PREFIX)
app.include_router(split.router, prefix=API_V1_PREFIX)
app.include_router(miles_match.router, prefix=API_V1_PREFIX)
app.include_router(smart_quote.router, prefix=API_V1_PREFIX)
app.include_router(validate.router, prefix=API_V1_PREFIX)

# Chat product — independente do gerencial. Carrega só se a feature flag estiver
# ligada (CHAT_ENABLED=1). Import lazy pra não derrubar o boot do gerencial
# se langgraph/anthropic estiverem ausentes em algum ambiente.
from backend.app.chat.config import settings as _chat_settings  # noqa: E402
if _chat_settings.enabled:
    try:
        from backend.app.api.v1.chat import router as chat_router
        app.include_router(chat_router, prefix=API_V1_PREFIX)
    except Exception as _chat_import_error:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Chat product não carregado (%s) — instale dependências (langgraph, "
            "langchain-anthropic, psycopg) para habilitar.", _chat_import_error,
        )


@app.get("/")
def root():
    return {
        "service": "Agente Viagem IA",
        "version": "1.0.0",
        "docs": "/docs",
        "api": API_V1_PREFIX,
    }
