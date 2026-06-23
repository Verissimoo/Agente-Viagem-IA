"""Segurança de CONTEÚDO — recusa pedidos fora do escopo de viagens.

Complementa `jailbreak.py` (que pega prompt-injection). Aqui pegamos conteúdo
NOCIVO / NSFW / fora de escopo que um atendente de **passagens aéreas** nunca
deve responder. Política: **recusar com educação e redirecionar pra viagem** —
nunca produzir o conteúdo.

Camada de regex (alta precisão, baixo falso-positivo): só dispara em INTENÇÃO
clara. É deliberadamente conservador pra não bloquear consulta legítima de
destino (ex.: "voo para Amsterdam", "passagem pra Tailândia").

Categorias (ver docs/GUARDRAILS.md):
  sexual   — conteúdo sexual/NSFW explícito
  violence — armas, explosivos, instruções de dano físico
  illegal  — drogas/contrabando/lavagem/fraude (com verbo de intenção)
  self_harm— autoagressão/suicídio (resposta acolhedora + redirecionamento)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# (regex, categoria). Case-insensitive. PT-BR + EN. Word boundaries pra evitar
# falso-positivo em nomes de cidade/aeroporto.
_PATTERNS: List[Tuple[str, str]] = [
    # ── Sexual / NSFW ──────────────────────────────────────────────
    (r"\b(porn[oô]?|pornografia|xvideos?|onlyfans|hentai|nsfw)\b", "sexual"),
    (r"\b(sexo|transar|pelad[ao]s?|nudes?|nud[ez]|garota\s+de\s+programa)\b", "sexual"),
    (r"\b(porn|nude|naked|blowjob|escort\s+service|sexual\s+content)\b", "sexual"),
    (r"\b(conte[uú]do|hist[oó]ria|texto)\s+(er[oó]tic[oa]|sexual)\b", "sexual"),
    # ── Violência / armas / explosivos ─────────────────────────────
    (r"\bcomo\s+(fazer|fabricar|montar|construir)\s+(uma?\s+)?(bomba|explosivo|arma)", "violence"),
    (r"\bhow\s+to\s+(make|build|create)\s+a?\s*(bomb|explosive|weapon|gun)", "violence"),
    (r"\b(comprar|conseguir|onde\s+comprar)\s+(uma?\s+)?(arma|pistola|fuzil|munição)", "violence"),
    (r"\bcomo\s+(matar|assassinar|envenenar|esfaquear)\b", "violence"),
    (r"\bhow\s+to\s+(kill|murder|poison|stab)\s+(someone|a\s+person|him|her)", "violence"),
    # ── Ilícito: drogas / contrabando / lavagem / fraude ───────────
    (r"\b(comprar|vender|traficar|produzir|sintetizar)\s+(droga|coca[ií]na|maconha|crack|metanfetamina|lsd)", "illegal"),
    (r"\bcomo\s+(contrabandear|lavar\s+dinheiro|sonegar|fraudar|clonar\s+cart[aã]o)", "illegal"),
    (r"\bhow\s+to\s+(launder\s+money|smuggle|forge|counterfeit|evade\s+taxes)", "illegal"),
    (r"\b(buy|sell|make)\s+(drugs|cocaine|meth|heroin|fake\s+passport)", "illegal"),
    # ── Autoagressão / suicídio ────────────────────────────────────
    (r"\b(quero|vou|penso\s+em)\s+(me\s+)?(matar|suicidar|tirar\s+minha\s+vida)", "self_harm"),
    (r"\b(suic[ií]dio|me\s+machucar|automutila)", "self_harm"),
    (r"\b(kill\s+myself|end\s+my\s+life|self[\s\-]?harm|commit\s+suicide)\b", "self_harm"),
    # ── Malware / invasão ──────────────────────────────────────────
    (r"\b(criar?|escrev[ae]|fazer|desenvolver|gera?r?)\s+(um\s+)?(v[ií]rus|malware|ransomware|keylogger|trojan|phishing)\b", "malware"),
    (r"\b(write|create|build|code)\s+(a\s+)?(virus|malware|ransomware|keylogger|trojan|exploit|phishing\s+page)\b", "malware"),
    (r"\bcomo\s+(invadir|hackear|derrubar|clonar)\s+(?:\w+\s+){0,2}(site|servidor|conta|sistema|rede|wi-?fi|whatsapp|instagram|e-?mail|celular)\b", "malware"),
    (r"\b(sql\s+injection|ddos\s+attack|como\s+roubar\s+senhas?)\b", "malware"),
    # ── Off-topic / abuso de LLM (queima nossos tokens) ────────────
    (r"\b(escrev[ae]|fa[çc]a|cri[ae]|gera?r?|me\s+(ajud[ae]|d[êe]))\s+(um|uma)?\s*(c[oó]digo|script|programa|fun[çc][aã]o|query)\b", "off_topic"),
    (r"\b(write|generate|create|fix|debug)\s+(me\s+)?(a\s+|some\s+)?(code|script|program|function|sql)\b", "off_topic"),
    (r"\b(fa[çc]a|escrev[ae]|me\s+ajud[ae]\s+(com|na|no))\s+.{0,12}(reda[çc][aã]o|disserta[çc][aã]o|tese|monografia|tcc|dever\s+de\s+casa|li[çc][aã]o\s+de\s+casa)\b", "off_topic"),
    (r"\b(do|write)\s+my\s+(essay|homework|assignment|thesis|paper)\b", "off_topic"),
    (r"\b(traduz[ae]?|traduzir|translate)\s+(esse|este|isso|isto|o\s+(texto|seguinte)|this|the\s+following)\b", "off_topic"),
    (r"\b(me\s+cont[ae]|cont[ae]|tell\s+me)\s+(uma?\s+)?(piada|joke)\b", "off_topic"),
    (r"\b(escrev[ae]|fa[çc]a|write)\s+(um|uma|a)\s+(poema|poesia|m[uú]sica|reda[çc][aã]o|poem|song|essay)\b", "off_topic"),
    (r"\breceita\s+(de|para)\s+(?!emiss[aã]o|milhas)\w+", "off_topic"),
    (r"\b(responda|aja|comporte-se|act|respond|behave)\s+(como|as|like)\s+(o\s+|a\s+)?(chatgpt|gpt-?\d?|claude|gemini|bing|copilot)\b", "off_topic"),
    (r"\b(resolva|calcule|solve|calculate)\b.{0,30}\b(equa[çc][aã]o|integral|derivada|equation|matem[aá]tica)\b", "off_topic"),
]


@dataclass(frozen=True)
class ContentSafetyResult:
    flagged: bool
    category: Optional[str] = None        # sexual | violence | illegal | self_harm
    sample: Optional[str] = None


def check_content_safety(text: str) -> ContentSafetyResult:
    """Regex puro (sem LLM). Use em todo input, junto do detect_jailbreak."""
    if not text:
        return ContentSafetyResult(flagged=False)
    low = text.lower()
    for pattern, cat in _PATTERNS:
        m = re.search(pattern, low, flags=re.IGNORECASE)
        if m:
            return ContentSafetyResult(
                flagged=True, category=cat,
                sample=text[max(0, m.start() - 10): m.end() + 10],
            )
    return ContentSafetyResult(flagged=False)


# Respostas de recusa por categoria — educadas, sem produzir o conteúdo, sempre
# redirecionando pra viagem. self_harm recebe um tom acolhedor.
_REFUSALS = {
    "self_harm": (
        "Sinto muito que você esteja passando por isso — e isso é sério demais "
        "pra mim, que sou só um atendente de viagens. Por favor, procure o **CVV "
        "(ligue 188)**, é gratuito e sigiloso, 24h. Se for uma emergência, ligue "
        "**192**. Estou aqui se quiser, em outro momento, planejar uma viagem."
    ),
    "_default": (
        "Desculpa, mas com isso eu não consigo ajudar — sou o atendente de "
        "**passagens aéreas** da Passagens com Desconto. Posso te ajudar a cotar "
        "um voo: é só me dizer a rota e a data. ✈️"
    ),
}


def refusal_message(category: Optional[str]) -> str:
    """Mensagem de recusa apropriada pra categoria (redireciona pra viagem)."""
    return _REFUSALS.get(category or "_default", _REFUSALS["_default"])
