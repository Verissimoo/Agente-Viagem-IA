"""Captura respostas reais da API para inspeção offline.

Uso:
    python tests/snapshots/capture.py quote-for-date GRU SSA --date 2026-06-15
    python tests/snapshots/capture.py quote-for-date LIS MAD --date 2026-06-15 --return 2026-06-22
    python tests/snapshots/capture.py explore GRU SSA --date 2026-06-15 --flex 3
    python tests/snapshots/capture.py search GRU SSA --date 2026-06-15
    python tests/snapshots/capture.py all     # roda presets

Os arquivos vão pra tests/snapshots/fixtures/ — gitignorados.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date as _date
from pathlib import Path

# Adicionar raiz do projeto ao sys.path quando rodado standalone.
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi.testclient import TestClient

from backend.app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURES.mkdir(exist_ok=True)


def _save(name: str, body: dict) -> Path:
    out = FIXTURES / f"{name}.json"
    out.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def capture_quote_for_date(
    client: TestClient,
    origin: str,
    destination: str,
    date: str,
    return_date: str | None = None,
    adults: int = 1,
) -> Path:
    payload = {"origin": origin, "destination": destination, "date": date, "adults": adults}
    if return_date:
        payload["return_date"] = return_date
    print(f"  POST /smart-quote/quote-for-date  {origin}->{destination}  {date}" + (f" / {return_date}" if return_date else ""))
    r = client.post("/api/v1/smart-quote/quote-for-date", json=payload)
    r.raise_for_status()
    suffix = f"__{origin}-{destination}__{date}" + (f"__rt_{return_date}" if return_date else "")
    return _save(f"quote-for-date{suffix}", r.json())


def capture_explore(
    client: TestClient,
    origin: str,
    destination: str,
    date: str,
    flex_days: int = 4,
    adults: int = 1,
) -> Path:
    payload = {
        "origin": origin, "destination": destination,
        "date_start": date, "flex_days": flex_days, "adults": adults,
    }
    print(f"  POST /smart-quote/explore  {origin}->{destination}  {date} ±{flex_days}d")
    r = client.post("/api/v1/smart-quote/explore", json=payload)
    r.raise_for_status()
    return _save(f"explore__{origin}-{destination}__{date}__flex{flex_days}", r.json())


def capture_search(
    client: TestClient,
    origin: str,
    destination: str,
    date: str,
    return_date: str | None = None,
) -> Path:
    payload = {"origin": origin, "destination": destination, "date_start": date, "top_n": 20}
    if return_date:
        payload["date_return"] = return_date
    print(f"  POST /search  {origin}->{destination}  {date}")
    r = client.post("/api/v1/search", json=payload)
    r.raise_for_status()
    return _save(f"search__{origin}-{destination}__{date}", r.json())


def capture_all(client: TestClient) -> list[Path]:
    """Presets de rotas que cobrem os principais casos."""
    today = _date.today()
    iso15 = (today.replace(day=1) if today.day > 25 else today).isoformat()
    # Datas distantes pra garantir inventário
    from datetime import timedelta
    d15 = (today + timedelta(days=15)).isoformat()
    d22 = (today + timedelta(days=22)).isoformat()

    paths = []
    cases = [
        ("quote-for-date", "GRU", "SSA", d15, None),    # doméstica BR
        ("quote-for-date", "GRU", "SSA", d15, d22),     # doméstica BR roundtrip
        ("quote-for-date", "GRU", "MIA", d15, None),    # internacional GRU-MIA
        ("quote-for-date", "BSB", "LIS", d15, None),    # internacional via partner
        ("explore", "GRU", "SSA", d15, None),
        ("explore", "BSB", "LIS", d15, None),
        ("search", "GRU", "SSA", d15, None),
    ]
    for kind, o, d, dt, ret in cases:
        try:
            if kind == "quote-for-date":
                paths.append(capture_quote_for_date(client, o, d, dt, return_date=ret))
            elif kind == "explore":
                paths.append(capture_explore(client, o, d, dt))
            elif kind == "search":
                paths.append(capture_search(client, o, d, dt))
        except Exception as e:
            print(f"  ✗ falhou: {e}")
    return paths


def main():
    parser = argparse.ArgumentParser(description="Captura snapshots da API")
    parser.add_argument("route", choices=["quote-for-date", "explore", "search", "all"])
    parser.add_argument("origin", nargs="?", help="IATA origem (não usado em 'all')")
    parser.add_argument("destination", nargs="?", help="IATA destino")
    parser.add_argument("--date", help="Data ida YYYY-MM-DD")
    parser.add_argument("--return", dest="return_date", help="Data volta YYYY-MM-DD")
    parser.add_argument("--flex", type=int, default=4, help="Flex days (explore)")
    parser.add_argument("--adults", type=int, default=1)
    args = parser.parse_args()

    client = TestClient(app)
    print(f"\nCapturando snapshots para fixtures/\n")

    if args.route == "all":
        paths = capture_all(client)
    else:
        if not (args.origin and args.destination and args.date):
            print("Erro: origin, destination e --date são obrigatórios")
            sys.exit(1)
        if args.route == "quote-for-date":
            paths = [capture_quote_for_date(client, args.origin, args.destination, args.date, args.return_date, args.adults)]
        elif args.route == "explore":
            paths = [capture_explore(client, args.origin, args.destination, args.date, args.flex, args.adults)]
        else:  # search
            paths = [capture_search(client, args.origin, args.destination, args.date, args.return_date)]

    print(f"\nGerados {len(paths)} arquivo(s):")
    for p in paths:
        size_kb = p.stat().st_size / 1024
        print(f"  {p.relative_to(Path.cwd())}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
