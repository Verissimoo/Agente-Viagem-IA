"""Helpers puros (sem st.*) usados pela UI multiagent.

Tudo aqui pode ser testado isoladamente sem subir Streamlit.
"""
from pcd.core.conversion import miles_to_brl as _miles_to_brl_core


# Metadados visuais de cada companhia (src = valor exato do SourceType)
CIA_META = {
    "LATAM":     {"emoji": "💎", "css": "latam",     "prefix": "L",  "src": "buscamilhas_latam"},
    "GOL":       {"emoji": "🟠", "css": "gol",       "prefix": "G",  "src": "buscamilhas_gol"},
    "AZUL":      {"emoji": "🔵", "css": "azul",      "prefix": "A",  "src": "buscamilhas_azul"},
    "TAP":       {"emoji": "🟢", "css": "tap",       "prefix": "TP", "src": "buscamilhas_tap"},
    "AMERICAN AIRLINES": {"emoji": "🦅", "css": "american",  "prefix": "AA", "src": "buscamilhas_american"},
    "INTERLINE": {"emoji": "🌐", "css": "interline", "prefix": "IN", "src": "buscamilhas_interline"},
    "COPA":      {"emoji": "🛫", "css": "copa",      "prefix": "CM", "src": "buscamilhas_copa"},
    "MCP_AWARD": {"emoji": "🌍", "css": "mcp",       "prefix": "W",  "src": "mcp_award"},
    "QATAR":     {"emoji": "🇶🇦", "css": "qatar",     "prefix": "QR", "src": "mcp_qatar"},
}

# Companhias internacionais sem acréscimo de bagagem (já inclusa na tarifa de milhas)
INTERNACIONAIS_SEM_BAGAGEM_EXTRA = {"TAP", "AMERICAN AIRLINES", "INTERLINE"}


def src_name(cia: str) -> str:
    """Retorna o valor exato do SourceType para a companhia."""
    return CIA_META.get(cia, {}).get("src", f"buscamilhas_{cia.lower()}")


def tab_key(cia: str) -> str:
    """Chave única da tab da companhia (sem espaços)."""
    return f"cia_{cia.lower().replace(' ', '_')}"


def miles_to_brl(miles, airline: str = "", program: str = "") -> float:
    """Wrapper compatível — delega para pcd.core.conversion.miles_to_brl."""
    return _miles_to_brl_core(miles, airline=airline, program=program)


def format_duration(min_total) -> str:
    try:
        v = int(min_total or 0)
    except Exception:
        return "—"
    if v <= 0:
        return "0m"
    h, m = divmod(v, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def safe_int_miles(val) -> int:
    try:
        if val is None or str(val).lower() in ("none", "", "—"):
            return 0
        return int(float(str(val).replace(",", "")))
    except Exception:
        return 0


def safe_float(val) -> float:
    try:
        return float(val) if val is not None else 0.0
    except Exception:
        return 0.0


def source_is(offer, name: str) -> bool:
    if not offer or not hasattr(offer, "source"):
        return False
    s = offer.source
    return str(s.value if hasattr(s, "value") else s).lower() == name.lower()


def id_prefix(airline: str, source: str = "") -> str:
    a = str(airline).upper()
    s = str(source).upper()
    if "LATAM"     in a or "LATAM"     in s: return "L"
    if "GOL"       in a or "GOL"       in s: return "G"
    if "AZUL"      in a or "AZUL"      in s: return "A"
    if "IBERIA"    in a or "IBERIA"    in s: return "IB"
    if "TAP"       in a or "TAP"       in s: return "TP"
    if "AMERICAN"  in a or "AMERICAN"  in s: return "AA"
    if "INTERLINE" in a or "INTERLINE" in s: return "IN"
    if "MCP"       in s or "MCP_AWARD" in s: return "W"
    return "X"


def get_baggage_price(offer, include_baggage: bool) -> float:
    base = safe_float(getattr(offer, "equivalent_brl", 0))
    if not include_baggage:
        return base
    a = str(getattr(offer, "airline", "")).upper()

    # Internacionais já incluem bagagem — sem acréscimo
    for cia in INTERNACIONAIS_SEM_BAGAGEM_EXTRA:
        if cia in a:
            return base

    if "GOL"  in a: return base + 130.0
    if "AZUL" in a: return base + 160.0

    if "LATAM" in a:
        m_out = getattr(offer, "baggage_miles_out", None)
        m_in  = getattr(offer, "baggage_miles_in",  None)
        has_bag_out = m_out is not None
        has_bag_in  = m_in  is not None
        if has_bag_out or has_bag_in:
            val_out = safe_int_miles(m_out) if has_bag_out else safe_int_miles(getattr(offer, "miles_out", 0) or getattr(offer, "miles", 0))
            val_in  = 0
            trip_type_val = getattr(getattr(offer, "trip_type", None), "name", str(getattr(offer, "trip_type", "")))
            if "ROUNDTRIP" in trip_type_val.upper():
                val_in = safe_int_miles(m_in) if has_bag_in else safe_int_miles(getattr(offer, "miles_in", 0) or 0)
            total_m = val_out + val_in
            eq = miles_to_brl(total_m, "LATAM")
            return eq + safe_float(getattr(offer, "taxes_brl", 0))

    return base
