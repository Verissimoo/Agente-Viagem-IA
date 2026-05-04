"""
scripts/teste_fogo_pro.py
==========================
Valida se o plano PRO do Award Travel Finder entrega:
  1. tier: "pro" (nao "free")
  2. cabin_access: "all" (nao "economy_only")
  3. Numero de voo real (ex: QR701, nao QR0)
  4. Horarios reais (nao zerados como 00:00:00)
  5. Cabines premium (business/first) com pontos disponiveis

Rotas testadas:
  - DOH -> JFK  (Qatar Airways - tipo "flights", foco em luxo/first)
  - GRU -> LHR  (British Airways - "calendar" no free, "flights" esperado no pro)
"""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp_client import call_rest_availability

_TODAY = datetime.now()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _d(days: int) -> str:
    return (_TODAY + timedelta(days=days)).strftime("%Y-%m-%d")


def _is_zero_time(t):
    """True se horario zerado ou ausente."""
    if not t:
        return True
    return "00:00:00" in str(t)


def _validate_segment(seg: dict) -> dict:
    fnum = seg.get("flight_number") or ""
    dep  = seg.get("departure_time") or ""
    arr  = seg.get("arrival_time")   or ""

    fnum_ok = bool(fnum) and fnum not in ("QR0", "BA0", "") and len(fnum) > 2
    dep_ok  = not _is_zero_time(dep)
    arr_ok  = not _is_zero_time(arr)

    return {
        "flight_number":  fnum or "N/A",
        "departure_time": dep  or "N/A",
        "arrival_time":   arr  or "N/A",
        "fnum_ok": fnum_ok,
        "dep_ok":  dep_ok,
        "arr_ok":  arr_ok,
        "from":    seg.get("from") or seg.get("origin") or "?",
        "to":      seg.get("to")   or seg.get("destination") or "?",
        "aircraft": seg.get("aircraft") or "N/A",
        "duration": seg.get("duration") or "N/A",
    }


def _ok(v: bool) -> str:
    return "[OK] " if v else "[!!] "


def _print_cabins(cabins: dict) -> None:
    order = ["first", "business", "premium_economy", "economy"]
    print("    Cabines:")
    for cab in order:
        c = cabins.get(cab)
        if not c:
            continue
        avail = c.get("available", False)
        pts   = c.get("points", "--")
        seats = c.get("seats",  "--")
        icon  = "[OK]" if avail else "[ - ]"
        print(f"      {icon} {cab:<18} {str(pts):>8} pts  {seats} assentos")


def analyse_response(airline_label: str, raw: dict) -> dict:
    sep = "=" * 65
    print(f"\n{sep}")
    print(f"  COMPANHIA : {airline_label}")
    print(sep)

    # ── Tier / Plano ──────────────────────────────────────────────
    usage        = raw.get("usage", {})
    tier         = usage.get("tier",         "N/A")
    cabin_access = usage.get("cabin_access", "N/A")
    remaining    = usage.get("remaining_calls", "?")
    monthly      = usage.get("monthly_limit",   "?")

    tier_ok  = tier not in ("free", "N/A")
    cabin_ok = cabin_access == "all"

    print("\n  [PLANO]")
    print(f"    Tier         : {tier}  {_ok(tier_ok)}PRO")
    print(f"    Cabin Access : {cabin_access}  {_ok(cabin_ok)}ALL")
    print(f"    Chamadas     : {remaining}/{monthly}")

    # ── Dados de voo ──────────────────────────────────────────────
    data_block = raw.get("data", {})
    resp_type  = data_block.get("response_type", "N/A")
    route      = str(data_block.get("route", "N/A")).replace("\u2192", "->")
    date_s     = data_block.get("search_date", "N/A")

    print(f"\n  [TIPO DE RESPOSTA] : {resp_type.upper()}")
    print(f"  [ROTA/DATA]        : {route} | {date_s}")

    metrics = {"tier": tier, "cabin_access": cabin_access, "resp_type": resp_type, "label": airline_label}

    if resp_type == "calendar":
        avail    = data_block.get("availability", {})
        has_data = avail.get("data_available", False)
        print(f"\n  [CALENDAR] dados disponiveis: {'[SIM]' if has_data else '[NAO]'}")
        if has_data:
            _print_cabins(avail.get("cabins", {}))
        print("\n  [AVISO] Tipo 'calendar' nao entrega itinerario real.")
        return metrics

    if resp_type == "flights":
        flights = data_block.get("flights", [])
        print(f"\n  [VOOS ENCONTRADOS] : {len(flights)}")
        if not flights:
            print("  [!!] Nenhum voo retornado.")
            return metrics

        total_segs = good_fnum = good_dep = good_arr = 0

        for fi, flight in enumerate(flights):
            segs    = flight.get("segments") or []
            bundles = flight.get("bundles")  or []
            cabins  = flight.get("cabins")   or {}
            dur     = flight.get("duration", "N/A")

            print(f"\n  VOO #{fi+1}  |  Duracao: {dur}  |  Segmentos: {len(segs)}")

            for si, seg in enumerate(segs):
                info = _validate_segment(seg)
                total_segs += 1
                if info["fnum_ok"]:  good_fnum += 1
                if info["dep_ok"]:   good_dep  += 1
                if info["arr_ok"]:   good_arr  += 1

                print(f"    Seg {si+1}: {info['from']} -> {info['to']}  (aeronave: {info['aircraft']})")
                print(f"      N. Voo  : {_ok(info['fnum_ok'])}{info['flight_number']}")
                print(f"      Partida : {_ok(info['dep_ok'])}{info['departure_time']}")
                print(f"      Chegada : {_ok(info['arr_ok'])}{info['arrival_time']}")
                print(f"      Duracao : {info['duration']}")

            if bundles:
                print(f"    Bundles: {len(bundles)}")
                for b in bundles:
                    sts  = b.get("status", "?")
                    icon = "[OK]" if str(sts).upper() == "AVAILABLE" else "[ - ]"
                    cab  = b.get("cabin_class", "?")
                    pts  = b.get("points", "?")
                    prog = b.get("points_name", "?")
                    tx   = b.get("taxes", "?")
                    ccy  = b.get("taxes_currency", "")
                    print(f"      {icon} {cab:<18} {str(pts):>8} {prog:<12} Taxas: {tx} {ccy}  [{sts}]")
            else:
                _print_cabins(cabins)

        print(f"\n  {'-'*60}")
        print("  [RESUMO DE QUALIDADE]")
        print(f"    Segmentos totais  : {total_segs}")
        print(f"    N. voo real       : {good_fnum}/{total_segs}  {_ok(good_fnum==total_segs)}")
        print(f"    Partida real      : {good_dep}/{total_segs}   {_ok(good_dep==total_segs)}")
        print(f"    Chegada real      : {good_arr}/{total_segs}   {_ok(good_arr==total_segs)}")

        if good_fnum == total_segs and good_dep == total_segs and total_segs > 0:
            print("\n  [SUCESSO] ITINERARIO COMPLETO - PRO esta funcionando!")
        else:
            print("\n  [AVISO] Itinerario incompleto - horarios ainda zerados.")

    return metrics


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------

def run():
    SEARCHES = [
        {"airline": "qatar_airways",   "origin": "DOH", "dest": "JFK",
         "days": 120, "label": "Qatar Airways (DOH -> JFK)"},
        {"airline": "british_airways", "origin": "GRU", "dest": "LHR",
         "days": 90,  "label": "British Airways (GRU -> LHR)"},
    ]

    print("\n" + "=" * 65)
    print("  TESTE DE FOGO PRO - Award Travel Finder")
    print(f"  Executado: {_TODAY.strftime('%d/%m/%Y %H:%M')}")
    print("=" * 65)

    all_metrics = []
    for i, s in enumerate(SEARCHES):
        if i > 0:
            time.sleep(2)
        date_str = _d(s["days"])
        try:
            raw = call_rest_availability(s["airline"], s["origin"], s["dest"], date_str)
            m   = analyse_response(s["label"], raw)
            all_metrics.append(m)
        except Exception as e:
            print(f"\n  [ERRO] {s['label']}: {e}")
            all_metrics.append({"label": s["label"], "tier": "ERRO",
                                 "cabin_access": "ERRO", "resp_type": "ERRO"})

    # ── Tabela final ──────────────────────────────────────────────
    print(f"\n\n{'='*65}")
    print("  TABELA COMPARATIVA RESUMIDA")
    print(f"  {'Companhia':<35} {'Tier':<12} {'Cabines':<20} {'Tipo'}")
    print(f"  {'-'*65}")
    for r in all_metrics:
        tier_f  = "[OK] PRO"  if r["tier"]         not in ("free","N/A","ERRO") else f"[!!] {r['tier']}"
        cabin_f = "[OK] ALL"  if r["cabin_access"] == "all"                     else f"[!!] {r['cabin_access']}"
        print(f"  {r['label']:<35} {tier_f:<12} {cabin_f:<20} {r['resp_type']}")

    print("")
    print("  Free -> tier=free,  cabin_access=economy_only, horarios zerados")
    print("  PRO  -> tier=pro,   cabin_access=all,          horarios reais")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    run()
