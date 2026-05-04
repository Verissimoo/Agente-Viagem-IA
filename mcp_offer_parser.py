"""
mcp_offer_parser.py
===================
Extrator de dados brutos retornados pelo Award Travel Finder (REST API / MCP).

Responsabilidade ÚNICA:
  Transformar o JSON bruto da API em uma lista de UnifiedOffer (dicts normalizados).

O que este módulo FAZ:
  - Identifica o response_type de cada airline ("flights" ou "calendar").
  - Extrai pontos, programa de milhas e segmentos de voo.
  - Converte taxas para BRL via fx_rates.convert().
  - Ignora airlines com http_error e cabines indisponíveis.

O que este módulo NÃO FAZ (propositalmente):
  - Não calcula price_brl nem equivalent_brl.
  - Não multiplica pontos pelo valor do milheiro.
  Esses cálculos são responsabilidade da tabela de configuração do sistema.

Estrutura do UnifiedOffer retornado:
  {
    "airline":        str,           # nome/slug da companhia (ex: "qatar_airways")
    "cabin_class":    str,           # "economy" | "business" | "first" | "premium_economy"
    "miles":          int,           # quantidade bruta de pontos
    "miles_program":  str,           # nome do programa (ex: "Avios", "MileagePlus")
    "taxes_original": float,         # valor das taxas na moeda original
    "taxes_currency": str,           # moeda original das taxas (ex: "USD")
    "taxes_brl":      float,         # taxas convertidas para BRL (0.0 se falhar)
    "seats":          int | None,    # quantidade de assentos disponíveis (-1 = ilimitado)
    "booking_link":   str | None,    # link direto para reserva
    "flight_number":  str | None,    # número do voo (quando disponível)
    "departure_time": str | None,    # ISO 8601 (quando disponível)
    "arrival_time":   str | None,    # ISO 8601 (quando disponível)
    "route":          str | None,    # "GRU -> JFK"
    "search_date":    str | None,    # "YYYY-MM-DD"
    "source":         str,           # "mcp_flights" | "mcp_calendar"
    "price_brl":      None,          # reservado para cálculo posterior
    "equivalent_brl": None,          # reservado para cálculo posterior
  }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Programa padrão por airline (fallback quando a API não informa points_name)
# ---------------------------------------------------------------------------
_DEFAULT_PROGRAM: dict[str, str] = {
    "british_airways":    "Avios",
    "qatar_airways":      "Avios",
    "cathay_pacific":     "Asia Miles",
    "american_airlines":  "AAdvantage",
    "united_airlines":    "MileagePlus",
    "air_canada":         "Aeroplan",
    "delta_airlines":     "SkyMiles",
    "singapore_airlines": "KrisFlyer",
    "emirates":           "Skywards",
    "lufthansa":          "Miles & More",
    "turkish_airlines":   "Miles&Smiles",
    "virgin_atlantic":    "Virgin Points",
    "alaska_airlines":    "Mileage Plan",
    "ana":                "ANA Mileage Club",
    "japan_airlines":     "JAL Mileage Bank",
    "korean_air":         "SKYPASS",
    "avianca":            "LifeMiles",
}

# Ordem de preferência de cabines para exibição
_CABIN_ORDER = ["first", "business", "premium_economy", "economy"]


# ---------------------------------------------------------------------------
# Conversão de taxas
# ---------------------------------------------------------------------------

def _convert_taxes_to_brl(amount: float | None, currency: str | None) -> float:
    """
    Converte o valor de taxes para BRL usando fx_rates.convert().
    Retorna 0.0 e imprime log se a conversão falhar.
    """
    if amount is None or amount == 0.0:
        return 0.0
    if not currency:
        return 0.0

    currency = currency.upper()
    if currency == "BRL":
        return float(amount)

    try:
        from fx_rates import convert
        return round(convert(float(amount), currency, "BRL"), 2)
    except Exception as exc:
        print(f"[mcp_offer_parser] [AVISO] Falha na conversao FX {currency}->BRL: {exc}. Usando 0.0.")
        return 0.0


# ---------------------------------------------------------------------------
# Helpers de extração
# ---------------------------------------------------------------------------

def _safe_str(v: Any) -> str | None:
    return str(v).strip() if isinstance(v, str) and v.strip() else None


def _safe_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _safe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _make_offer(
    airline: str,
    cabin_class: str,
    miles: int,
    miles_program: str,
    taxes_original: float,
    taxes_currency: str | None,
    taxes_brl: float,
    seats: int | None = None,
    booking_link: str | None = None,
    flight_number: str | None = None,
    departure_time: str | None = None,
    arrival_time: str | None = None,
    route: str | None = None,
    search_date: str | None = None,
    source: str = "mcp_unknown",
) -> dict:
    """Constrói um UnifiedOffer normalizado."""
    return {
        "airline":        airline,
        "cabin_class":    cabin_class,
        "miles":          miles,
        "miles_program":  miles_program,
        "taxes_original": taxes_original,
        "taxes_currency": (taxes_currency or "USD").upper(),
        "taxes_brl":      taxes_brl,
        "seats":          seats,
        "booking_link":   booking_link,
        "flight_number":  flight_number,
        "departure_time": departure_time,
        "arrival_time":   arrival_time,
        "route":          route,
        "search_date":    search_date,
        "source":         source,
        # Reservados para cálculo posterior pela tabela de configuração
        "price_brl":      None,
        "equivalent_brl": None,
    }


# ---------------------------------------------------------------------------
# Extratores por response_type
# ---------------------------------------------------------------------------

def _parse_flights_response(airline: str, data: dict) -> list[dict]:
    """
    Processa response_type = "flights" (ex: Qatar Airways).

    Cada flight pode ter múltiplos bundles com cabin_class e pontos distintos.
    Retorna um UnifiedOffer por bundle disponível.
    """
    offers: list[dict] = []
    route       = _safe_str(data.get("route"))
    search_date = _safe_str(data.get("search_date"))
    flights     = data.get("flights") or []

    if not isinstance(flights, list):
        return offers

    default_program = _DEFAULT_PROGRAM.get(airline, airline.replace("_", " ").title())

    for flight in flights:
        if not isinstance(flight, dict):
            continue

        # Extrai segmento principal para dados de voo
        segments = flight.get("segments") or []
        first_seg = segments[0] if isinstance(segments, list) and segments else {}
        flight_number  = _safe_str(first_seg.get("flight_number"))
        departure_time = _safe_str(first_seg.get("departure_time"))
        arrival_time   = _safe_str(first_seg.get("arrival_time"))

        # Usa bundles como fonte primária (mais detalhados)
        bundles = flight.get("bundles") or []
        if isinstance(bundles, list) and bundles:
            for bundle in bundles:
                if not isinstance(bundle, dict):
                    continue

                status = str(bundle.get("status") or "").upper()
                if status not in {"AVAILABLE", "OPEN", ""}:
                    continue

                points = _safe_int(bundle.get("points"))
                if not points or points <= 0:
                    continue

                cabin_class   = str(bundle.get("cabin_class") or "economy").lower()
                miles_program = _safe_str(bundle.get("points_name")) or default_program
                taxes_raw     = _safe_float(bundle.get("taxes")) or 0.0
                taxes_ccy     = _safe_str(bundle.get("taxes_currency"))
                taxes_brl     = _convert_taxes_to_brl(taxes_raw, taxes_ccy)
                booking_link  = _safe_str(bundle.get("link"))
                inventory     = _safe_int(bundle.get("inventory_quantity"))  # -1 = ilimitado

                offers.append(_make_offer(
                    airline=airline,
                    cabin_class=cabin_class,
                    miles=points,
                    miles_program=miles_program,
                    taxes_original=taxes_raw,
                    taxes_currency=taxes_ccy,
                    taxes_brl=taxes_brl,
                    seats=inventory,
                    booking_link=booking_link,
                    flight_number=flight_number,
                    departure_time=departure_time,
                    arrival_time=arrival_time,
                    route=route,
                    search_date=search_date,
                    source="mcp_flights",
                ))

        else:
            # Fallback: usa cabins summary do flight
            cabins = flight.get("cabins") or {}
            if not isinstance(cabins, dict):
                continue

            for cabin_class in _CABIN_ORDER:
                cabin = cabins.get(cabin_class)
                if not isinstance(cabin, dict):
                    continue
                if not cabin.get("available", False):
                    continue

                points = _safe_int(cabin.get("points"))
                if not points or points <= 0:
                    continue

                taxes_raw = _safe_float(cabin.get("taxes")) or 0.0
                taxes_ccy = _safe_str(cabin.get("taxes_currency"))
                taxes_brl = _convert_taxes_to_brl(taxes_raw, taxes_ccy)

                offers.append(_make_offer(
                    airline=airline,
                    cabin_class=cabin_class,
                    miles=points,
                    miles_program=default_program,
                    taxes_original=taxes_raw,
                    taxes_currency=taxes_ccy,
                    taxes_brl=taxes_brl,
                    seats=_safe_int(cabin.get("seats")),
                    flight_number=flight_number,
                    departure_time=departure_time,
                    arrival_time=arrival_time,
                    route=route,
                    search_date=search_date,
                    source="mcp_flights",
                ))

    return offers


def _parse_calendar_response(airline: str, data: dict) -> list[dict]:
    """
    Processa response_type = "calendar" (ex: British Airways, Cathay Pacific).

    Neste formato não há segmentos individuais, apenas resumo de disponibilidade
    por data e cabine.
    """
    offers: list[dict] = []
    route       = _safe_str(data.get("route"))
    search_date = _safe_str(data.get("search_date"))

    avail = data.get("availability")
    if not isinstance(avail, dict):
        return offers

    if avail.get("data_available") is False:
        # Sem dados disponíveis para esta data
        return offers

    cabins = avail.get("cabins") or {}
    if not isinstance(cabins, dict):
        return offers

    default_program = _DEFAULT_PROGRAM.get(airline, airline.replace("_", " ").title())

    for cabin_class in _CABIN_ORDER:
        cabin = cabins.get(cabin_class)
        if not isinstance(cabin, dict):
            continue
        if not cabin.get("available", False):
            continue

        points = _safe_int(cabin.get("points"))
        # PRO: API retorna available=true mas points=null para algumas cabines
        # Usamos -1 como sentinela: "disponivel, mas pontuacao nao informada pela API"
        if points is None or points <= 0:
            points = -1

        taxes_raw = _safe_float(cabin.get("taxes")) or 0.0
        taxes_ccy = _safe_str(cabin.get("taxes_currency"))
        taxes_brl = _convert_taxes_to_brl(taxes_raw, taxes_ccy)

        offers.append(_make_offer(
            airline=airline,
            cabin_class=cabin_class,
            miles=points,
            miles_program=default_program,
            taxes_original=taxes_raw,
            taxes_currency=taxes_ccy,
            taxes_brl=taxes_brl,
            seats=_safe_int(cabin.get("seats")),
            route=route,
            search_date=search_date,
            source="mcp_calendar",
        ))

    return offers


# ---------------------------------------------------------------------------
# Função principal pública
# ---------------------------------------------------------------------------

def extract_mcp_offers(raw_json: dict) -> list[dict]:
    """
    Ponto de entrada principal.

    Aceita o payload bruto retornado por:
      - mcp_client.call_mcp_search_all_airlines()
      - scripts/fetch_mcp_sample.py  (consolidado REST)
      - Qualquer fixture salvo em debug_dumps/

    Parâmetro
    ---------
    raw_json : dict
        JSON bruto conforme gerado pelo fetch_mcp_sample.py.
        Estrutura esperada:
          {
            "airlines": {
              "qatar_airways":   { "response_type": "flights", "flights": [...] },
              "british_airways": { "response_type": "calendar", "availability": {...} },
              "american_airlines": { "http_error": 400, ... },  # ignorado
              ...
            }
          }

    Retorna
    -------
    list[dict]
        Lista de UnifiedOffer ordenada por: cabin_class (melhor primeiro),
        depois por miles crescente.
    """
    if not isinstance(raw_json, dict):
        print("[mcp_offer_parser] [AVISO] raw_json nao e um dict. Retornando lista vazia.")
        return []

    airlines_data = raw_json.get("airlines")
    if not isinstance(airlines_data, dict):
        # Tenta formato alternativo (resposta MCP pura com result aninhado)
        result = raw_json.get("result") or raw_json.get("data") or {}
        airlines_data = result.get("airlines") if isinstance(result, dict) else None

    if not isinstance(airlines_data, dict) or not airlines_data:
        print("[mcp_offer_parser] [AVISO] Nenhuma chave 'airlines' encontrada no JSON.")
        return []

    all_offers: list[dict] = []
    skipped_errors = 0
    skipped_unavailable = 0

    for airline, data in airlines_data.items():
        if not isinstance(data, dict):
            continue

        # Ignora airlines com erro HTTP
        if "http_error" in data:
            skipped_errors += 1
            continue

        response_type = str(data.get("response_type") or "").lower()

        if response_type == "flights":
            offers = _parse_flights_response(airline, data)
        elif response_type == "calendar":
            offers = _parse_calendar_response(airline, data)
        else:
            # Tenta inferir: se tem "flights" key, é flights; se tem "availability", é calendar
            if "flights" in data:
                offers = _parse_flights_response(airline, data)
            elif "availability" in data:
                offers = _parse_calendar_response(airline, data)
            else:
                print(f"[mcp_offer_parser] [AVISO] Formato desconhecido para {airline}. Ignorando.")
                continue

        if not offers:
            skipped_unavailable += 1

        all_offers.extend(offers)

    # Ordena: melhor cabine primeiro, depois menor milhas
    cabin_rank = {c: i for i, c in enumerate(_CABIN_ORDER)}
    all_offers.sort(key=lambda o: (
        cabin_rank.get(o["cabin_class"], 99),
        o["miles"],
    ))

    print(
        f"[mcp_offer_parser] Extraidas {len(all_offers)} ofertas | "
        f"airlines com erro: {skipped_errors} | "
        f"sem disponibilidade: {skipped_unavailable}"
    )
    return all_offers


# ---------------------------------------------------------------------------
# Bloco de teste
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Localiza o sample JSON gerado pelo fetch_mcp_sample.py
    SAMPLE_FILE = (
        Path(__file__).parent
        / "debug_dumps"
        / "mcp_all_airlines_GRU_JFK_sample.json"
    )

    if not SAMPLE_FILE.exists():
        print(f"[ERRO] Arquivo de sample nao encontrado: {SAMPLE_FILE}")
        print("       Execute primeiro: python scripts/fetch_mcp_sample.py")
        sys.exit(1)

    print(f"Carregando: {SAMPLE_FILE.name}")
    with open(SAMPLE_FILE, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    offers = extract_mcp_offers(raw)

    print(f"\nTotal de ofertas extraidas: {len(offers)}")
    print("=" * 60)

    # ------------------------------------------------------------------
    # Verificacao: Qatar Airways deve ter 60.000 Avios + taxas em BRL
    # ------------------------------------------------------------------
    qatar_economy = [
        o for o in offers
        if o["airline"] == "qatar_airways" and o["cabin_class"] == "economy"
    ]

    print("\n[VERIFICACAO] Qatar Airways - Economy:")
    if qatar_economy:
        o = qatar_economy[0]
        assert o["miles"] == 60000, f"Esperava 60000 milhas, obteve {o['miles']}"
        assert o["miles_program"] == "Avios", f"Esperava 'Avios', obteve {o['miles_program']}"
        assert isinstance(o["taxes_brl"], float), "taxes_brl deve ser float"
        print(f"  miles         : {o['miles']:,}  -> OK (esperado: 60.000)")
        print(f"  miles_program : {o['miles_program']}  -> OK (esperado: Avios)")
        print(f"  taxes_original: {o['taxes_original']} {o['taxes_currency']}")
        print(f"  taxes_brl     : R$ {o['taxes_brl']:.2f}")
        print(f"  cabin_class   : {o['cabin_class']}")
        print(f"  source        : {o['source']}")
        print(f"  price_brl     : {o['price_brl']}  (None = aguardando tabela de configuracao)")
        print("\n  [TESTE PASSOU] Qatar Economy: 60.000 Avios + taxas BRL confirmados.")
    else:
        print("  [AVISO] Nenhuma oferta de economy da Qatar encontrada no sample.")
        print("          Verifique se o sample contem dados da qatar_airways.")

    # ------------------------------------------------------------------
    # Exibe todas as ofertas encontradas
    # ------------------------------------------------------------------
    print("\n[TODAS AS OFERTAS]")
    print(f"{'Airline':<25} {'Cabine':<18} {'Miles':>8} {'Programa':<15} {'Taxas BRL':>10}  {'Source'}")
    print("-" * 100)
    for o in offers:
        print(
            f"{o['airline']:<25} "
            f"{o['cabin_class']:<18} "
            f"{o['miles']:>8,} "
            f"{o['miles_program']:<15} "
            f"R$ {o['taxes_brl']:>8.2f}  "
            f"{o['source']}"
        )

    if not offers:
        print("  (nenhuma oferta disponivel no sample atual)")
        print("  Dica: A rota GRU->JFK pode nao ter disponibilidade na data buscada.")
        print("  Tente gerar um novo sample com uma rota que a Qatar opera diretamente.")
