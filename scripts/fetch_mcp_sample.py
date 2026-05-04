"""
scripts/fetch_mcp_sample.py
===========================
Faz uma requisicao ao servidor MCP do Award Travel Finder para a tool
search_all_airlines e salva o JSON completo em debug_dumps/.

Estrategia dupla:
  1. Tenta o endpoint MCP diretamente com X-API-Key header (conforme solicitado).
  2. Se o MCP retornar 401/403 (exige OAuth interativo), aciona fallback REST:
     chama /api/v1/<airline>/availability para TODAS as airlines suportadas
     e consolida os resultados em uma estrutura equivalente ao search_all_airlines.

O arquivo salvo: debug_dumps/mcp_all_airlines_GRU_JFK_sample.json
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_KEY  = "cdc00e7246735ad6980749a48a4540042b6a16abdeb53a73c666258baaec2fde"
MCP_URL  = "https://mcp.awardtravelfinder.com/mcp"
REST_BASE = "https://awardtravelfinder.com/api/v1"

ORIGIN      = "GRU"
DESTINATION = "JFK"
DATE        = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")

OUTPUT_DIR  = Path(__file__).parent.parent / "debug_dumps"
OUTPUT_FILE = OUTPUT_DIR / "mcp_all_airlines_GRU_JFK_sample.json"

TIMEOUT = 30

# Airlines suportadas pela plataforma (documentacao + testes previos)
ALL_AIRLINES = [
    "british_airways",
    "qatar_airways",
    "cathay_pacific",
    "american_airlines",
    "united_airlines",
    "air_canada",
    "delta_airlines",
    "singapore_airlines",
    "emirates",
    "lufthansa",
    "turkish_airlines",
    "virgin_atlantic",
    "alaska_airlines",
    "ana",
    "japan_airlines",
    "korean_air",
    "avianca",
]

# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------

MCP_HEADERS = {
    "X-API-Key":    API_KEY,
    "Content-Type": "application/json",
    "Accept":       "application/json, text/event-stream",
}

REST_HEADERS = {
    "X-API-Key":    API_KEY,
    "Content-Type": "application/json",
    "Accept":       "application/json",
}

# ---------------------------------------------------------------------------
# Tentativa 1: MCP direto
# ---------------------------------------------------------------------------

def try_mcp_direct() -> dict | None:
    """
    Tenta POST no endpoint MCP com X-API-Key.
    Retorna o payload JSON se HTTP 2xx, None caso contrario.
    """
    body = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "tools/call",
        "params":  {
            "name":      "search_all_airlines",
            "arguments": {
                "origin":      ORIGIN,
                "destination": DESTINATION,
                "date":        DATE,
            },
        },
    }

    print(f"[MCP] POST {MCP_URL}")
    print(f"      Header: X-API-Key: {API_KEY[:12]}...")
    print(f"      Rota  : {ORIGIN} -> {DESTINATION} | {DATE}")

    try:
        resp = requests.post(MCP_URL, headers=MCP_HEADERS, json=body, timeout=TIMEOUT)
    except requests.exceptions.RequestException as exc:
        print(f"[MCP] Erro de rede: {exc}")
        return None

    print(f"[MCP] Status: HTTP {resp.status_code}")

    if resp.ok:
        try:
            return resp.json()
        except Exception:
            # Tenta parsear SSE
            for line in resp.text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    fragment = line[5:].strip()
                    if fragment and fragment != "[DONE]":
                        try:
                            return json.loads(fragment)
                        except Exception:
                            pass
            print(f"[MCP] Resposta nao parseavel: {resp.text[:300]}")
            return None
    else:
        print(f"[MCP] Falha: {resp.text[:200]}")
        return None


# ---------------------------------------------------------------------------
# Tentativa 2: REST API fallback (all airlines)
# ---------------------------------------------------------------------------

def fetch_all_via_rest() -> dict:
    """
    Chama /api/v1/<airline>/availability para cada airline suportada
    e consolida os resultados em uma estrutura equivalente ao search_all_airlines.
    """
    print(f"\n[REST] Iniciando busca consolidada para {len(ALL_AIRLINES)} airlines...")
    print(f"[REST] Rota: {ORIGIN} -> {DESTINATION} | {DATE}\n")

    results     = {}
    successful  = 0
    failed      = 0
    usage_info  = None

    for airline in ALL_AIRLINES:
        url = f"{REST_BASE}/{airline}/availability"
        params = {
            "departure_code": ORIGIN,
            "arrival_code":   DESTINATION,
            "date":           DATE,
        }

        try:
            resp = requests.get(
                url,
                headers=REST_HEADERS,
                params=params,
                timeout=TIMEOUT,
            )
        except requests.exceptions.Timeout:
            print(f"  [TIMEOUT] {airline}")
            results[airline] = {"error": "timeout"}
            failed += 1
            continue
        except requests.exceptions.RequestException as exc:
            print(f"  [ERRO]    {airline}: {exc}")
            results[airline] = {"error": str(exc)}
            failed += 1
            continue

        if resp.ok:
            try:
                data = resp.json()
                results[airline] = data.get("data", data)
                if usage_info is None:
                    usage_info = data.get("usage")
                avail = data.get("data", {}).get("availability", {})
                data_avail = avail.get("data_available", False)
                status_str = "disponivel" if data_avail else "sem disponibilidade"
                print(f"  [OK]      {airline:<25} -> {status_str}")
                successful += 1
            except Exception as exc:
                print(f"  [PARSE]   {airline}: {exc}")
                results[airline] = {"error": f"parse_error: {exc}"}
                failed += 1
        else:
            body_preview = resp.text[:120]
            print(f"  [HTTP {resp.status_code}] {airline:<25} -> {body_preview}")
            results[airline] = {
                "http_error": resp.status_code,
                "detail":     resp.text[:300],
            }
            failed += 1

    print(f"\n[REST] Concluido: {successful} sucesso | {failed} falha")

    # Estrutura consolidada equivalente ao search_all_airlines do MCP
    consolidated = {
        "tool":        "search_all_airlines",
        "source":      "rest_api_fallback",
        "note":        (
            "Dados obtidos via REST API /api/v1/<airline>/availability "
            "como fallback ao MCP SSE (que exige OAuth interativo). "
            "Estrutura equivalente ao retorno do MCP search_all_airlines."
        ),
        "query": {
            "origin":      ORIGIN,
            "destination": DESTINATION,
            "date":        DATE,
        },
        "summary": {
            "total_airlines_queried": len(ALL_AIRLINES),
            "successful":             successful,
            "failed":                 failed,
            "airlines_with_data":     [
                a for a, d in results.items()
                if isinstance(d, dict)
                and d.get("availability", {}).get("data_available") is True
            ],
        },
        "usage":    usage_info,
        "airlines": results,
    }

    return consolidated


# ---------------------------------------------------------------------------
# Salvar resultado
# ---------------------------------------------------------------------------

def save_result(payload: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    size_kb = OUTPUT_FILE.stat().st_size / 1024
    print(f"\nJSON gerado com sucesso em debug_dumps!")
    print(f"  Arquivo : {OUTPUT_FILE.name}")
    print(f"  Tamanho : {size_kb:.1f} KB")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("  Award Travel Finder — fetch_mcp_sample.py")
    print(f"  Rota : {ORIGIN} -> {DESTINATION}")
    print(f"  Data : {DATE}  (hoje + 90 dias)")
    print("=" * 60)

    # --- Tentativa 1: MCP direto ---
    print("\n[PASSO 1] Tentando endpoint MCP com X-API-Key...")
    payload = try_mcp_direct()

    # --- Tentativa 2: REST fallback (se MCP falhou) ---
    if payload is None:
        print("\n[PASSO 2] MCP nao disponivel. Acionando REST fallback para todas as airlines...")
        payload = fetch_all_via_rest()
    else:
        print("\n[PASSO 1] MCP respondeu com sucesso!")

    # --- Salvar ---
    save_result(payload)

    # Preview estrutural
    print("\n--- Estrutura do JSON gerado ---")
    top_keys = list(payload.keys())
    print(f"  Chaves raiz : {top_keys}")
    if "airlines" in payload:
        airline_keys = list(payload["airlines"].keys())
        print(f"  Airlines    : {airline_keys}")
    if "summary" in payload:
        print(f"  Resumo      : {payload['summary']}")
