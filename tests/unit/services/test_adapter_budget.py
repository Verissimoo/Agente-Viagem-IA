"""Resiliência: a fase de adapters respeita o orçamento de tempo e retorna
resultado parcial em vez de travar por causa de um provider lento."""
import time
from datetime import date, datetime

import backend.app.services.search_orchestrator as so
from backend.app.domain.models import (
    Itinerary, Segment, SearchRequest, SourceType, TripType, UnifiedOffer,
)
from backend.app.infrastructure.tracer import PipelineTracer


def _offer(price: float) -> UnifiedOffer:
    seg = Segment(
        origin="BSB", destination="SSA", carrier="G3",
        departure_dt=datetime(2099, 1, 1, 8, 0),
        arrival_dt=datetime(2099, 1, 1, 10, 0),
    )
    return UnifiedOffer(
        source=SourceType.KAYAK, airline="GOL", trip_type=TripType.ONEWAY,
        outbound=Itinerary(segments=[seg]), price_brl=price,
    )


class _Dummy:
    pass


def test_budget_returns_partial_without_waiting_slow(monkeypatch):
    monkeypatch.setattr(so, "_ADAPTER_BUDGET_S", 0.5)
    monkeypatch.setattr(so, "_ADAPTER_MAP", {"FAST": _Dummy, "SLOW": _Dummy})

    def fake_run_one(cia, cls, req, use_fixtures, debug_dump):
        if cia == "SLOW":
            time.sleep(3.0)            # estoura o orçamento de 0.5s
            return cia, [_offer(999)], None, 3000
        return cia, [_offer(100)], None, 10

    monkeypatch.setattr(so, "_run_one_adapter", fake_run_one)

    req = SearchRequest(
        origin=["BSB"], destination=["SSA"],
        date_start=date(2099, 1, 1), date_end=date(2099, 1, 1),
    )
    tracer = PipelineTracer("test-budget")

    t = time.time()
    offers = so._execute_dates_x_adapters_parallel(
        [req], ["FAST", "SLOW"], False, False, tracer,
    )
    elapsed = time.time() - t

    # Retornou antes do SLOW terminar (3s) — não travou.
    assert elapsed < 2.0
    # Só a oferta rápida entrou; a lenta foi abandonada.
    assert len(offers) == 1
    assert offers[0].price_brl == 100
