import re
import unicodedata

CITY_TO_IATAS = {
    # Brasil
    "BRASILIA": ["BSB"],
    "BRASÍLIA": ["BSB"],
    "SAO PAULO": ["CGH", "GRU", "VCP"],   # CGH costuma ser ótimo p/ doméstico; GRU/VCP entram como opção
    "SÃO PAULO": ["CGH", "GRU", "VCP"],
    "RIO DE JANEIRO": ["SDU", "GIG"],
    "RIO": ["SDU", "GIG"],
    "BELO HORIZONTE": ["CNF", "PLU"],
    "CURITIBA": ["CWB"],
    "PORTO ALEGRE": ["POA"],
    "SALVADOR": ["SSA"],
    "RECIFE": ["REC"],
    "FORTALEZA": ["FOR"],
    "FLORIANOPOLIS": ["FLN"],
    "FLORIANÓPOLIS": ["FLN"],
}

def _normalize(s: str) -> str:
    s = s.strip().upper()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\s+", " ", s)
    return s

def resolve_place_to_iatas(place: str) -> list[str]:
    """
    Recebe "Brasília" / "São Paulo" / "BSB" / "GRU" etc e retorna lista de IATAs.
    """
    place_n = _normalize(place)

    # Se já parece IATA (3 letras), devolve direto.
    if re.fullmatch(r"[A-Z]{3}", place_n):
        return [place_n]

    # Busca por city map
    if place_n in CITY_TO_IATAS:
        return CITY_TO_IATAS[place_n]

    # fallback simples: tenta achar uma cidade conhecida dentro do texto
    for city, iatas in CITY_TO_IATAS.items():
        if city in place_n:
            return iatas

    raise ValueError(f"Não consegui mapear '{place}' para aeroportos. Adicione no CITY_TO_IATAS.")
