from __future__ import annotations

import os
import re
import unicodedata
from typing import List

_RAW_CITY_TO_IATAS = {
    # BRASIL
    "Brasilia": ["BSB"],
    "BSB": ["BSB"],
    "Sao Paulo": ["CGH", "GRU", "VCP"],
    "Sampa": ["CGH", "GRU", "VCP"],
    "SP": ["CGH", "GRU", "VCP"],
    "Guarulhos": ["GRU"],
    "Congonhas": ["CGH"],
    "Viracopos": ["VCP"],
    "Campinas": ["VCP"],
    "Rio de Janeiro": ["SDU", "GIG"],
    "Rio": ["SDU", "GIG"],
    "RJ": ["SDU", "GIG"],
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

def resolve_city_to_iatas(query: str) -> List[str]:
    """
    Aceita cidade ou IATA, e retorna lista de IATAs.
    """
    if not query:
        return []
        
    normalized_q = normalize_city_key(query)
    
    # 1. Check if it's a direct IATA match
    if len(normalized_q) == 3 and normalized_q.isalpha():
        return [normalized_q]
        
    # 2. Check in mapping
    iatas = _CITY_TO_IATAS.get(normalized_q)
    if iatas:
        return iatas
        
    # 3. Fallback: partial match or check if query contains IATA
    found = re.findall(r"\b[A-Z]{3}\b", query.upper())
    if found:
        return list(dict.fromkeys(found)) # preserve order, unique

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





