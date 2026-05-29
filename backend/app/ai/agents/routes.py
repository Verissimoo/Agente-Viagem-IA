"""Classificação de rota (doméstica vs internacional) — pra decidir quais
validações vale a pena rodar.

Regras de negócio:
- **Doméstico** (origem E destino em BR): split não vale a pena (não há hub
  forte tipo SP pra arbitragem inter-cias). Hidden city e Kayak sim.
- **Internacional**: split vale (ex.: BSB→LIS via GRU pode sair mais barato
  que direto). Tudo roda.
"""
from __future__ import annotations

from typing import Optional, Tuple


# IATAs brasileiros — usados pra classificar rota como doméstica.
# Lista do maior pro menor por movimento (cobre ~98% dos voos comerciais BR).
_BR_IATAS: frozenset[str] = frozenset({
    # Hubs grandes
    "GRU", "CGH", "VCP",   # São Paulo
    "GIG", "SDU",          # Rio
    "BSB",                 # Brasília
    "CNF", "PLU",          # Belo Horizonte
    "POA",                 # Porto Alegre
    "CWB",                 # Curitiba
    "FLN",                 # Florianópolis
    "SSA",                 # Salvador
    "REC",                 # Recife
    "FOR",                 # Fortaleza
    "MAO",                 # Manaus
    # Médios
    "BEL", "SLZ", "NAT", "JPA", "MCZ", "AJU", "BPS", "IOS",
    "VIX", "GYN", "CGB", "CGR", "PMW", "THE",
    "NVT", "JOI", "XAP", "MGF", "LDB", "IGU", "CAC", "CXJ",
    "PFB", "RIA", "PET", "BVB", "PVH", "MCP", "RBR",
    "UDI", "RAO", "JDF", "SJP", "PPB", "JTC",
    # Regionais comuns em buscas B2B
    "MII", "SOD", "CMG", "AAX", "GVR", "ITR", "URG", "ROO",
    "VOK", "LEC", "JJD", "QSC", "JPR", "ARU", "CAU",
})


def is_br_iata(code: Optional[str]) -> bool:
    """True se o IATA é brasileiro (cobertura ~98% dos voos comerciais BR)."""
    if not code:
        return False
    return str(code).upper().strip() in _BR_IATAS


def is_domestic_route(origin: Optional[str], destination: Optional[str]) -> bool:
    """Rota doméstica = origem E destino no Brasil."""
    return is_br_iata(origin) and is_br_iata(destination)


def classify_route(origin: Optional[str], destination: Optional[str]) -> str:
    """Retorna 'domestic' | 'international' | 'unknown'."""
    o_br, d_br = is_br_iata(origin), is_br_iata(destination)
    if o_br and d_br:
        return "domestic"
    if not (o_br or d_br):
        return "unknown"   # rota estrangeira-estrangeira (raro no nosso caso)
    return "international"
