"""Cobertura global de cidade→IATA (curada + dataset airportsdata + aliases PT-BR)."""
import pytest

from backend.app.providers.buscamilhas.iata_resolver import resolve_city_to_iatas


@pytest.mark.parametrize("city,expected_first", [
    ("Marselha", "MRS"),       # alias PT-BR + global (dataset city='Marseille')
    ("Marseille", "MRS"),
    ("Nice", "NCE"),
    ("Toronto", "YYZ"),        # curada (dataset traz city='Mississauga')
    ("Dubai", "DXB"),
    ("Cancún", "CUN"),
    ("Istambul", "IST"),
    ("Istanbul", "IST"),
    ("Veneza", "VCE"),         # curada (dataset usa 'Venezia' local)
    ("Tóquio", "NRT"),
    ("Genebra", "GVA"),
    ("Sydney", "SYD"),
])
def test_resolve_international_cities(city, expected_first):
    got = resolve_city_to_iatas(city)
    assert got, f"{city} não resolveu"
    assert got[0] == expected_first


def test_sao_paulo_curated_wins_over_dataset():
    # Curada tem a ordenação correta de hub (GRU intl primeiro), vence o dataset.
    assert resolve_city_to_iatas("São Paulo") == ["GRU", "VCP", "CGH"]


def test_direct_iata_passthrough():
    assert resolve_city_to_iatas("GRU") == ["GRU"]
    assert resolve_city_to_iatas("mrs") == ["MRS"]


def test_unknown_city_returns_empty():
    # Sem token de 3 letras → não resolve, sem crash.
    assert resolve_city_to_iatas("Cidade inexistente") == []
    assert resolve_city_to_iatas("") == []


def test_embedded_iata_code_fallback():
    # Fallback (d): query contém código IATA explícito (sem outros tokens de 3
    # letras — stopwords como "VOO" são filtradas pelo caller, não pelo resolver).
    assert resolve_city_to_iatas("destino final LIS") == ["LIS"]
