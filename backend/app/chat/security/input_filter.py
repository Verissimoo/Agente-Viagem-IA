"""Sanitização de input do usuário antes de chegar no LLM.

Defesas implementadas:
- Limite de tamanho (`max_message_chars`) — derruba payloads gigantes.
- Strip de control chars (exceto \\n, \\t) — evita injeção de tokens raros.
- Normaliza whitespace excessivo.
- Bloqueia mensagens com 0 conteúdo após sanitização.

NÃO faz detecção semântica de jailbreak — esse é o trabalho de `jailbreak.py`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from backend.app.chat.config import settings


class InputViolation(Exception):
    """Mensagem rejeitada antes de chegar no LLM."""


# Control chars [U+0000..U+001F] exceto \t (0x09) e \n (0x0A),
# DEL (0x7F), bidi/format chars no Unicode (U+200B..U+200F, U+202A..U+202E,
# U+2060..U+206F, U+FEFF).
_CONTROL_CHARS_RE = re.compile(
    "["
    "\x00-\x08"          # NULL..BS (sem \t \n)
    "\x0b\x0c"           # VT, FF
    "\x0e-\x1f"          # SO..US
    "\x7f"               # DEL
    "​-‏"      # zero-width + bidi
    "‪-‮"      # bidi override
    "⁠-⁯"      # word joiner + invisible
    "﻿"             # BOM
    "]"
)
_EXCESSIVE_WHITESPACE_RE = re.compile(r"[ \t]{3,}")
_EXCESSIVE_NEWLINES_RE = re.compile(r"\n{4,}")


@dataclass(frozen=True)
class SanitizedInput:
    text: str
    original_length: int


def sanitize_user_message(raw: str, *, max_chars: int | None = None) -> SanitizedInput:
    """Sanitiza e devolve a mensagem pronta pro LLM.

    Levanta `InputViolation` se a mensagem for inutilizável (vazia ou só lixo).
    """
    if raw is None:
        raise InputViolation("Mensagem vazia")

    text = str(raw)
    original_length = len(text)

    max_chars = max_chars or settings.max_message_chars
    if original_length > max_chars:
        # Trunca em vez de rejeitar — UX melhor pra mensagens longas honestas.
        # Mas se for absurdamente grande (>4x), aí sim rejeita por suspeita.
        if original_length > max_chars * 4:
            raise InputViolation(
                f"Mensagem muito longa ({original_length} chars; limite {max_chars})"
            )
        text = text[:max_chars]

    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_CHARS_RE.sub("", text)
    text = _EXCESSIVE_WHITESPACE_RE.sub("  ", text)
    text = _EXCESSIVE_NEWLINES_RE.sub("\n\n\n", text)
    text = text.strip()

    if not text:
        raise InputViolation("Mensagem vazia após sanitização")

    return SanitizedInput(text=text, original_length=original_length)
