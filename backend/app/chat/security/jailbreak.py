"""Detector de prompt injection / jailbreak attempts.

Heurística de alta precisão / cobertura média — usa lista de padrões
conhecidos de jailbreak em PT/EN. Não detecta tudo (LLMs criativos
contornam patterns), por isso usamos defesa em camadas:

1. System prompt firme com instruções defensivas (em `prompts.py`).
2. Detector aqui — bloqueia tentativas óbvias.
3. Output filter — sanitiza vazamento mesmo se o LLM for enganado.

Resposta padrão para detecção: refusa com mensagem genérica + audit log.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


# Padrões de instrução de override. Case-insensitive. PT-BR + EN.
# Estes são padrões "imperativos" — pedem para o modelo ignorar/revelar/mudar comportamento.
_INJECTION_PATTERNS: List[tuple[str, str]] = [
    # English
    (r"ignore (all |any |the )?(previous|prior|above)\s+(instructions?|prompts?|rules?)", "ignore_prev"),
    (r"disregard (all |any |the )?(previous|prior|above)\s+(instructions?|prompts?|rules?)", "disregard"),
    (r"forget (all |any |the )?(previous|prior|above)\s+(instructions?|prompts?|rules?)", "forget"),
    (r"you are no longer\s+(an? )?(assistant|ai|gpt)", "role_swap"),
    (r"act as\s+(an? )?(?!travel|agent|seller|atendente)", "act_as_other"),
    (r"reveal (your|the) (system|initial|original)\s+(prompt|instructions?)", "leak_prompt"),
    (r"print (your|the) (system|initial|original)\s+(prompt|instructions?)", "leak_prompt"),
    (r"show me (your|the) (system|initial|original)\s+(prompt|instructions?)", "leak_prompt"),
    (r"what (is|are) your (system|initial|original)\s+(prompt|instructions?)", "leak_prompt"),
    (r"\bDAN\b|do anything now", "dan_jailbreak"),
    (r"developer mode|debug mode|admin mode", "fake_mode"),
    (r"jailbreak", "explicit"),
    # PT-BR
    (r"ignor[ea]r?\s+(?:\w+\s+){0,3}(instru[cç][oõ]es|prompts?|regras?)\s+(anteriores|prévias|acima)", "ignore_pt"),
    (r"esque[çc][ae]\w*\s+(?:\w+\s+){0,3}(instru[cç][oõ]es|prompts?|regras?)", "forget_pt"),
    (r"voc[eê] (n[aã]o é mais|agora é)\s+(uma? )?(assistente|ia|gpt|atendente)", "role_swap_pt"),
    (r"finja (que )?(é|você é|ser)\s+(?!o\s+atendente|a\s+atendente)", "pretend_pt"),
    (r"(revele|mostre|imprima)\s+(seu|o)\s+(prompt|instru[cç][oõ]es|sistema)", "leak_prompt_pt"),
    (r"(qual|quais)\s+(é|são|seu|seus)\s+(prompt|instru[cç][oõ]es)\s+(de\s+sistema|inicial|original)", "leak_prompt_pt"),
    # Cobre "qual é SEU prompt de sistema", "me diga o system prompt", etc.
    (r"\b(system\s+prompt|prompt\s+d[eo]\s+sistema)\b", "leak_prompt_sys"),
    (r"modo\s+(desenvolvedor|debug|admin|administrador)", "fake_mode_pt"),
    # Token / format smuggling
    (r"</?(system|instruction|prompt)>", "tag_smuggle"),
    (r"\[\[(system|instruction|prompt)\]\]", "tag_smuggle"),
    (r"```\s*system", "fence_smuggle"),
]

# Padrões de exfiltração / engenharia social fora do escopo.
# Esses NÃO são jailbreak puros, mas são pedidos suspeitos que devem ser
# escalonados (audit + recusa genérica), não respondidos.
_OFF_TOPIC_HOSTILE: List[tuple[str, str]] = [
    (r"como (eu )?(hackear|burlar|fraudar|enganar)", "hostile_ptbr"),
    (r"how (do i|can i)\s+(hack|bypass|defraud|cheat)", "hostile_en"),
    (r"(dados|informações)\s+(de outros|de outras pessoas|de clientes|do banco)", "data_exfil"),
]


@dataclass(frozen=True)
class JailbreakResult:
    flagged: bool
    pattern_id: Optional[str] = None
    severity: str = "info"          # "info" | "warn" | "block"
    sample: Optional[str] = None    # trecho que disparou (pra audit)


def detect_jailbreak(text: str) -> JailbreakResult:
    """Rápido (regex puro, sem LLM). Use em todo input antes do agente."""
    if not text:
        return JailbreakResult(flagged=False)

    normalized = text.lower()

    for pattern, pid in _INJECTION_PATTERNS:
        m = re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return JailbreakResult(
                flagged=True,
                pattern_id=pid,
                severity="block",
                sample=text[max(0, m.start() - 10): m.end() + 10],
            )

    for pattern, pid in _OFF_TOPIC_HOSTILE:
        m = re.search(pattern, normalized, flags=re.IGNORECASE)
        if m:
            return JailbreakResult(
                flagged=True,
                pattern_id=pid,
                severity="warn",
                sample=text[max(0, m.start() - 10): m.end() + 10],
            )

    return JailbreakResult(flagged=False)
