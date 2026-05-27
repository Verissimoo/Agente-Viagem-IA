"""Asserções de que o BuscaMilhas adapter extrai os campos críticos do JSON
para evitar regressões silenciosas onde uma coluna inteira fica vazia."""
from __future__ import annotations

import datetime as dt

import pytest

from backend.app.domain.models import CabinClass, SearchRequest, TripType
from backend.app.providers.buscamilhas.adapter import (
    BuscaMilhasAzulAdapter,
    BuscaMilhasGolAdapter,
    BuscaMilhasLatamAdapter,
)


def _make_req(origin="GRU", destination="SSA", days_ahead=15):
    target = dt.date.today() + dt.timedelta(days=days_ahead)
    return SearchRequest(
        origin=[origin],
        destination=[destination],
        date_start=target,
        date_end=target,
        adults=1,
        cabin=CabinClass.ECONOMY,
        trip_type=TripType.ONEWAY,
    )


@pytest.mark.parametrize("adapter_cls,prog_name", [
    (BuscaMilhasGolAdapter, "Smiles"),
    (BuscaMilhasLatamAdapter, "LATAM Pass"),
    (BuscaMilhasAzulAdapter, "TudoAzul"),
])
def test_buscamilhas_extracts_essential_fields(adapter_cls, prog_name):
    """Cada oferta de milhas DEVE carregar miles, taxas, segments com carrier
    IATA real, flight_number, datas dep/arr. Sem isso a planilha do quote-
    complete fica com coluna vazia."""
    req = _make_req()
    offers = adapter_cls().search(req, use_fixtures=False, debug_dump=False)
    if not offers:
        pytest.skip(f"{prog_name}: sem ofertas para amostragem")

    sample = offers[0]
    # Campos críticos
    assert sample.miles and sample.miles > 0, f"{prog_name}: miles ausente/zero"
    assert sample.taxes_brl is not None, f"{prog_name}: taxas ausentes"
    assert sample.outbound and sample.outbound.segments, f"{prog_name}: sem outbound"

    seg = sample.outbound.segments[0]
    # Carrier deve ser IATA 2-3 chars (não nome cheio como "LATAM AIRLINES (TAM)")
    assert seg.carrier and 2 <= len(seg.carrier) <= 3, (
        f"{prog_name}: carrier {seg.carrier!r} deveria ser IATA curto"
    )
    assert seg.flight_number, f"{prog_name}: flight_number ausente"
    assert seg.departure_dt, f"{prog_name}: departure_dt ausente"
    assert seg.arrival_dt, f"{prog_name}: arrival_dt ausente"
    assert seg.arrival_dt > seg.departure_dt, f"{prog_name}: chegada antes da partida"


@pytest.mark.parametrize("adapter_cls,prog_name", [
    (BuscaMilhasGolAdapter, "Smiles"),
    (BuscaMilhasLatamAdapter, "LATAM Pass"),
    (BuscaMilhasAzulAdapter, "TudoAzul"),
])
def test_buscamilhas_no_absurd_duration(adapter_cls, prog_name):
    """Filtro de duração: nenhuma oferta com >18h em rota intra-Brasil
    (BuscaMilhas devolve partner-awards fantasmas overnight)."""
    req = _make_req()
    offers = adapter_cls().search(req, use_fixtures=False, debug_dump=False)
    if not offers:
        pytest.skip(f"{prog_name}: sem ofertas")
    too_long = [o for o in offers if o.outbound.duration_min and o.outbound.duration_min > 18 * 60]
    assert not too_long, (
        f"{prog_name}: {len(too_long)} ofertas com >18h (provável partner award fantasma)"
    )


def test_buscamilhas_operating_carrier_is_not_program():
    """Quando GOL Smiles emite voo TAP, o carrier do segmento DEVE ser 'TP'
    (operating real) e não 'GOL' (programa). Bug crítico para validação."""
    req = _make_req(origin="LIS", destination="MAD")
    offers = BuscaMilhasGolAdapter().search(req, use_fixtures=False, debug_dump=False)
    if not offers:
        pytest.skip("sem ofertas LIS-MAD")
    # Nenhuma oferta deve ter carrier="GOL" (programa) no segment.
    leaks = [o for o in offers if o.outbound.segments[0].carrier == "GOL"]
    assert not leaks, (
        f"{len(leaks)} ofertas Smiles em LIS-MAD com carrier='GOL' "
        f"no segment (deveria ser TP/IB/UX etc operando)"
    )
