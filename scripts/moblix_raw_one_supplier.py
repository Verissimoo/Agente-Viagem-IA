from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from datetime import date, timedelta

import requests
from dotenv import load_dotenv


def _env(key: str, default: str | None = None) -> str | None:
    v = os.getenv(key)
    if v is None or str(v).strip() == "":
        return default
    return v


def build_payload_one_way(
    origin: str,
    destination: str,
    departure_date: str,
    adults: int = 1,
    cabin_class: str = "economy",
    supplier: str = "latam",
    search_type: str = "milhas",
) -> dict:
    """
    Payload mínimo baseado na doc:
      type, slices, passengers, cabinClass, suppliers, enableDeduplication, searchType
    """
    payload = {
        "type": "one_way",
        "slices": [
            {"origin": origin, "destination": destination, "departureDate": departure_date}
        ],
        "passengers": [{"type": "adult", "count": int(adults)}],
        "cabinClass": cabin_class,
        "suppliers": [supplier],
        "enableDeduplication": True,
        "searchType": search_type,
    }
    return payload


def summarize_response(resp: dict) -> dict:
    """
    A API às vezes retorna:
      { success, data: {...} }
    e às vezes direto:
      { requestId, flightGroups, ... }
    Então normalizamos assim:
    """
    root = resp.get("data") if isinstance(resp.get("data"), dict) else resp

    request_id = root.get("requestId") or resp.get("requestId")
    groups = root.get("flightGroups") if isinstance(root.get("flightGroups"), list) else []

    offers_total = 0
    providers_count: dict[str, int] = {}

    for g in groups:
        if not isinstance(g, dict):
            continue
        offers = g.get("offers") if isinstance(g.get("offers"), list) else []
        offers_total += len(offers)
        for off in offers:
            if not isinstance(off, dict):
                continue
            prov = off.get("provider") or off.get("providerId") or off.get("source") or "N/D"
            prov = str(prov)
            providers_count[prov] = providers_count.get(prov, 0) + 1

    return {
        "requestId": request_id,
        "groups_len": len(groups),
        "offers_total": offers_total,
        "providers_count": providers_count,
        "top_level_keys": list(resp.keys()) if isinstance(resp, dict) else [],
        "root_keys": list(root.keys()) if isinstance(root, dict) else [],
    }


def dump_json(resp: dict, dump_dir: str, name: str) -> Path:
    out_dir = Path(dump_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}_{int(time.time())}.json"
    path.write_text(json.dumps(resp, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main():
    load_dotenv(override=False)

    base_url = (_env("MOBLIX_BASE_URL", "https://app.apidevoos.dev/api/v1") or "").rstrip("/")
    api_key = _env("MOBLIX_API_KEY")
    timeout = int(_env("MOBLIX_TIMEOUT", "60") or "60")
    dump_dir = _env("MOBLIX_DEBUG_DIR", "debug_dumps") or "debug_dumps"

    if not api_key:
        raise SystemExit("ERRO: MOBLIX_API_KEY não está definido no .env")

    parser = argparse.ArgumentParser(description="Moblix raw one-supplier test (milhas)")
    parser.add_argument("--origin", default="BSB", help="IATA origem (ex: BSB)")
    parser.add_argument("--destination", default="GRU", help="IATA destino (ex: GRU)")
    parser.add_argument("--date", default=None, help="Data ida YYYY-MM-DD (default: hoje+45d)")
    parser.add_argument("--adults", type=int, default=1, help="Adultos")
    parser.add_argument("--cabin", default="economy", help="economy|premium_economy|business|first")
    parser.add_argument("--supplier", default="latam", help="Fornecedor único (ex: latam)")
    parser.add_argument("--search-type", default="milhas", help="milhas|pagante|'' (default milhas)")
    args = parser.parse_args()

    dep_date = args.date
    if not dep_date:
        dep_date = (date.today() + timedelta(days=45)).isoformat()

    payload = build_payload_one_way(
        origin=args.origin.upper(),
        destination=args.destination.upper(),
        departure_date=dep_date,
        adults=args.adults,
        cabin_class=args.cabin,
        supplier=args.supplier.lower(),
        search_type=args.search_type.lower().strip(),
    )

    url = f"{base_url}/flights/search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    print("=== MOBLIX RAW TEST ===")
    print("URL:", url)
    print("Payload:", json.dumps(payload, ensure_ascii=False))
    print("Timeout:", timeout)

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except Exception as e:
        raise SystemExit(f"ERRO de rede ao chamar Moblix: {e}")

    print("HTTP:", r.status_code)

    # Se vier erro HTTP, mostre corpo e saia (ajuda muito no diagnóstico)
    if r.status_code >= 400:
        body = (r.text or "")[:3000]
        print("Body (até 3000 chars):")
        print(body)
        # 429 é quota/créditos conforme doc
        if r.status_code == 429:
            print("\nDICA: HTTP 429 = quota mensal/créditos excedidos.")
        raise SystemExit(f"Falha HTTP {r.status_code}")

    # tenta JSON
    try:
        data = r.json()
    except Exception:
        text = (r.text or "")[:3000]
        raise SystemExit(f"Resposta não-JSON. Trecho:\n{text}")

    # dump
    dump_name = f"moblix_raw_{args.origin.upper()}_{args.destination.upper()}_{dep_date}_{args.supplier.lower()}_{args.search_type.lower() or 'default'}"
    dump_path = dump_json(data, dump_dir=dump_dir, name=dump_name)

    summary = summarize_response(data)
    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"{k}: {v}")

    print("\nDump salvo em:", str(dump_path))


if __name__ == "__main__":
    main()
