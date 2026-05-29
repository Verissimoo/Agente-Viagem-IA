"""Mapeamento de códigos IATA de cias para nomes amigáveis + programas de milhas.

Usado pra apresentação ao vendedor — códigos como "G3", "LA", "AD" não dizem
nada pro usuário final. Convertemos pra "GOL", "LATAM", "AZUL".

Pra milhas, mostramos o NOME DO PROGRAMA (Smiles, LATAM Pass) em vez da cia.
"""
from __future__ import annotations

from typing import Optional


# Código IATA → nome comercial da companhia
_CARRIER_NAMES: dict[str, str] = {
    # Brasil
    "G3": "GOL",
    "LA": "LATAM",
    "JJ": "LATAM",          # legado LATAM Brasil
    "AD": "AZUL",
    "O6": "AZUL",
    "2Z": "AZUL Conecta",
    # América do Sul
    "AR": "Aerolíneas",
    "AV": "Avianca",
    "LP": "LATAM Peru",
    "4M": "LATAM Argentina",
    "PZ": "LATAM Paraguai",
    "JA": "JetSMART",
    "H2": "Sky Airline",
    "CM": "Copa",
    # América do Norte
    "AA": "American Airlines",
    "DL": "Delta",
    "UA": "United",
    "AC": "Air Canada",
    "AM": "Aeroméxico",
    "B6": "JetBlue",
    "WN": "Southwest",
    # Europa
    "TP": "TAP Portugal",
    "AF": "Air France",
    "KL": "KLM",
    "BA": "British Airways",
    "IB": "Iberia",
    "LH": "Lufthansa",
    "LX": "Swiss",
    "OS": "Austrian",
    "SN": "Brussels",
    "AZ": "ITA Airways",
    "AY": "Finnair",
    "SK": "SAS",
    "TK": "Turkish Airlines",
    # Oriente Médio
    "QR": "Qatar Airways",
    "EK": "Emirates",
    "EY": "Etihad",
    "SV": "Saudia",
    # Ásia
    "CX": "Cathay Pacific",
    "SQ": "Singapore Airlines",
    "QF": "Qantas",
    "NZ": "Air New Zealand",
    # África
    "ET": "Ethiopian",
    "SA": "South African",
    "KQ": "Kenya Airways",
    "MS": "EgyptAir",
}


# Código IATA da CIA → nome do PROGRAMA de fidelidade (pra apresentar
# alternativas em milhas com o nome correto do programa, não da cia).
_MILES_PROGRAMS: dict[str, str] = {
    "G3": "Smiles",
    "LA": "LATAM Pass",
    "JJ": "LATAM Pass",
    "AD": "TudoAzul",
    "O6": "TudoAzul",
    "2Z": "TudoAzul",
    "AA": "AAdvantage",
    "DL": "SkyMiles",
    "UA": "MileagePlus",
    "AC": "Aeroplan",
    "AM": "Club Premier",
    "TP": "Miles&Go",
    "AF": "Flying Blue",
    "KL": "Flying Blue",
    "BA": "Avios",
    "IB": "Avios",
    "LH": "Miles & More",
    "LX": "Miles & More",
    "OS": "Miles & More",
    "AY": "Finnair Plus",
    "TK": "Miles&Smiles",
    "QR": "Privilege Club",
    "EK": "Skywards",
    "EY": "Guest",
    "CX": "Asia Miles",
    "SQ": "KrisFlyer",
    "QF": "Qantas Frequent Flyer",
    "AV": "LifeMiles",
    "CM": "ConnectMiles",
    "AR": "Aerolíneas Plus",
    "ET": "ShebaMiles",
}


def prettify_carrier(code_or_name: Optional[str]) -> Optional[str]:
    """Converte 'G3' → 'GOL'. Se já for nome ou desconhecido, devolve original."""
    if not code_or_name:
        return code_or_name
    s = str(code_or_name).strip()
    if not s:
        return s
    # Já é um nome amigável (>=4 chars, contém letra minúscula ou espaço) → mantém
    upper = s.upper()
    if upper in _CARRIER_NAMES:
        return _CARRIER_NAMES[upper]
    return s  # desconhecido: devolve como veio


def miles_program_name(carrier_code: Optional[str]) -> Optional[str]:
    """Converte 'G3' → 'Smiles', 'LA' → 'LATAM Pass'."""
    if not carrier_code:
        return None
    return _MILES_PROGRAMS.get(str(carrier_code).strip().upper())


def carrier_to_program(carrier_or_name: Optional[str]) -> Optional[str]:
    """Aceita 'G3' OU 'GOL' e devolve o programa ('Smiles')."""
    if not carrier_or_name:
        return None
    upper = str(carrier_or_name).strip().upper()
    # Tenta como código direto
    if upper in _MILES_PROGRAMS:
        return _MILES_PROGRAMS[upper]
    # Tenta como nome reverso: 'GOL' → procura código que mapeia
    for code, name in _CARRIER_NAMES.items():
        if name.upper() == upper and code in _MILES_PROGRAMS:
            return _MILES_PROGRAMS[code]
    return None
