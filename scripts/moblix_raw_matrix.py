from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(key)
    if v is None or str(v).strip() == "":
        return default
    return v


def build_payload(
    origin: str,
    destination: str,
    departure_date: str,
    adults: int,
    cabin_class: str,
    suppliers: Optional[List[str]],
    search_type: Optional[str],
    enable_deduplication: bool = True,
    max_connections: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Payload conforme doc: POST /api/v1/flights/search
    Campos principais: type, slices, passengers, cabinClass, suppliers, enableDeduplication, searchType
    """
    payload: Dict[str, Any] = {
        "type": "one_way",
        "slices": [{"origin": origin, "destination": destination, "departureDate": departure_date}],
        "passengers": [{"type": "adult", "count": int(adults)}],
        "cabinClass": cabin_class,
        "enableDeduplication": bool(enable_deduplication),
    }

    if max_connections is not None:
        payload["maxConnections"] = int(max_connections)

    if suppliers:
        payload["suppliers"] = suppliers

    if search_type is not None and search_type.strip() != "":
        payload["searchType"] = search_type

    return payload


def _root(resp: Dict[str, Any]) -> Dict[str, Any]:
    # A API pode devolver direto ou dentro de "data"
    if isinstance(resp.get("data"), dict):
        return resp["data"]
    return resp


def extract_total_points(offer: Dict[str, Any]) -> Optional[int]:
    price = offer.get("price")
    if not isinstance(price, dict):
        return None
    points_info = price.get("pointsInfo")
    if not isinstance(points_info, dict):
        return None
    tp = points_info.get("totalPoints")
    if isinstance(tp, (int, float)):
        return int(tp)
    if isinstance(tp, str):
        s = tp.strip().replace(".", "").replace(",", "")
        if s.isdigit():
            return int(s)
    return None


def sum_taxes_brl(offer: Dict[str, Any]) -> Optional[float]:
    price = offer.get("price")
    if not isinstance(price, dict):
        return None
    taxes = price.get("taxes")
    if not isinstance(taxes, list):
        return None
    total = 0.0
    ok = False
    for t in taxes:
        if not isinstance(t, dict):
            continue
        amt = t.get("amount")
        if isinstance(amt, (int, float)):
            total += float(amt)
            ok = True
        elif isinstance(amt, str):
            try:
                total += float(amt.replace(",", "."))
                ok = True
            except Exception:
                pass
    return total if ok else None


def get_total_brl(offer: Dict[str, Any]) -> Optional[float]:
    price = offer.get("price")
    if not isinstance(price, dict):
        return None
    total = price.get("total")
    if isinstance(total, (int, float)):
        return float(total)
    if isinstance(total, str):
        try:
            return float(total.replace(",", "."))
        except Exception:
            return None
    return None


def get_booking_url(offer: Dict[str, Any]) -> Optional[str]:
    booking = offer.get("booking")
    if isinstance(booking, dict):
        url = booking.get("bookingUrl")
        return url if isinstance(url, str) and url.strip() else None
    return None


def has_checked_bag_23kg(offer: Dict[str, Any]) -> bool:
    bags = offer.get("baggageIncluded")
    if not isinstance(bags, list):
        return False

    for b in bags:
        if not isinstance(b, dict):
            continue
        if b.get("isIncluded") is False:
            continue

        btype = str(b.get("type") or "").lower()
        desc = str(b.get("description") or "").lower()

        # heurísticas bem simples (vamos refinar depois com dumps de azul/gol)
        if "23" in desc and "kg" in desc:
            return True
        if "despach" in btype and ("23" in desc and "kg" in desc):
            return True
        if "despach" in desc and ("23" in desc and "kg" in desc):
            return True

    return False


def dump_json(data: Dict[str, Any], dump_dir: str, name: str) -> Path:
    out_dir = Path(dump_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}_{int(time.time())}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def summarize(resp: Dict[str, Any]) -> Dict[str, Any]:
    root = _root(resp)
    request_id = root.get("requestId") or resp.get("requestId")

    groups = root.get("flightGroups") if isinstance(root.get("flightGroups"), list) else []
    offers_total = 0
    offers_with_points = 0

    by_provider: Dict[str, int] = {}
    by_provider_with_points: Dict[str, int] = {}

    samples: Dict[str, Dict[str, Any]] = {}

    for g in groups:
        if not isinstance(g, dict):
            continue

        offers = g.get("offers") if isinstance(g.get("offers"), list) else []
        for off in offers:
            if not isinstance(off, dict):
                continue

            offers_total += 1

            provider = off.get("providerId") or off.get("provider") or off.get("source") or "N/D"
            provider = str(provider)

            by_provider[provider] = by_provider.get(provider, 0) + 1

            pts = extract_total_points(off)
            if pts is not None:
                offers_with_points += 1
                by_provider_with_points[provider] = by_provider_with_points.get(provider, 0) + 1

            # guarda 1 sample por provider (preferindo o que tem pontos)
            if provider not in samples or (samples[provider].get("totalPoints") is None and pts is not None):
                samples[provider] = {
                    "offerId": off.get("id"),
                    "providerId": provider,
                    "totalPoints": pts,
                    "taxes_total": sum_taxes_brl(off),
                    "total_brl": get_total_brl(off),
                    "tem_23kg": has_checked_bag_23kg(off),
                    "bookingUrl": get_booking_url(off),
                }

    return {
        "requestId": request_id,
        "groups_len": len(groups),
        "offers_total": offers_total,
        "offers_with_points": offers_with_points,
        "by_provider": by_provider,
        "by_provider_with_points": by_provider_with_points,
        "samples": samples,
        "top_keys": list(resp.keys()),
        "root_keys": list(root.keys()) if isinstance(root, dict) else [],
    }


def call_api(payload: Dict[str, Any], base_url: str, api_key: str, timeout: int) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/flights/search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    r = requests.post(url, json=payload, headers=headers, timeout=timeout)

    if r.status_code >= 400:
        body = (r.text or "")[:3000]
        raise RuntimeError(f"HTTP {r.status_code}\nBody:\n{body}")

    try:
        return r.json()
    except Exception:
        text = (r.text or "")[:3000]
        raise RuntimeError(f"Resposta não-JSON:\n{text}")


def main():
    load_dotenv(override=False)

    base_url = _env("MOBLIX_BASE_URL", "https://app.apidevoos.dev/api/v1") or ""
    api_key = _env("MOBLIX_API_KEY")
    timeout = int(_env("MOBLIX_TIMEOUT", "60") or "60")
    dump_dir = _env("MOBLIX_DEBUG_DIR", "debug_dumps") or "debug_dumps"

    if not api_key:
        raise SystemExit("ERRO: MOBLIX_API_KEY não definido no .env")

    parser = argparse.ArgumentParser(description="Moblix raw test matrix (milhas) por fornecedor")
    parser.add_argument("--origin", default="BSB", help="Origem (IATA aeroporto/cidade, ex: BSB)")
    parser.add_argument("--destination", default="SAO", help="Destino (IATA aeroporto/cidade, ex: SAO, GRU, VCP)")
    parser.add_argument("--date", default=None, help="Data ida YYYY-MM-DD (default: hoje+45d)")
    parser.add_argument("--adults", type=int, default=1)
    parser.add_argument("--cabin", default="economy", help="economy|premium_economy|business|first")
    parser.add_argument("--search-type", default="milhas", help="milhas|pagante|''")
    parser.add_argument(
        "--suppliers",
        default="latam,gol,azul",
        help="Lista separada por vírgula (ex: latam,gol,azul). Para testar 1: gol",
    )
    parser.add_argument(
        "--mode",
        choices=["each", "combined"],
        default="each",
        help="each=1 chamada por fornecedor | combined=uma chamada com todos",
    )
    args = parser.parse_args()

    dep_date = args.date or (date.today() + timedelta(days=45)).isoformat()

    suppliers_list = [s.strip().lower() for s in (args.suppliers or "").split(",") if s.strip()]
    if not suppliers_list:
        raise SystemExit("ERRO: informe ao menos 1 supplier em --suppliers (ex: latam)")

    origin = args.origin.strip().upper()
    destination = args.destination.strip().upper()
    cabin = args.cabin.strip().lower()
    search_type = args.search_type.strip().lower()

    print("=== MOBLIX RAW MATRIX ===")
    print("origin:", origin, "| destination:", destination, "| date:", dep_date)
    print("mode:", args.mode, "| suppliers:", suppliers_list, "| searchType:", search_type or "(omitido)")
    print("base_url:", base_url)

    runs: List[Tuple[str, List[str]]] = []
    if args.mode == "combined":
        runs.append(("combined", suppliers_list))
    else:
        for s in suppliers_list:
            runs.append((s, [s]))

    for label, sups in runs:
        print(f"\n--- RUN: {label} | suppliers={sups} ---")
        payload = build_payload(
            origin=origin,
            destination=destination,
            departure_date=dep_date,
            adults=int(args.adults),
            cabin_class=cabin,
            suppliers=sups,
            search_type=search_type,
            enable_deduplication=True,
            max_connections=None,
        )

        try:
            resp = call_api(payload, base_url=base_url, api_key=api_key, timeout=timeout)
        except Exception as e:
            print("ERRO na chamada:", e)
            continue

        dump_name = f"moblix_{label}_{origin}_{destination}_{dep_date}_{search_type or 'default'}"
        dump_path = dump_json(resp, dump_dir=dump_dir, name=dump_name)

        s = summarize(resp)
        print("requestId:", s["requestId"])
        print("groups_len:", s["groups_len"])
        print("offers_total:", s["offers_total"])
        print("offers_with_points:", s["offers_with_points"])
        print("by_provider:", s["by_provider"])
        print("by_provider_with_points:", s["by_provider_with_points"])
        print("dump:", str(dump_path))

        # samples (1 por provider)
        print("samples:")
        for prov, sample in (s["samples"] or {}).items():
            print(f"  - {prov}: {sample}")


if __name__ == "__main__":
    main()
