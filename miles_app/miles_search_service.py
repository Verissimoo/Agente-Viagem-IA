from __future__ import annotations

"""
miles_search_service.py  (v2 — BuscaMilhas)
--------------------------------------------
Substitui a integração Moblix pela API Busca Milhas.
Uma requisição por companhia (regra da API).
Suporta GOL, LATAM e AZUL (nacionais).
"""

import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

from miles_app.buscamilhas_client import search_flights_buscamilhas, COMPANHIAS_NACIONAIS
from miles_app.buscamilhas_offer_parser import extract_rows_from_buscamilhas, debug_raw_json


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except Exception:
        return default


def _as_date_obj(x: Union[str, date, None]) -> Optional[date]:
    if x is None:
        return None
    if isinstance(x, date):
        return x
    if isinstance(x, str):
        return datetime.strptime(x, "%Y-%m-%d").date()
    return None


def _iso(d: date) -> str:
    return d.isoformat()


def _to_br_date(iso_str: Optional[str]) -> Optional[str]:
    """YYYY-MM-DD → DD/MM/AAAA  (formato que a API exige)"""
    if not iso_str:
        return None
    try:
        d = datetime.strptime(iso_str[:10], "%Y-%m-%d")
        return d.strftime("%d/%m/%Y")
    except Exception:
        return iso_str


def _date_range(center: Union[str, date], flex: int) -> List[str]:
    d0 = _as_date_obj(center)
    if d0 is None:
        return []
    return [_iso(d0 + timedelta(days=off)) for off in range(-flex, flex + 1)]


def _as_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        if isinstance(v, str) and v.strip() in ("", "—", "-", "None", "null"):
            return None
        return int(float(v))
    except Exception:
        return None


# ------------------------------------------------------------------
# Função principal
# ------------------------------------------------------------------

def search_miles_in_range(
    origin: str,
    destination: str,
    departure_date: Union[str, date],
    return_date: Optional[Union[str, date]],
    flex_days: int,
    list_size: int = 15,
    companhias: Optional[List[str]] = None,
    return_raw: bool = False,       # se True, inclui raw JSON no retorno (debug)
) -> Dict[str, Any]:
    """
    Busca milhas nas companhias selecionadas com flexibilidade de datas.

    Parâmetros:
      - origin / destination : códigos IATA
      - departure_date        : data ida (ISO YYYY-MM-DD ou date)
      - return_date           : data volta ou None (OW)
      - flex_days             : ±N dias de flexibilidade
      - list_size             : máx linhas no retorno
      - companhias            : lista ["LATAM","GOL","AZUL"] ou None (todas nacionais)
      - return_raw            : inclui raw JSON bruto para debug
    """
    max_calls = _env_int("MILES_MAX_CALLS", 20)
    trip_type = "RT" if return_date else "OW"

    if companhias is None:
        companhias = COMPANHIAS_NACIONAIS

    dep_dates = _date_range(departure_date, flex_days)
    ret_dates = _date_range(return_date, flex_days) if return_date else [None]

    # alinha datas pelo mesmo offset
    planned: List[Tuple[str, Optional[str]]] = []
    if trip_type == "OW":
        for d in dep_dates:
            planned.append((d, None))
    else:
        for i in range(min(len(dep_dates), len(ret_dates))):
            planned.append((dep_dates[i], ret_dates[i]))

    planned = planned[:max_calls]

    all_rows: List[Dict[str, Any]] = []
    raw_responses: List[Dict[str, Any]] = []
    errors: List[str] = []
    call_count = 0

    for dep_iso, ret_iso in planned:
        dep_br = _to_br_date(dep_iso)
        ret_br = _to_br_date(ret_iso)

        for companhia in companhias:
            call_count += 1
            try:
                raw = search_flights_buscamilhas(
                    companhia=companhia,
                    origem=origin,
                    destino=destination,
                    data_ida=dep_br,
                    data_volta=ret_br,
                    somente_milhas=True,
                    somente_pagante=False,
                )

                if return_raw:
                    raw_responses.append({
                        "companhia": companhia,
                        "dep": dep_br,
                        "ret": ret_br,
                        "raw": raw,
                        "debug_preview": debug_raw_json(raw),
                    })

                rows = extract_rows_from_buscamilhas(raw, companhia=companhia, trip_type=trip_type)
                all_rows.extend(rows)

            except Exception as e:
                errors.append(f"{companhia} {dep_br}: {e}")

    # dedup por Companhia+Trecho+NumeroVoo+Data
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for r in all_rows:
        k = (r.get("Companhia"), r.get("Trecho"), r.get("NumeroVoo"), r.get("Data"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)

    # bag_map global: aprende pares Milhas→Bagagem válidos e preenche lacunas
    bag_map: Dict[Tuple[str, int], int] = {}
    for r in uniq:
        if str(r.get("Trecho") or "").upper() != "IDA":
            continue
        comp = str(r.get("Companhia") or "")
        miles = _as_int(r.get("Milhas"))
        bag = _as_int(r.get("Bagagem"))
        if miles is None or bag is None or bag <= miles:
            continue
        key = (comp, miles)
        prev = bag_map.get(key)
        if prev is None or bag < prev:
            bag_map[key] = bag

    for r in uniq:
        comp = str(r.get("Companhia") or "")
        miles = _as_int(r.get("Milhas"))
        bag = _as_int(r.get("Bagagem"))
        if miles is None:
            continue
        key = (comp, miles)
        if bag is None:
            if key in bag_map:
                r["Bagagem"] = bag_map[key]
        elif bag <= miles:
            r["Bagagem"] = "—"

    # ordena por milhas e taxa
    uniq.sort(key=lambda x: (x.get("Milhas") or 10**18, x.get("Taxas (R$)") or 10**18))

    result: Dict[str, Any] = {
        "rows": uniq[: max(1, int(list_size))],
        "debug": {
            "trip_type": trip_type,
            "planned_date_pairs": len(planned),
            "companhias": companhias,
            "total_calls": call_count,
            "rows_total": len(uniq),
            "bag_map_size": len(bag_map),
            "errors": errors,
        },
    }

    if return_raw:
        result["raw_responses"] = raw_responses

    return result


# ------------------------------------------------------------------
# Compat alias (mantém chamadas antigas funcionando)
# ------------------------------------------------------------------

def search_latam_miles_in_range(
    origin: str,
    destination: str,
    departure_date: Union[str, date],
    return_date: Optional[Union[str, date]],
    flex_days: int,
    list_size: int = 15,
) -> Dict[str, Any]:
    """Compat: mantém interface antiga, busca só LATAM."""
    return search_miles_in_range(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        flex_days=flex_days,
        list_size=list_size,
        companhias=["LATAM"],
    )








