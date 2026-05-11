"""
Testes obrigatórios da Fase 2 — encaixe do voo doméstico.

Valida:
  1. Data correta da busca doméstica (mesmo dia / dia anterior / dia seguinte)
  2. Janela de horário calculada corretamente
  3. Layovers preenchidos nas ofertas
  4. Toggle de bagagem refiltra em tempo real (rebucket_fit)

Execução:
    python tests/test_segment_split_v2.py
"""
from __future__ import annotations

import io
import os
import sys
from datetime import datetime

# Força UTF-8 no stdout/stderr (necessário no Windows com cp1252).
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass

from pcd.agents.segment_split import (
    SegmentSplitAgent,
    KayakOffer,
    rebucket_fit,
)


def _mk_intl(origin: str, destination: str,
             dep_iso: str | None, arr_iso: str | None,
             price_brl: float = 4500.0) -> KayakOffer:
    return KayakOffer(
        origin=origin, destination=destination,
        airlines=["LATAM"], airlines_iata=["LA"],
        departure_dt=datetime.fromisoformat(dep_iso) if dep_iso else None,
        arrival_dt=datetime.fromisoformat(arr_iso) if arr_iso else None,
        duration_min=730, stops=0, price_brl=price_brl,
        raw={"leg_id": f"intl_{origin}_{destination}_{dep_iso}"},
    )


def _expect(label: str, got, expected, ok_list: list[bool], details: list[str]):
    ok = (got == expected)
    ok_list.append(ok)
    flag = "✓" if ok else "✗"
    details.append(f"    [{flag}] {label}: got={got!r}  expected={expected!r}")
    return ok


def case_1_bsb_mia_madrugada():
    print("=" * 78)
    print("CASO 1: BSB → MIA, voo intl GRU→MIA decola 18/06 às 01:00")
    print("        Esperado: busca BSB→GRU em 17/06 (day_before), janela 13:00-22:30")
    print("=" * 78)
    intl = _mk_intl("GRU", "MIA", "2026-06-18T01:00:00", "2026-06-18T08:25:00")
    fit = SegmentSplitAgent().fit_domestic_leg(
        intl_offer=intl, other_endpoint="BSB",
        intl_direction="from_gru", adults=1, with_baggage=False,
    )

    ok_list, details = [], []
    _expect("search_date", fit.search_date, "2026-06-17", ok_list, details)
    _expect("search_date_offset", fit.search_date_offset, "day_before", ok_list, details)
    _expect("window_start (12h antes da partida)",
            fit.target_window_start, datetime.fromisoformat("2026-06-17T13:00:00"),
            ok_list, details)
    _expect("window_end (2h30 antes da partida)",
            fit.target_window_end, datetime.fromisoformat("2026-06-17T22:30:00"),
            ok_list, details)

    print(f"  search_date={fit.search_date} offset={fit.search_date_offset}")
    print(f"  window=[{fit.target_window_start} .. {fit.target_window_end}]")
    print(f"  compatibles={len(fit.compatible_offers)} incompat={len(fit.incompatible_offers)} no_results={fit.no_results}")
    if fit.compatible_offers:
        sample = fit.compatible_offers[0]
        print(
            f"  sample compat: {sample.origin}->{sample.destination} "
            f"{sample.departure_dt}->{sample.arrival_dt} "
            f"layover={sample.layover_minutes}min R${sample.price_brl:,.2f}"
        )
    for line in details:
        print(line)
    return all(ok_list)


def case_2_bsb_mia_dia():
    print("=" * 78)
    print("CASO 2: BSB → MIA, voo intl GRU→MIA decola 18/06 às 14:00")
    print("        Esperado: busca BSB→GRU em 18/06 (same_day), janela ~02:00-11:30")
    print("=" * 78)
    intl = _mk_intl("GRU", "MIA", "2026-06-18T14:00:00", "2026-06-18T21:25:00")
    fit = SegmentSplitAgent().fit_domestic_leg(
        intl_offer=intl, other_endpoint="BSB",
        intl_direction="from_gru", adults=1, with_baggage=False,
    )

    ok_list, details = [], []
    _expect("search_date", fit.search_date, "2026-06-18", ok_list, details)
    _expect("search_date_offset", fit.search_date_offset, "same_day", ok_list, details)
    _expect("window_end (2h30 antes da partida)",
            fit.target_window_end, datetime.fromisoformat("2026-06-18T11:30:00"),
            ok_list, details)
    _expect("window_start (12h antes da partida)",
            fit.target_window_start, datetime.fromisoformat("2026-06-18T02:00:00"),
            ok_list, details)

    print(f"  search_date={fit.search_date} offset={fit.search_date_offset}")
    print(f"  window=[{fit.target_window_start} .. {fit.target_window_end}]")
    print(f"  compatibles={len(fit.compatible_offers)} incompat={len(fit.incompatible_offers)} no_results={fit.no_results}")
    if fit.compatible_offers:
        sample = fit.compatible_offers[0]
        print(
            f"  sample compat: layover={sample.layover_minutes}min "
            f"({sample.departure_dt}->{sample.arrival_dt}) R${sample.price_brl:,.2f}"
        )
    for line in details:
        print(line)
    return all(ok_list)


def case_3_mad_bsb_manha():
    print("=" * 78)
    print("CASO 3: MAD → BSB, voo intl MAD→GRU chega em GRU 18/06 às 06:00")
    print("        Esperado: busca GRU→BSB em 18/06 (same_day), janela 08:30 em diante")
    print("=" * 78)
    intl = _mk_intl("MAD", "GRU", "2026-06-17T18:00:00", "2026-06-18T06:00:00")
    fit = SegmentSplitAgent().fit_domestic_leg(
        intl_offer=intl, other_endpoint="BSB",
        intl_direction="to_gru", adults=1, with_baggage=False,
    )

    ok_list, details = [], []
    _expect("search_date", fit.search_date, "2026-06-18", ok_list, details)
    _expect("search_date_offset", fit.search_date_offset, "same_day", ok_list, details)
    _expect("window_start (2h30 após chegada)",
            fit.target_window_start, datetime.fromisoformat("2026-06-18T08:30:00"),
            ok_list, details)
    _expect("window_end (12h após chegada)",
            fit.target_window_end, datetime.fromisoformat("2026-06-18T18:00:00"),
            ok_list, details)

    print(f"  search_date={fit.search_date} offset={fit.search_date_offset}")
    print(f"  window=[{fit.target_window_start} .. {fit.target_window_end}]")
    print(f"  compatibles={len(fit.compatible_offers)} incompat={len(fit.incompatible_offers)} no_results={fit.no_results}")
    for line in details:
        print(line)
    return all(ok_list)


def case_4_mad_bsb_tarde_noite():
    print("=" * 78)
    print("CASO 4: MAD → BSB, voo intl MAD→GRU chega em GRU 18/06 às 23:30")
    print("        Esperado: busca GRU→BSB em 19/06 (day_after)")
    print("=" * 78)
    intl = _mk_intl("MAD", "GRU", "2026-06-18T11:30:00", "2026-06-18T23:30:00")
    fit = SegmentSplitAgent().fit_domestic_leg(
        intl_offer=intl, other_endpoint="BSB",
        intl_direction="to_gru", adults=1, with_baggage=False,
    )

    ok_list, details = [], []
    _expect("search_date_offset", fit.search_date_offset, "day_after", ok_list, details)
    # janela: 02:00-11:30 do dia seguinte
    _expect("window_start (chegada+2h30)",
            fit.target_window_start, datetime.fromisoformat("2026-06-19T02:00:00"),
            ok_list, details)
    _expect("window_end (chegada+12h)",
            fit.target_window_end, datetime.fromisoformat("2026-06-19T11:30:00"),
            ok_list, details)

    print(f"  search_date={fit.search_date} offset={fit.search_date_offset}")
    print(f"  window=[{fit.target_window_start} .. {fit.target_window_end}]")
    print(f"  compatibles={len(fit.compatible_offers)} incompat={len(fit.incompatible_offers)} no_results={fit.no_results}")
    for line in details:
        print(line)
    return all(ok_list)


def case_5_cgb_mcz_dois_lados():
    print("=" * 78)
    print("CASO 5: CGB → MCZ via GRU — encaixe em ambas as pernas")
    print("=" * 78)
    agent = SegmentSplitAgent()

    # Perna 1: CGB→GRU; encaixe = GRU→MCZ (to_gru)
    leg_arr = _mk_intl("CGB", "GRU", "2026-08-10T10:00:00", "2026-08-10T13:00:00",
                       price_brl=900.0)
    fit_to = agent.fit_domestic_leg(
        intl_offer=leg_arr, other_endpoint="MCZ",
        intl_direction="to_gru", adults=1, with_baggage=False,
    )

    # Perna 2: GRU→MCZ; encaixe = CGB→GRU (from_gru)
    leg_dep = _mk_intl("GRU", "MCZ", "2026-08-10T18:00:00", "2026-08-10T21:00:00",
                       price_brl=900.0)
    fit_from = agent.fit_domestic_leg(
        intl_offer=leg_dep, other_endpoint="CGB",
        intl_direction="from_gru", adults=1, with_baggage=False,
    )

    ok_list, details = [], []
    _expect("perna 1 (to_gru) search_date", fit_to.search_date, "2026-08-10",
            ok_list, details)
    _expect("perna 1 offset", fit_to.search_date_offset, "same_day", ok_list, details)
    _expect("perna 1 win_start (chegada+2h30)",
            fit_to.target_window_start, datetime.fromisoformat("2026-08-10T15:30:00"),
            ok_list, details)

    _expect("perna 2 (from_gru) search_date", fit_from.search_date, "2026-08-10",
            ok_list, details)
    _expect("perna 2 offset", fit_from.search_date_offset, "same_day",
            ok_list, details)
    _expect("perna 2 win_end (partida-2h30)",
            fit_from.target_window_end, datetime.fromisoformat("2026-08-10T15:30:00"),
            ok_list, details)

    print(f"  perna1 to_gru: search={fit_to.search_date} ({fit_to.search_date_offset}) "
          f"win=[{fit_to.target_window_start} .. {fit_to.target_window_end}] "
          f"compat={len(fit_to.compatible_offers)}")
    print(f"  perna2 from_gru: search={fit_from.search_date} ({fit_from.search_date_offset}) "
          f"win=[{fit_from.target_window_start} .. {fit_from.target_window_end}] "
          f"compat={len(fit_from.compatible_offers)}")
    for line in details:
        print(line)
    return all(ok_list)


def case_6_rebucket_bagagem():
    print("=" * 78)
    print("CASO 6 (extra): rebucket_fit reage ao toggle de bagagem")
    print("=" * 78)
    intl = _mk_intl("GRU", "MIA", "2026-06-18T14:00:00", "2026-06-18T21:25:00")
    fit = SegmentSplitAgent().fit_domestic_leg(
        intl_offer=intl, other_endpoint="BSB",
        intl_direction="from_gru", adults=1, with_baggage=False,
    )

    ok_list, details = [], []

    # Original: 2h30 (sem bagagem) → janela termina às 11:30
    _expect("janela orig (sem bag)",
            fit.target_window_end, datetime.fromisoformat("2026-06-18T11:30:00"),
            ok_list, details)

    # Re-bucket com bagagem: 4h → janela termina às 10:00 (mais apertada)
    rebucketed = rebucket_fit(fit, with_baggage=True)
    _expect("nova janela (com bag) — termina 4h antes da partida",
            rebucketed.target_window_end, datetime.fromisoformat("2026-06-18T10:00:00"),
            ok_list, details)
    _expect("with_baggage atualizado", rebucketed.with_baggage, True, ok_list, details)
    # Retorno ao false deve dar a janela original
    rebucketed2 = rebucket_fit(rebucketed, with_baggage=False)
    _expect("voltando ao false — janela retorna a 11:30",
            rebucketed2.target_window_end, datetime.fromisoformat("2026-06-18T11:30:00"),
            ok_list, details)

    print(f"  orig com_bag=False win_end={fit.target_window_end}")
    print(f"  rebucket com_bag=True win_end={rebucketed.target_window_end}")
    print(f"  rebucket com_bag=False win_end={rebucketed2.target_window_end}")
    for line in details:
        print(line)
    return all(ok_list)


def main():
    runners = [
        ("Caso 1 (BSB→MIA madrugada)", case_1_bsb_mia_madrugada),
        ("Caso 2 (BSB→MIA dia)",       case_2_bsb_mia_dia),
        ("Caso 3 (MAD→BSB manhã)",     case_3_mad_bsb_manha),
        ("Caso 4 (MAD→BSB noite)",     case_4_mad_bsb_tarde_noite),
        ("Caso 5 (CGB→MCZ via GRU)",   case_5_cgb_mcz_dois_lados),
        ("Caso 6 (rebucket bagagem)",  case_6_rebucket_bagagem),
    ]
    results = []
    for label, fn in runners:
        ok = fn()
        results.append((label, ok))
        print()

    print("=" * 78)
    print("RESUMO")
    print("=" * 78)
    for label, ok in results:
        print(f"  {'OK ' if ok else 'XX '} {label}")
    fails = sum(1 for _, ok in results if not ok)
    print(f"\nTotal: {len(results)} casos · falhas: {fails}")
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
