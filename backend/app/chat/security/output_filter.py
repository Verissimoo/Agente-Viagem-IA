"""Output filter — sanitiza resposta do assistente antes do usuário ver.

Objetivos:
1. **Esconder fontes/providers**. Vendedor não pode saber que usamos Skiplagged,
   Kayak, BuscaMilhas, MCP, Economilhas — esse é o "segredo" da operação.
2. **NÃO esconder o tipo de oferta**. Termos comerciais como "hidden city",
   "split", "direto", "milhas", "cash" são informação operacional que o
   vendedor PRECISA pra atender bem o cliente. Mantemos como estão.
3. **Não revelar instruções do sistema** (caso o LLM tenha sido enganado).
4. **Detectar e remover** marcadores internos de debug acidentais
   (\\<DEBUG\\>, [INTERNAL], etc.).
5. Esconder mecânica de scraping/crawler — *como* descobrimos é segredo,
   mas *o que é* a oferta é informação do vendedor.

IMPORTANTE: este filtro é a ÚLTIMA linha de defesa. O system prompt já instrui
o LLM a não usar esses termos — mas se ele escorregar, aqui pegamos.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Tuple


# (regex, replacement). Ordem importa — mais específico primeiro.
# Tudo case-insensitive. Word boundaries onde faz sentido.
_REPLACEMENTS: List[Tuple[str, str]] = [
    # Provider/site/API names → "nossas fontes"
    (r"\bskiplagged\b", "nossa rede de cotação"),
    (r"\bbusca[\s\-]?milhas?\b", "nossa rede de cotação"),
    (r"\bbuscamilhas[_\s\-]?azul[_\s\-]?cash\b", "Azul Oficial"),
    (r"\beconomilhas?\b", "nossa rede de cotação"),
    (r"\bmcp(?:[\s\-]?award)?\b", "nossa rede de cotação"),
    (r"\bkayak\b", "nossa rede de cotação"),
    (r"\bawardtravelfinder\b", "nossa rede de cotação"),
    (r"\bqatar[\s\-]?mcp\b", "nossa rede de cotação"),
    (r"\brapid\s?api\b", "nossa rede de cotação"),

    # Mecânica/técnica de coleta — escondida (como descobrimos)
    (r"\bscraping\b", "consulta"),
    (r"\bcrawler\b", "consulta"),
    (r"\bweb[\s\-]?scraper\b", "consulta"),
    (r"\bself[\s\-]?transfer\b", "conexão auto-gerenciada"),

    # Variáveis e nomes internos que podem vazar do prompt
    (r"\bUnifiedOffer\b", "oferta"),
    (r"\bSearchRequest\b", "cotação"),
    (r"\bPipelineResult\b", "resultado"),
    (r"\bunified_offer\b", "oferta"),
    (r"\bsearch_request\b", "cotação"),

    # Tags / markers de debug que escapam
    (r"<\s*(DEBUG|INTERNAL|SYSTEM|TOOL_USE)\s*>.*?<\s*/\s*\1\s*>", ""),
    (r"\[(?:DEBUG|INTERNAL|SYSTEM)\][^\n]*", ""),
]

# Padrões cuja presença indica vazamento sério — derrubam a mensagem inteira
# em vez de só sanitizar. Mais paranoico, mas garante que system prompt e
# nomes de provider em CONTEXTOS suspeitos não passem.
_CRITICAL_LEAKS: List[str] = [
    r"my (system )?prompt (is|says)",
    r"meu prompt (de sistema|do sistema)?\s*(é|diz)",
    r"i was (told|instructed) to",
    r"fui (instruído|orientado) a",
    r"according to my (system )?instructions",
    r"de acordo com minhas instruções (de sistema)?",
]

# Mensagem genérica usada quando precisamos truncar a resposta.
_SAFE_FALLBACK = (
    "Desculpe, não consegui formular uma resposta apropriada para essa consulta. "
    "Pode reformular o pedido? Estou aqui pra te ajudar a cotar passagens — "
    "me conte para onde quer voar."
)


def _replace_all(text: str, rules: Iterable[Tuple[str, str]]) -> str:
    out = text
    for pattern, repl in rules:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE | re.DOTALL)
    return out


def sanitize_assistant_output(text: str) -> str:
    """Retorna texto sanitizado pronto para o vendedor ler.

    - Substitui nomes de providers e jargão técnico.
    - Remove blocos `<DEBUG>...</DEBUG>` etc.
    - Se detectar vazamento crítico de prompt, devolve fallback seguro.
    """
    if not text:
        return text

    for leak_pattern in _CRITICAL_LEAKS:
        if re.search(leak_pattern, text, flags=re.IGNORECASE):
            return _SAFE_FALLBACK

    out = _replace_all(text, _REPLACEMENTS)
    # Compactar múltiplas substituições adjacentes ("nossa rede de cotação, nossa rede de cotação")
    out = re.sub(
        r"(nossa rede de cotação)(?:[,\s]+nossa rede de cotação)+",
        r"\1",
        out,
        flags=re.IGNORECASE,
    )
    # Espaços duplos resultantes
    out = re.sub(r"  +", " ", out).strip()
    return out or _SAFE_FALLBACK
