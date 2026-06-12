from __future__ import annotations

import os
import re
import unicodedata
from typing import List

_RAW_CITY_TO_IATAS = {
    # BRASIL
    "Brasilia": ["BSB"],
    "BSB": ["BSB"],
    # Ordem por RELEVÂNCIA (hub principal/internacional primeiro) — `_iata` usa o
    # 1º como primário e a busca varre os top-2. GRU (maior hub intl) e VCP (hub
    # internacional da Azul) antes de CGH (doméstico, sem voo internacional).
    "Sao Paulo": ["GRU", "VCP", "CGH"],
    "Sampa": ["GRU", "VCP", "CGH"],
    "SP": ["GRU", "VCP", "CGH"],
    "Guarulhos": ["GRU"],
    "Congonhas": ["CGH"],
    "Viracopos": ["VCP"],
    "Campinas": ["VCP"],
    "Rio de Janeiro": ["GIG", "SDU"],
    "Rio": ["GIG", "SDU"],
    "RJ": ["GIG", "SDU"],
    "Galeao": ["GIG"],
    "Santos Dumont": ["SDU"],
    "Belo Horizonte": ["CNF", "PLU"],
    "BH": ["CNF", "PLU"],
    "Confins": ["CNF"],
    "Salvador": ["SSA"],
    "Recife": ["REC"],
    "Fortaleza": ["FOR"],
    "Curitiba": ["CWB"],
    "Porto Alegre": ["POA"],
    "Florianopolis": ["FLN"],
    "Manaus": ["MAO"],
    "Belem": ["BEL"],
    "Goiania": ["GYN"],
    "Vitoria": ["VIX"],
    "Cuiaba": ["CGB"],
    "Campo Grande": ["CGR"],
    "Natal": ["NAT"],
    "Maceio": ["MCZ"],
    "Joao Pessoa": ["JPA"],
    "Aracaju": ["AJU"],
    "Teresina": ["THE"],
    "Sao Luis": ["SLZ"],
    "Palmas": ["PMW"],
    "Porto Velho": ["PVH"],
    "Rio Branco": ["RBR"],
    "Boa Vista": ["BVB"],
    "Macapa": ["MCP"],

    # EUA
    "Miami": ["MIA"],
    "Orlando": ["MCO"],
    "Nova York": ["JFK", "LGA", "EWR"],
    "New York": ["JFK", "LGA", "EWR"],
    "NYC": ["JFK", "LGA", "EWR"],
    "Los Angeles": ["LAX"],
    "Chicago": ["ORD", "MDW"],
    "San Francisco": ["SFO"],
    "Washington": ["IAD", "DCA", "BWI"],
    "Las Vegas": ["LAS"],
    "Houston": ["IAH", "HOU"],
    "Dallas": ["DFW"],
    "Boston": ["BOS"],
    "Atlanta": ["ATL"],

    # EUROPA
    "Lisboa": ["LIS"],
    "Porto": ["OPO"],
    "Madrid": ["MAD"],
    "Barcelona": ["BCN"],
    "Paris": ["CDG", "ORY"],
    "Londres": ["LHR", "LGW", "STN", "LCY", "SEN", "LTN"],
    "London": ["LHR", "LGW", "STN", "LCY", "SEN", "LTN"],
    "Roma": ["FCO", "CIA"],
    "Milao": ["MXP", "LIN", "BGY"],
    "Frankfurt": ["FRA"],
    "Munique": ["MUC"],
    "Berlim": ["BER"],
    "Amsterda": ["AMS"],
    "Amsterdam": ["AMS"],
    "Zurique": ["ZRH"],
    "Viena": ["VIE"],
    "Bruxelas": ["BRU", "CRL"],

    # AMÉRICA DO SUL
    "Buenos Aires": ["EZE", "AEP"],
    "Santiago": ["SCL"],
    "Montevidéu": ["MVD"],
    "Montevideo": ["MVD"],
    "Asunção": ["ASU"],
    "Asuncion": ["ASU"],
    "Bogota": ["BOG"],
    "Medellin": ["MDE"],
    "Cartagena": ["CTG"],
    "Lima": ["LIM"],
    "Quito": ["UIO"],
    "Guayaquil": ["GYE"],
    "La Paz": ["LPB"],
    "Santa Cruz": ["VVI"],

    # JAPÃO
    "Toquio": ["NRT", "HND"],
    "Tokyo": ["NRT", "HND"],
    "Osaka": ["KIX", "ITM"],

    # Hubs internacionais que o dataset traz com `city` inesperado (ex.: YYZ vem
    # como 'Mississauga') ou que pedem ordenação de hub — curada vence o dataset.
    "Toronto": ["YYZ", "YTZ"],
    "Montreal": ["YUL"],
    "Vancouver": ["YVR"],
    "Cidade do Mexico": ["MEX"],
    "Mexico City": ["MEX"],
    "Cancun": ["CUN"],
    "Bangkok": ["BKK", "DMK"],
    "Doha": ["DOH"],
    "Dubai": ["DXB", "DWC"],
    "Abu Dhabi": ["AUH"],
    "Istambul": ["IST", "SAW"],
    "Istanbul": ["IST", "SAW"],
    "Dublin": ["DUB"],
    "Lisbon": ["LIS"],
    "Marselha": ["MRS"],
    "Marseille": ["MRS"],

    # Europa (dataset usa nomes locais inconsistentes — curada garante o certo).
    "Nice": ["NCE"], "Lyon": ["LYS"], "Toulouse": ["TLS"], "Bordeaux": ["BOD"],
    "Veneza": ["VCE"], "Venice": ["VCE"], "Florenca": ["FLR"], "Napoles": ["NAP"],
    "Turim": ["TRN"], "Genova": ["GOA"], "Bolonha": ["BLQ"],
    "Genebra": ["GVA"], "Praga": ["PRG"], "Varsovia": ["WAW"], "Cracovia": ["KRK"],
    "Estocolmo": ["ARN", "BMA"], "Copenhague": ["CPH"], "Oslo": ["OSL"],
    "Helsinque": ["HEL"], "Atenas": ["ATH"], "Hamburgo": ["HAM"], "Colonia": ["CGN"],
    "Dusseldorf": ["DUS"], "Stuttgart": ["STR"], "Edimburgo": ["EDI"],
    "Manchester": ["MAN"], "Dublin (IRL)": ["DUB"], "Budapeste": ["BUD"],
    "Bucareste": ["OTP"], "Moscou": ["SVO", "DME", "VKO"],

    # Ásia / Oriente Médio / Oceania / África
    "Pequim": ["PEK", "PKX"], "Xangai": ["PVG", "SHA"], "Hong Kong": ["HKG"],
    "Seul": ["ICN", "GMP"], "Singapura": ["SIN"], "Bangkok (alt)": ["BKK", "DMK"],
    "Mumbai": ["BOM"], "Nova Delhi": ["DEL"], "Deli": ["DEL"],
    "Sidney": ["SYD"], "Sydney": ["SYD"], "Melburne": ["MEL"], "Melbourne": ["MEL"],
    "Auckland": ["AKL"],
    "Cairo": ["CAI"], "Casablanca": ["CMN"], "Joanesburgo": ["JNB"],
    "Cidade do Cabo": ["CPT"], "Nairobi": ["NBO"], "Tel Aviv": ["TLV"],
}

def normalize_city_key(s: str) -> str:
    """
    Remove acentos, upper, remove pontuação e múltiplos espaços.
    Ex: 'São Paulo' -> 'SAO PAULO'
    """
    if not s:
        return ""
    # Normalize unicode (decompose chars like 'ã' to 'a' + '~')
    s = unicodedata.normalize("NFKD", s)
    # Filter out combining marks (accents)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    # To upper
    s = s.upper()
    # Replace non-alphanumeric with space, but keep slashes/dashes if useful? 
    # The requirement says remove punctuation and multiple spaces.
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    # Remove multiple spaces and strip
    s = re.sub(r"\s+", " ", s).strip()
    return s

# Generate normalized index
_CITY_TO_IATAS = {}
for city, iatas in _RAW_CITY_TO_IATAS.items():
    key = normalize_city_key(city)
    if key not in _CITY_TO_IATAS:
        _CITY_TO_IATAS[key] = []
    for code in iatas:
        if code.upper() not in _CITY_TO_IATAS[key]:
            _CITY_TO_IATAS[key].append(code.upper())


# Aliases PT-BR → nome que o dataset global usa (em inglês/local). Aplicados
# ANTES de consultar o índice global. Acentos já são removidos pela normalize,
# então mapeamos a forma sem acento. Chave e valor ficam normalizados.
_PTBR_CITY_ALIASES_RAW = {
    "marselha": "marseille", "nice": "nice", "lyon": "lyon", "toulouse": "toulouse",
    "toquio": "tokyo", "genebra": "geneva", "praga": "prague", "varsovia": "warsaw",
    "estocolmo": "stockholm", "copenhague": "copenhagen", "munique": "munich",
    "milao": "milan", "atenas": "athens", "viena": "vienna", "bruxelas": "brussels",
    "cidade do mexico": "mexico city", "joanesburgo": "johannesburg",
    "pequim": "beijing", "xangai": "shanghai", "seul": "seoul",
    "cidade do cabo": "cape town", "florenca": "florence", "veneza": "venice",
    "napoles": "naples", "lisboa": "lisbon", "roma": "rome", "genova": "genoa",
    "turim": "turin", "hamburgo": "hamburg", "colonia": "cologne",
    "dusseldorf": "dusseldorf", "edimburgo": "edinburgh", "moscou": "moscow",
    "varsovia": "warsaw", "bucareste": "bucharest", "helsinque": "helsinki",
    "oslo": "oslo", "dubai": "dubai", "abu dhabi": "abu dhabi", "doha": "doha",
    "istambul": "istanbul", "cidade do panama": "panama city",
    "bangcoc": "bangkok", "nova delhi": "new delhi", "mumbai": "mumbai",
    "hong kong": "hong kong", "singapura": "singapore", "sidney": "sydney",
    "melburne": "melbourne", "auckland": "auckland", "cairo": "cairo",
    "nairobi": "nairobi", "casablanca": "casablanca", "argel": "algiers",
}
_PTBR_CITY_ALIASES = {
    normalize_city_key(k): normalize_city_key(v) for k, v in _PTBR_CITY_ALIASES_RAW.items()
}


def _build_global_index() -> dict:
    """Índice cidade_normalizada → [IATA,...] do dataset offline `airportsdata`
    (~8k aeroportos). Construído UMA vez no import (custo único, sem I/O em
    runtime). A tabela curada tem prioridade na resolução."""
    try:
        import airportsdata
        data = airportsdata.load("IATA")
    except Exception:
        return {}
    idx: dict = {}
    for code, info in data.items():
        if not code or len(code) != 3 or not code.isalpha():
            continue
        city = info.get("city")
        if not city:
            continue
        key = normalize_city_key(city)
        if not key:
            continue
        idx.setdefault(key, [])
        cu = code.upper()
        if cu not in idx[key]:
            idx[key].append(cu)
    # Ordem determinística e estável (o dataset não traz relevância).
    for k in idx:
        idx[k] = sorted(idx[k])
    return idx


_GLOBAL_CITY_TO_IATAS = _build_global_index()


def _from_alias_or_global(key: str) -> List[str]:
    """Resolve por alias PT-BR (→ nome do dataset) e depois pelo índice global."""
    alias = _PTBR_CITY_ALIASES.get(key)
    if alias:
        # Alias pode bater na curada (ex.: 'roma' já curada) ou no global.
        iatas = _CITY_TO_IATAS.get(alias) or _GLOBAL_CITY_TO_IATAS.get(alias)
        if iatas:
            return list(iatas)
    return list(_GLOBAL_CITY_TO_IATAS.get(key) or [])


def resolve_city_to_iatas(query: str) -> List[str]:
    """Aceita cidade ou IATA e retorna lista de IATAs.

    Ordem: (a) IATA direto · (b) tabela curada (prioridade, ordenação de hub) ·
    (c) alias PT-BR + índice global do dataset · (d) regex de 3 letras (fallback).
    Nunca lança — degrada para []."""
    if not query:
        return []

    normalized_q = normalize_city_key(query)

    # (a) IATA direto (3 letras).
    if len(normalized_q) == 3 and normalized_q.isalpha():
        return [normalized_q]

    # (b) Tabela curada (vence o dataset — tem a ordenação correta de hubs).
    iatas = _CITY_TO_IATAS.get(normalized_q)
    if iatas:
        return list(iatas)

    # (c) Alias PT-BR + índice global do dataset.
    g = _from_alias_or_global(normalized_q)
    if g:
        return g

    # (d) Fallback: a query contém um código IATA de 3 letras?
    found = re.findall(r"\b[A-Z]{3}\b", query.upper())
    if found:
        return list(dict.fromkeys(found))  # preserva ordem, único

    return []

def resolve_place_to_codes(place: str) -> List[str]:
    """
    Compatibilidade com sistema antigo, resolvendo para códigos IATA.
    """
    iatas = resolve_city_to_iatas(place)
    if not iatas:
        raise ValueError(f"Não consegui mapear '{place}'. Tente usar o código IATA de 3 letras.")
    
    # Manter lógica de limitar a 1 se MILES_MAX_CODES_PER_PLACE estiver definido
    limit = int(os.getenv("MILES_MAX_CODES_PER_PLACE", "1"))
    return iatas[: max(1, limit)]

def resolve_place_to_iatas(place: str) -> List[str]:
    return resolve_city_to_iatas(place)





