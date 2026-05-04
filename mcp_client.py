"""
mcp_client.py
=============
Cliente para o Award Travel Finder.

Conforme a documentacao oficial (dashboard /api-access):

  MCP (SSE):
    URL: https://mcp.awardtravelfinder.com/mcp?api_key=<CHAVE>
    A chave vai como QUERY PARAMETER, nao como header.

  REST API:
    URL: https://awardtravelfinder.com/api/v1/<airline>/availability
    Header: X-API-Key: <CHAVE>

Este modulo implementa AMBOS os modos:
  - call_mcp_search_all_airlines()   -> via MCP (SSE/JSON-RPC)
  - call_rest_availability()         -> via REST API direta

Sistema de dump automatico em debug_dumps/ apos toda resposta bem-sucedida.
Modo fixture via use_fixture_path=None para testes offline.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuracoes
# ---------------------------------------------------------------------------

API_KEY = (
    os.getenv("MCP_BEARER_TOKEN")
    or os.getenv("ATF_API_KEY")
    or "cdc00e7246735ad6980749a48a4540042b6a16abdeb53a73c666258baaec2fde"
)

# MCP: chave como query parameter (conforme Quick Start do dashboard)
MCP_BASE_URL  = "https://mcp.awardtravelfinder.com/mcp"
MCP_URL       = f"{MCP_BASE_URL}?api_key={API_KEY}"

# REST API: chave como X-API-Key header
REST_BASE_URL = "https://awardtravelfinder.com/api/v1"

DEBUG_DUMPS_DIR  = Path(__file__).parent / "debug_dumps"
REQUEST_TIMEOUT  = 120


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _ensure_debug_dir() -> None:
    DEBUG_DUMPS_DIR.mkdir(parents=True, exist_ok=True)


def _dump_payload(tag: str, payload: dict) -> Path:
    """Salva payload em debug_dumps/<tag>_<timestamp>.json."""
    _ensure_debug_dir()
    timestamp = int(time.time())
    filename  = f"{tag}_{timestamp}.json"
    filepath  = DEBUG_DUMPS_DIR / filename
    with open(filepath, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"[mcp_client] [DUMP] {filepath}")
    return filepath


def _parse_sse_response(raw_text: str) -> dict:
    """
    Extrai JSON de resposta SSE (linhas 'data: {...}').
    Fallback: tenta parsear o texto completo como JSON puro.
    """
    candidates = []
    for line in raw_text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            fragment = line[5:].strip()
            if fragment and fragment != "[DONE]":
                try:
                    candidates.append(json.loads(fragment))
                except json.JSONDecodeError:
                    pass

    if candidates:
        return candidates[-1]

    # fallback JSON puro
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        raise ValueError(
            f"Nao foi possivel parsear a resposta MCP.\n"
            f"Primeiros 500 chars:\n{raw_text[:500]}"
        )


def _rest_headers() -> dict:
    return {
        "X-API-Key":    API_KEY,
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }


# ---------------------------------------------------------------------------
# Modo MCP (SSE / JSON-RPC 2.0)
# ---------------------------------------------------------------------------

def call_mcp_search_all_airlines(
    origin: str,
    destination: str,
    date: str,
    use_fixture_path: str | None = None,
) -> dict:
    """
    Invoca a tool search_all_airlines via protocolo MCP (SSE).

    Autenticacao: chave como query parameter ?api_key=...
    (conforme Quick Start > Claude Desktop config do dashboard)

    Parametros
    ----------
    origin          : IATA de origem (ex: "GRU")
    destination     : IATA de destino (ex: "JFK")
    date            : YYYY-MM-DD
    use_fixture_path: se fornecido, le arquivo local ao inves de fazer request

    Retorna
    -------
    dict com o payload completo do MCP
    """

    # Modo fixture
    if use_fixture_path is not None:
        fixture = Path(use_fixture_path)
        if not fixture.exists():
            raise FileNotFoundError(f"Fixture nao encontrado: {fixture}")
        print(f"[mcp_client] [FIXTURE] Lendo: {fixture}")
        with open(fixture, "r", encoding="utf-8") as fh:
            return json.load(fh)

    # JSON-RPC body para tools/call
    rpc_body = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "tools/call",
        "params":  {
            "name":      "search_all_airlines",
            "arguments": {
                "origin":      origin.upper(),
                "destination": destination.upper(),
                "date":        date,
            },
        },
    }

    print(f"[mcp_client] [MCP] {origin.upper()} -> {destination.upper()} | {date}")
    print(f"[mcp_client]       URL: {MCP_URL}")

    try:
        resp = requests.post(
            MCP_URL,
            headers={
                "Content-Type": "application/json",
                "Accept":       "application/json, text/event-stream",
                "X-API-Key":    API_KEY,
            },
            json=rpc_body,
            timeout=REQUEST_TIMEOUT,
            stream=True,
        )
    except requests.exceptions.Timeout:
        raise TimeoutError(f"Timeout ({REQUEST_TIMEOUT}s) — aumente REQUEST_TIMEOUT.")
    except requests.exceptions.ConnectionError as exc:
        raise ConnectionError(f"Falha de conexao com MCP: {exc}") from exc

    print(f"[mcp_client] [HTTP] Status: {resp.status_code}")

    if not resp.ok:
        snippet = resp.text[:800]
        raise RuntimeError(
            f"Erro HTTP {resp.status_code} no MCP.\n"
            f"Resposta: {snippet}"
        )

    raw_text = resp.text
    print(f"[mcp_client] [RECV] {len(raw_text.encode())} bytes")

    payload = _parse_sse_response(raw_text)

    tag = f"mcp_raw_{origin.upper()}_{destination.upper()}_{date}"
    _dump_payload(tag, payload)

    return payload


# ---------------------------------------------------------------------------
# Modo REST API direta (alternativa ao MCP)
# ---------------------------------------------------------------------------

def call_rest_availability(
    airline: str,
    departure: str,
    arrival: str,
    date: str,
    use_fixture_path: str | None = None,
) -> dict:
    """
    Consulta disponibilidade via REST API do Award Travel Finder.

    Autenticacao: header X-API-Key
    Endpoint: GET /api/v1/{airline}/availability?departure=&arrival=&date=

    Airlines suportadas (conforme documentacao): british_airways, qatar_airways,
    cathay_pacific, air_canada, united_airlines, etc.

    Parametros
    ----------
    airline   : slug da companhia (ex: "british_airways")
    departure : IATA de origem  (ex: "GRU")
    arrival   : IATA de destino (ex: "JFK")
    date      : YYYY-MM-DD
    """

    if use_fixture_path is not None:
        fixture = Path(use_fixture_path)
        if not fixture.exists():
            raise FileNotFoundError(f"Fixture nao encontrado: {fixture}")
        print(f"[mcp_client] [FIXTURE] Lendo: {fixture}")
        with open(fixture, "r", encoding="utf-8") as fh:
            return json.load(fh)

    url = f"{REST_BASE_URL}/{airline}/availability"
    # Parametros corretos conforme documentacao Award Travel Finder:
    # departure_code e arrival_code (nao departure/arrival)
    params = {
        "departure_code": departure.upper(),
        "arrival_code":   arrival.upper(),
        "date":           date,
    }

    print(f"[mcp_client] [REST] {airline} | {departure.upper()} -> {arrival.upper()} | {date}")
    print(f"[mcp_client]        URL: {url}")

    try:
        resp = requests.get(
            url,
            headers=_rest_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        raise TimeoutError(f"Timeout ({REQUEST_TIMEOUT}s).")
    except requests.exceptions.ConnectionError as exc:
        raise ConnectionError(f"Falha de conexao REST: {exc}") from exc

    print(f"[mcp_client] [HTTP] Status: {resp.status_code}")

    if not resp.ok:
        raise RuntimeError(
            f"Erro HTTP {resp.status_code} na REST API.\n"
            f"Resposta: {resp.text[:800]}"
        )

    try:
        payload = resp.json()
    except Exception:
        raise ValueError(f"Resposta nao eh JSON valido: {resp.text[:500]}")

    tag = f"rest_raw_{airline}_{departure.upper()}_{arrival.upper()}_{date}"
    _dump_payload(tag, payload)

    return payload


# ---------------------------------------------------------------------------
# Bloco de teste real
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    today       = datetime.now()
    search_date = (today + timedelta(days=90)).strftime("%Y-%m-%d")

    ORIGIN      = "GRU"
    DESTINATION = "JFK"

    print("=" * 60)
    print(f"  Teste Award Travel Finder: {ORIGIN} -> {DESTINATION}")
    print(f"  Data de busca            : {search_date}")
    print("=" * 60)

    success = False

    # --- Tentativa 1: MCP via SSE (query param api_key) ---
    print("\n[TENTATIVA 1] Protocolo MCP (SSE + api_key query param)")
    try:
        result = call_mcp_search_all_airlines(
            origin=ORIGIN,
            destination=DESTINATION,
            date=search_date,
        )
        print("\n[OK] MCP respondeu com sucesso!")
        print(f"     Chaves raiz: {list(result.keys())}")
        preview = json.dumps(result, ensure_ascii=False, indent=2)
        if len(preview) > 2000:
            print(preview[:2000])
            print("... [truncado]")
        else:
            print(preview)
        success = True

    except Exception as exc:
        print(f"[FALHA MCP] {exc}")

    # --- Tentativa 2: REST API direta (X-API-Key header) ---
    if not success:
        print("\n[TENTATIVA 2] REST API direta (X-API-Key + departure_code/arrival_code)")
        # Airlines confirmadas via diagnostico (british_airways = HTTP 200 validado)
        airlines_to_try = [
            "british_airways",   # confirmado HTTP 200
            "qatar_airways",
            "cathay_pacific",
            "american_airlines",
            "united_airlines",
        ]

        for airline in airlines_to_try:
            try:
                print(f"\n  Testando airline: {airline}")
                result = call_rest_availability(
                    airline=airline,
                    departure=ORIGIN,
                    arrival=DESTINATION,
                    date=search_date,
                )
                print(f"\n[OK] REST API ({airline}) respondeu com sucesso!")
                print(f"     Chaves raiz: {list(result.keys())}")
                preview = json.dumps(result, ensure_ascii=False, indent=2)
                print(preview[:2000])
                success = True
                break
            except Exception as exc:
                print(f"  [FALHA {airline}] {exc}")

    if not success:
        print("\n[ERRO] Nenhuma tentativa teve sucesso.")
        print("       Verifique se o plano Free inclui acesso API ou faca upgrade.")
        sys.exit(1)
    else:
        print("\n[CONCLUIDO] Dump salvo em debug_dumps/")
