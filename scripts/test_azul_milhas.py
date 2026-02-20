import os
import json
import time
from pathlib import Path

import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
DUMPS = ROOT / "debug_dumps"
DUMPS.mkdir(exist_ok=True)


def dump_json(data: dict, name: str) -> Path:
    f = DUMPS / f"{name}_{int(time.time())}.json"
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return f


def call_api(payload: dict) -> dict:
    load_dotenv(ROOT / ".env")

    base_url = os.getenv("MOBLIX_BASE_URL", "https://app.apidevoos.dev/api/v1").rstrip("/")
    api_key = os.getenv("MOBLIX_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MOBLIX_API_KEY não encontrada no .env")

    url = f"{base_url}/flights/search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    r = requests.post(url, json=payload, headers=headers, timeout=90)
    r.raise_for_status()
    return r.json()


def summarize_points(raw: dict):
    groups = raw.get("flightGroups") or []
    offers_total = 0
    offers_with_points = 0

    sample_with_points = None
    sample_without_points = None

    for g in groups:
        for off in (g.get("offers") or []):
            offers_total += 1
            price = off.get("price") or {}
            pts = (price.get("pointsInfo") or {}).get("totalPoints")

            if isinstance(pts, (int, float)) and pts > 0:
                offers_with_points += 1
                if sample_with_points is None:
                    sample_with_points = {
                        "providerId": off.get("providerId"),
                        "totalPoints": pts,
                        "program": (price.get("pointsInfo") or {}).get("program"),
                        "pointsType": (price.get("pointsInfo") or {}).get("pointsType"),
                        "total_brl": price.get("total"),
                        "taxes": price.get("taxes"),
                        "bookingUrl": (off.get("booking") or {}).get("bookingUrl"),
                    }
            else:
                if sample_without_points is None:
                    sample_without_points = {
                        "providerId": off.get("providerId"),
                        "price_total_brl": price.get("total"),
                        "taxes": price.get("taxes"),
                        "has_pointsInfo": isinstance(price.get("pointsInfo"), dict),
                        "bookingUrl": (off.get("booking") or {}).get("bookingUrl"),
                    }

    print("\n=== SUMMARY AZUL MILHAS ===")
    print("groups:", len(groups))
    print("offers_total:", offers_total)
    print("offers_with_points:", offers_with_points)

    print("\n--- SAMPLE WITH POINTS ---")
    print("FOUND" if sample_with_points else "NOT FOUND")
    if sample_with_points:
        print(json.dumps(sample_with_points, ensure_ascii=False, indent=2))

    print("\n--- SAMPLE WITHOUT POINTS ---")
    print("FOUND" if sample_without_points else "NOT FOUND")
    if sample_without_points:
        print(json.dumps(sample_without_points, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    # Troque por uma rota bem Azul (ex.: VCP<->BSB, VCP<->REC, CNF<->VCP etc.)
    origin = "VCP"
    destination = "BSB"
    dep_date = "2026-03-30"

    payload = {
        "type": "one_way",
        "slices": [
            {"origin": origin, "destination": destination, "departureDate": dep_date}
        ],
        "passengers": [{"type": "adult", "count": 1}],
        "cabinClass": "economy",
        "enableDeduplication": False,
        "suppliers": ["azul"],
        "searchType": "milhas",
    }

    print("Calling Azul (milhas)...")
    raw = call_api(payload)
    dump = dump_json(raw, f"AZUL_{origin}_{destination}_{dep_date}")
    print("Dump saved:", dump)

    summarize_points(raw)
