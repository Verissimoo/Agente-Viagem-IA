"""
Conversão centralizada milhas/pontos → BRL.

Antes desta unificação, as taxas viviam duplicadas em
streamlit_app_multiagent.RATES e em pcd/core/ranking (cpm hardcoded
por SourceType). As listas estavam dessincronizadas — TAP, INTERLINE,
COPA e AMERICAN AIRLINES caíam no fallback no ranking mas tinham
valor próprio na UI, gerando custos divergentes para a mesma oferta.

Lookup ordenado: programa de milhagem > nome da companhia > default.
"""
from typing import Optional

from pcd.core.schema import SourceType, UnifiedOffer

# Valor em BRL de 1 milha/ponto, por programa ou companhia.
# Mantenha esta tabela como fonte única da verdade.
# A chave reservada "DEFAULT" é o fallback global (a UI pode editá-la).
RATES_BRL_PER_MILE: dict[str, float] = {
    # Buscamilhas — nacionais
    "LATAM":     0.0285,
    "GOL":       0.0200,
    "AZUL":      0.0200,
    # Buscamilhas — internacionais
    "TAP":               0.0220,
    "AMERICAN AIRLINES": 0.0220,
    "INTERLINE":         0.0200,
    "COPA":              0.0200,
    "IBERIA":            0.0700,  # Avios
    # MCP Award Travel Finder — programas
    "AVIOS":      0.0700,  # British Airways / Qatar / Iberia
    "ASIA MILES": 0.0650,  # Cathay Pacific
    # Fallback global
    "DEFAULT":    0.0210,
}

INTERNATIONAL_FALLBACK_BRL_PER_MILE: float = 0.0500

# Mapeia SourceType para a chave correspondente em RATES_BRL_PER_MILE.
# Usado quando airline/program não permitem identificar o programa
# (ex.: testes que setam airline="LA" em vez de "LATAM").
_SOURCE_TO_RATE_KEY: dict[SourceType, str] = {
    SourceType.BUSCAMILHAS_LATAM:     "LATAM",
    SourceType.BUSCAMILHAS_GOL:       "GOL",
    SourceType.BUSCAMILHAS_AZUL:      "AZUL",
    SourceType.BUSCAMILHAS_TAP:       "TAP",
    SourceType.BUSCAMILHAS_IBERIA:    "IBERIA",
    SourceType.BUSCAMILHAS_AMERICAN:  "AMERICAN AIRLINES",
    SourceType.BUSCAMILHAS_INTERLINE: "INTERLINE",
    SourceType.BUSCAMILHAS_COPA:      "COPA",
}


def cost_per_mile(
    airline: str = "",
    program: str = "",
    source: Optional[SourceType] = None,
) -> float:
    """Retorna o BRL por milha aplicável.

    Resolução:
      1. Match exato/substring no programa (ex.: 'Avios', 'Asia Miles').
      2. Match no nome da companhia.
      3. Fallback internacional para fontes MCP sem programa identificado.
      4. DEFAULT_BRL_PER_MILE.
    """
    prog = (program or "").upper()
    if prog:
        for k, v in RATES_BRL_PER_MILE.items():
            if k == "DEFAULT":
                continue
            if k in prog:
                return v

    air = (airline or "").upper()
    if air:
        for k, v in RATES_BRL_PER_MILE.items():
            if k == "DEFAULT":
                continue
            if k in air:
                return v

    if source is not None:
        key = _SOURCE_TO_RATE_KEY.get(source)
        if key and key in RATES_BRL_PER_MILE:
            return RATES_BRL_PER_MILE[key]
        if source in (SourceType.MCP_AWARD, SourceType.MCP_QATAR):
            return INTERNATIONAL_FALLBACK_BRL_PER_MILE

    return RATES_BRL_PER_MILE.get("DEFAULT", 0.0210)


def miles_to_brl(
    miles,
    airline: str = "",
    program: str = "",
    source: Optional[SourceType] = None,
) -> float:
    """Converte uma quantidade de milhas em BRL usando cost_per_mile."""
    try:
        m = float(miles or 0)
    except (TypeError, ValueError):
        return 0.0
    return m * cost_per_mile(airline, program, source)


def offer_equivalent_brl(offer: UnifiedOffer) -> float:
    """Custo total da oferta em BRL: dinheiro direto, ou milhas*cpm + taxas."""
    if offer.price_brl is not None:
        return offer.price_brl
    if offer.miles is not None:
        cpm = cost_per_mile(
            airline=offer.airline or "",
            program=offer.miles_program or "",
            source=offer.source,
        )
        return offer.miles * cpm + (offer.taxes_brl or 0.0)
    return 0.0
