"""Miles/points → BRL conversion with tiered rates by purchase volume.

Rates are loaded from `rates.json` (same directory). Each program has a list
of tiers `[{"max_miles": int|None, "rate": float}, ...]`. The first tier
whose `max_miles >= needed_miles` wins; `null` means unbounded top tier.

Lookup order for the program key:
  1. Exact/substring match in `program` arg (e.g. "Avios", "Asia Miles").
  2. Exact/substring match in `airline` arg.
  3. SourceType mapping (BUSCAMILHAS_LATAM → "LATAM").
  4. International fallback for MCP_AWARD / MCP_QATAR.
  5. "DEFAULT" program.

Public API:
  - cost_per_mile(airline, program, source, miles=None) → float
  - miles_to_brl(miles, airline, program, source) → float
  - offer_equivalent_brl(offer) → float
  - skiplagged_estimation_program() → str  (program used for cash-to-miles estimate)
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from backend.app.domain.models import SourceType, UnifiedOffer

_RATES_FILE = Path(__file__).with_name("rates.json")


def _load_rates() -> dict:
    with _RATES_FILE.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _rates_config() -> dict:
    """Cached at process start. Restart server to pick up rates.json edits."""
    return _load_rates()


def reload_rates() -> None:
    """Forces re-reading rates.json (test/dev convenience)."""
    _rates_config.cache_clear()


def get_rates_snapshot() -> dict:
    """Returns the full rates config (programs + metadata) for the API."""
    cfg = _rates_config()
    return {
        "programs": cfg.get("programs", {}),
        "international_fallback_rate": cfg.get("international_fallback_rate", 0.05),
        "skiplagged_estimation_program": cfg.get("_skiplagged_estimation_program", "GOL"),
    }


def update_rates(payload: dict) -> dict:
    """Writes a new rates table to rates.json and clears the cache.

    Accepts the same shape returned by `get_rates_snapshot()`. Validates that
    each program has at least one tier with a positive rate and that the last
    tier's max_miles is null (unbounded top tier).
    """
    programs = payload.get("programs")
    if not isinstance(programs, dict) or not programs:
        raise ValueError("`programs` must be a non-empty object")

    for prog, tiers in programs.items():
        if not isinstance(tiers, list) or not tiers:
            raise ValueError(f"Program '{prog}' must have at least one tier")
        for t in tiers:
            r = t.get("rate")
            if not isinstance(r, (int, float)) or r <= 0:
                raise ValueError(f"Program '{prog}': every tier must have a positive `rate`")
        if tiers[-1].get("max_miles") is not None:
            raise ValueError(f"Program '{prog}': last tier must have `max_miles: null`")

    # Preserve metadata fields (anything starting with _)
    current = _rates_config()
    merged = {k: v for k, v in current.items() if k.startswith("_") or k == "international_fallback_rate"}
    merged["programs"] = programs
    if "international_fallback_rate" in payload:
        merged["international_fallback_rate"] = float(payload["international_fallback_rate"])
    if "skiplagged_estimation_program" in payload:
        merged["_skiplagged_estimation_program"] = str(payload["skiplagged_estimation_program"]).upper()

    _RATES_FILE.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    reload_rates()
    return get_rates_snapshot()


def _programs() -> dict[str, list[dict]]:
    return _rates_config().get("programs", {})


def _international_fallback() -> float:
    return float(_rates_config().get("international_fallback_rate", 0.05))


_SOURCE_TO_PROGRAM: dict[SourceType, str] = {
    SourceType.BUSCAMILHAS_LATAM:     "LATAM",
    SourceType.BUSCAMILHAS_GOL:       "GOL",
    SourceType.BUSCAMILHAS_AZUL:      "AZUL",
    SourceType.BUSCAMILHAS_TAP:       "TAP",
    SourceType.BUSCAMILHAS_IBERIA:    "IBERIA",
    SourceType.BUSCAMILHAS_AMERICAN:  "AMERICAN AIRLINES",
    SourceType.BUSCAMILHAS_INTERLINE: "INTERLINE",
    SourceType.BUSCAMILHAS_COPA:      "COPA",
}


# Labels de award que NÃO casam por substring com a chave da tabela → alias
# explícito. Ex.: AwardTool devolve "American AAdvantage" mas a chave é
# "AMERICAN AIRLINES" (uma não é substring da outra), então o valor não aplicava.
_PROGRAM_LABEL_ALIASES: dict[str, str] = {
    "AADVANTAGE": "AMERICAN AIRLINES",
}

# Labels que casariam uma chave ERRADA por substring → forçar não-match (None).
# Ex.: "Turkish Miles&Smiles" contém "SMILES" (programa da GOL) mas NÃO é Smiles.
_PROGRAM_LABEL_BLOCK: tuple[str, ...] = ("MILES&SMILES", "MILES & SMILES")


def _resolve_program(
    airline: str = "",
    program: str = "",
    source: Optional[SourceType] = None,
) -> Optional[str]:
    """Returns the program key in rates.json, or None if no match.

    Prioriza matches MAIS LONGOS — "AZUL PELO MUNDO" tem precedência sobre
    "AZUL" pra evitar mistura ("Azul Pelo Mundo" pega rate genérico de AZUL).
    """
    programs = _programs()
    # Ordena chaves do mais longo pro mais curto pra priorizar match específico.
    keys_sorted = sorted(
        (k for k in programs if k != "DEFAULT"),
        key=lambda k: -len(k),
    )

    prog = (program or "").upper()
    # Bloqueia falsos-positivos de substring (ex.: Turkish "Miles&Smiles" ≠ Smiles).
    if any(blk in prog for blk in _PROGRAM_LABEL_BLOCK):
        return None
    # Aliases explícitos primeiro (labels que não casam por substring).
    for alias, canon in _PROGRAM_LABEL_ALIASES.items():
        if alias in prog and canon in programs:
            return canon
    if prog:
        for key in keys_sorted:
            if key in prog:
                return key

    air = (airline or "").upper()
    if air:
        for key in keys_sorted:
            if key in air:
                return key

    if source is not None:
        mapped = _SOURCE_TO_PROGRAM.get(source)
        if mapped and mapped in programs:
            return mapped

    return None


def resolve_program_key(
    airline: str = "",
    program: str = "",
    source: Optional[SourceType] = None,
) -> Optional[str]:
    """Chave canônica do programa na rates.json (ou None). Público — usado pelo
    allowlist de programas no orchestrator."""
    return _resolve_program(airline=airline, program=program, source=source)


def _rate_for_volume(tiers: list[dict], miles_needed: float) -> float:
    """Selects the rate from a tier list based on the number of miles bought."""
    if not tiers:
        return 0.0
    for tier in tiers:
        cap = tier.get("max_miles")
        rate = float(tier.get("rate", 0.0))
        if cap is None or miles_needed <= cap:
            return rate
    return float(tiers[-1].get("rate", 0.0))


def cost_per_mile(
    airline: str = "",
    program: str = "",
    source: Optional[SourceType] = None,
    miles: Optional[float] = None,
) -> float:
    """Returns the BRL-per-mile rate, honoring tiered pricing if `miles` given."""
    programs = _programs()
    needed = float(miles or 0)

    # Resolve o PROGRAMA primeiro (inclusive award: seats.aero/AwardTool/MCP) e
    # usa o peso por programa da tabela — assim os pesos de Alaska/Air France/
    # LifeMiles/Avios/Qatar etc. aplicam de verdade no award. Só quando o programa
    # NÃO é identificado é que o award cai na tarifa base internacional.
    key = _resolve_program(airline=airline, program=program, source=source)
    if key:
        return _rate_for_volume(programs[key], needed)

    if source in (SourceType.SEATS_AERO, SourceType.AWARDTOOL,
                  SourceType.MCP_AWARD, SourceType.MCP_QATAR):
        return _international_fallback()

    return _rate_for_volume(programs.get("DEFAULT", []), needed)


def miles_to_brl(
    miles,
    airline: str = "",
    program: str = "",
    source: Optional[SourceType] = None,
) -> float:
    """Converts a miles quantity into BRL using the appropriate tiered rate."""
    try:
        m = float(miles or 0)
    except (TypeError, ValueError):
        return 0.0
    return m * cost_per_mile(airline=airline, program=program, source=source, miles=m)


def offer_equivalent_brl(offer: UnifiedOffer) -> float:
    """Total cost in BRL for an offer: cash or miles * rate + taxes."""
    if offer.price_brl is not None:
        return offer.price_brl
    if offer.miles is not None:
        cpm = cost_per_mile(
            airline=offer.airline or "",
            program=offer.miles_program or "",
            source=offer.source,
            miles=offer.miles,
        )
        return offer.miles * cpm + (offer.taxes_brl or 0.0)
    return 0.0


def skiplagged_estimation_program() -> str:
    """Returns the program used as reference when converting Skiplagged cash
    into a miles-equivalent estimate (configured in rates.json)."""
    return _rates_config().get("_skiplagged_estimation_program", "GOL")


def estimate_miles_for_brl(
    price_brl: float,
    program: Optional[str] = None,
) -> tuple[int, str, float]:
    """For a cash price in BRL, returns (miles_equivalent, program_used, rate_used).

    Used by Skiplagged offers to display 'this cash deal ≈ N miles' on the card.
    Uses the configured estimation program by default.
    """
    program = (program or skiplagged_estimation_program()).upper()
    programs = _programs()
    tiers = programs.get(program) or programs.get("DEFAULT") or [{"rate": 0.025}]

    # Tiered rates are circular: rate depends on miles bought, which depends
    # on rate. Solve iteratively (2 passes is enough — tiers are coarse).
    rate = _rate_for_volume(tiers, miles_needed=0)
    miles = price_brl / rate if rate > 0 else 0
    for _ in range(2):
        rate = _rate_for_volume(tiers, miles_needed=miles)
        miles = price_brl / rate if rate > 0 else 0

    return int(round(miles)), program, rate


# Backwards-compatible flat snapshot of program rates at zero volume.
# Some legacy code still imports this; we expose it for compat.
RATES_BRL_PER_MILE: dict[str, float] = {
    key: _rate_for_volume(tiers, 0.0) for key, tiers in _programs().items()
}
INTERNATIONAL_FALLBACK_BRL_PER_MILE: float = _international_fallback()
