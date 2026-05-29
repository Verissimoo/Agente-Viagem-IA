"""Factory de modelo LLM — abstrai Anthropic/Groq por trás de uma interface comum.

Lazy import dos packages — assim o módulo importa mesmo sem langchain instalado
(útil pra rodar testes que mockeiam o LLM).
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from backend.app.chat.config import settings


def _make_anthropic(model: str, *, temperature: float, max_tokens: int) -> Any:
    from langchain_anthropic import ChatAnthropic
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY ausente — configure no .env ou troque CHAT_MODEL_PROVIDER."
        )
    return ChatAnthropic(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=30,
    )


def _make_groq(model: str, *, temperature: float, max_tokens: int) -> Any:
    # langchain-groq se disponível; senão erro claro.
    try:
        from langchain_groq import ChatGroq
    except ImportError as e:
        raise RuntimeError(
            "langchain-groq não instalado. Rode pip install langchain-groq."
        ) from e
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY ausente.")
    return ChatGroq(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=30,
    )


def get_chat_model(
    *,
    primary: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> Any:
    """Retorna um BaseChatModel pronto para `.invoke(messages)`.

    `primary=False` retorna o modelo barato (haiku ou llama-rápido) — use
    em tarefas triviais (classificação, extração de slot por turno).
    """
    model_name = settings.primary_model if primary else settings.cheap_model
    provider = settings.model_provider.lower()

    if provider == "anthropic":
        return _make_anthropic(model_name, temperature=temperature, max_tokens=max_tokens)
    if provider == "groq":
        return _make_groq(model_name, temperature=temperature, max_tokens=max_tokens)
    raise RuntimeError(f"Provider desconhecido: {provider}")


@lru_cache(maxsize=4)
def get_cached_chat_model(primary: bool = True) -> Any:
    """Cacheia modelos com defaults — evita re-criar ChatAnthropic a cada turno."""
    return get_chat_model(primary=primary)
