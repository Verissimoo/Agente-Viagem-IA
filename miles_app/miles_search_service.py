from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

from miles_app.moblix_client import search_flights
from miles_app.moblix_offer_parser import extract_latam_miles_rows


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


def search_latam_miles_in_range(
    origin: str,
    destination: str,
    departure_date: Union[str, date],
    return_date: Optional[Union[str, date]],
    flex_days: int,
    list_size: int = 15,
) -> Dict[str, Any]:
    """
    LATAM milhas only.
    Flex: varia ida (e alinha volta no mesmo offset quando RT).
    """
    max_calls = _env_int("MILES_MAX_CALLS", 20)
    trip_type = "RT" if return_date else "OW"

    dep_dates = _date_range(departure_date, flex_days)
    ret_dates = _date_range(return_date, flex_days) if return_date else [None]

    planned: List[Tuple[str, Optional[str]]] = []
    if trip_type == "OW":
        for d in dep_dates:
            planned.append((d, None))
    else:
        for i in range(min(len(dep_dates), len(ret_dates))):
            planned.append((dep_dates[i], ret_dates[i]))

    planned = planned[:max_calls]

    all_rows: List[Dict[str, Any]] = []
    request_ids: List[str] = []

    for dep, ret in planned:
        raw = search_flights(
            origin=origin,
            destination=destination,
            departure_date=dep,
            return_date=ret,
            suppliers=["latam"],
            search_type=None,
        )
        request_ids.append(raw.get("requestId") or "")
        rows = extract_latam_miles_rows(raw, trip_type=trip_type)
        all_rows.extend(rows)

    # dedup por GroupId+Trecho+Saída+Data
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for r in all_rows:
        k = (r.get("GroupId"), r.get("Trecho"), r.get("Saída"), r.get("Data"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)

    # ---------------------------
    # ✅ bag_map global: Milhas -> Bagagem (pts)
    # só aprende pares válidos (bag > miles)
    # e depois preenche onde Bagagem está faltando
    # ---------------------------
    bag_map: Dict[int, int] = {}

    # aprende usando só IDA (evita duplicar por RT)
    for r in uniq:
        if str(r.get("Trecho") or "").upper() != "IDA":
            continue
        miles = _as_int(r.get("Milhas"))
        bag = _as_int(r.get("Bagagem"))
        if miles is None or bag is None:
            continue
        if bag <= miles:
            # invalida pela regra do projeto
            continue
        prev = bag_map.get(miles)
        if prev is None or bag < prev:
            bag_map[miles] = bag

    # preenche onde faltar
    for r in uniq:
        miles = _as_int(r.get("Milhas"))
        bag = _as_int(r.get("Bagagem"))
        if miles is None:
            continue
        if bag is None:
            if miles in bag_map:
                r["Bagagem"] = bag_map[miles]
        else:
            # se veio inválido, zera
            if bag <= miles:
                r["Bagagem"] = "—"

    # ordena por milhas e taxa
    uniq.sort(key=lambda x: (x.get("Milhas") or 10**18, x.get("Taxas (R$)") or 10**18))

    return {
        "rows": uniq[: max(1, int(list_size))],
        "debug": {
            "planned_calls": len(planned),
            "rows_total": len(uniq),
            "trip_type": trip_type,
            "suppliers": ["latam"],
            "requestIds": [rid for rid in request_ids if rid],
            "bag_map_size": len(bag_map),
        },
    }








