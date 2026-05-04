"""
scripts/mcp_auth_diagnose.py
============================
Diagnostico cientifico de autenticacao para o servidor MCP do Award Travel Finder.

Testa variações de header e corpo de requisicao para determinar:
  - Qual formato de autenticacao o servidor aceita (X-API-Key vs Bearer)
  - Qual metodo JSON-RPC o servidor reconhece (listTools vs tools/list vs initialize)
  - Se o protocolo exige SSE ou JSON simples

Resultados são salvos em debug_dumps/auth_diagnostic_result.json
"""

import json
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

API_KEY = "cdc00e7246735ad6980749a48a4540042b6a16abdeb53a73c666258baaec2fde"
BASE_URL = "https://mcp.awardtravelfinder.com"
MCP_URL  = f"{BASE_URL}/mcp"

DEBUG_DUMPS_DIR = Path(__file__).parent.parent / "debug_dumps"
MCP_CLIENT_PATH = Path(__file__).parent.parent / "mcp_client.py"
LOG_FILE        = DEBUG_DUMPS_DIR / "auth_diagnostic_result.json"

TIMEOUT = 20

# Corpos JSON-RPC a testar (metodos variam por versao do protocolo MCP)
RPC_BODIES = {
    "listTools":      {"jsonrpc": "2.0", "id": 1, "method": "listTools",    "params": {}},
    "tools/list":     {"jsonrpc": "2.0", "id": 1, "method": "tools/list",   "params": {}},
    "initialize":     {"jsonrpc": "2.0", "id": 1, "method": "initialize",   "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "diagnose-script", "version": "1.0"},
    }},
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sep(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _fmt_headers(headers) -> dict:
    return dict(headers)


def _run_request(label: str, headers: dict, body: dict) -> dict:
    """Executa um POST e retorna um dict com o diagnostico."""
    print(f"\n  [{label}]")
    print(f"    Auth header : {list(k for k in headers if 'auth' in k.lower() or 'api' in k.lower() or 'x-' in k.lower())}")
    print(f"    Metodo RPC  : {body.get('method')}")

    result = {
        "label":           label,
        "url":             MCP_URL,
        "request_headers": headers,
        "request_body":    body,
        "timestamp":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    try:
        resp = requests.post(MCP_URL, headers=headers, json=body, timeout=TIMEOUT)
        result["status_code"]       = resp.status_code
        result["response_headers"]  = _fmt_headers(resp.headers)
        result["www_authenticate"]  = resp.headers.get("WWW-Authenticate", "")

        try:
            result["response_body"] = resp.json()
        except Exception:
            result["response_body"] = resp.text[:1000]

        status_str = f"HTTP {resp.status_code}"
        if resp.ok:
            status_str += " [SUCESSO]"
            result["success"] = True
        else:
            status_str += " [FALHA]"
            result["success"] = False

        print(f"    Status      : {status_str}")
        if not resp.ok:
            www_auth = resp.headers.get("WWW-Authenticate", "")
            if www_auth:
                print(f"    WWW-Auth    : {www_auth}")
            try:
                body_preview = json.dumps(resp.json())[:200]
            except Exception:
                body_preview = resp.text[:200]
            print(f"    Resposta    : {body_preview}")

    except requests.exceptions.Timeout:
        result["success"]      = False
        result["status_code"]  = None
        result["error"]        = f"Timeout apos {TIMEOUT}s"
        print(f"    TIMEOUT apos {TIMEOUT}s")

    except requests.exceptions.ConnectionError as exc:
        result["success"]     = False
        result["status_code"] = None
        result["error"]       = f"Erro de conexao: {exc}"
        print(f"    ERRO DE CONEXAO: {exc}")

    return result


# ---------------------------------------------------------------------------
# Suite de testes
# ---------------------------------------------------------------------------

def run_diagnostics() -> list[dict]:
    results = []

    # ---- Variantes de autenticacao ----------------------------------------
    auth_variants = {
        "X-API-Key":             {"X-API-Key": API_KEY},
        "Bearer":                {"Authorization": f"Bearer {API_KEY}"},
        "X-API-Key+Bearer":      {"X-API-Key": API_KEY, "Authorization": f"Bearer {API_KEY}"},
        "token (lowercase)":     {"token": API_KEY},
        "api-key (lowercase)":   {"api-key": API_KEY},
    }

    base_content_headers = {
        "Content-Type": "application/json",
        "Accept":       "application/json, text/event-stream",
    }

    _sep("FASE 1 — Descoberta de metodo de autenticacao (metodo: tools/list)")
    rpc_body = RPC_BODIES["tools/list"]

    for auth_name, auth_hdrs in auth_variants.items():
        headers = {**base_content_headers, **auth_hdrs}
        label   = f"AUTH={auth_name} | RPC=tools/list"
        res     = _run_request(label, headers, rpc_body)
        results.append(res)
        if res.get("success"):
            print(f"\n  *** SUCESSO com '{auth_name}' + tools/list! Encerrando fase 1. ***")
            return results

    _sep("FASE 2 — Variantes de metodo RPC (usando Bearer token)")
    bearer_hdrs = {**base_content_headers, "Authorization": f"Bearer {API_KEY}"}

    for rpc_name, rpc_body in RPC_BODIES.items():
        label = f"AUTH=Bearer | RPC={rpc_name}"
        res   = _run_request(label, bearer_hdrs, rpc_body)
        results.append(res)
        if res.get("success"):
            print(f"\n  *** SUCESSO com Bearer + {rpc_name}! ***")
            return results

    _sep("FASE 3 — Sem Accept SSE (JSON puro apenas)")
    plain_hdrs = {
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }
    res = _run_request("AUTH=Bearer | Accept=JSON only | RPC=tools/list", plain_hdrs, RPC_BODIES["tools/list"])
    results.append(res)

    _sep("FASE 4 — Endpoints alternativos")
    alt_endpoints = [
        f"{BASE_URL}/api/mcp",
        f"{BASE_URL}/v1/mcp",
        f"{BASE_URL}/mcp/v1",
    ]
    for endpoint in alt_endpoints:
        label = f"URL={endpoint} | AUTH=Bearer"
        result = {
            "label":    label,
            "url":      endpoint,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        print(f"\n  [{label}]")
        try:
            resp = requests.post(
                endpoint,
                headers={**base_content_headers, "Authorization": f"Bearer {API_KEY}"},
                json=RPC_BODIES["tools/list"],
                timeout=TIMEOUT,
            )
            result["status_code"]      = resp.status_code
            result["response_headers"] = _fmt_headers(resp.headers)
            result["success"]          = resp.ok
            try:
                result["response_body"] = resp.json()
            except Exception:
                result["response_body"] = resp.text[:500]
            print(f"    Status : HTTP {resp.status_code}")
        except Exception as exc:
            result["success"]     = False
            result["status_code"] = None
            result["error"]       = str(exc)
            print(f"    Erro   : {exc}")
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Salvar log
# ---------------------------------------------------------------------------

def save_log(results: list[dict]) -> None:
    DEBUG_DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print(f"\n[diagnose] Log completo salvo em: {LOG_FILE}")


# ---------------------------------------------------------------------------
# Analise e patch automatico do mcp_client.py
# ---------------------------------------------------------------------------

def analyse_and_patch(results: list[dict]) -> None:
    _sep("VEREDITO FINAL")

    successful = [r for r in results if r.get("success")]

    if successful:
        winner = successful[0]
        req_headers = winner.get("request_headers", {})

        # Descobre o padrao de auth vencedor
        if "Authorization" in req_headers and "X-API-Key" not in req_headers:
            auth_pattern = "Bearer"
            header_snippet = f'"Authorization": f"Bearer {{MCP_BEARER_TOKEN}}"'
        elif "X-API-Key" in req_headers and "Authorization" not in req_headers:
            auth_pattern = "X-API-Key"
            header_snippet = f'"X-API-Key": MCP_BEARER_TOKEN'
        else:
            auth_pattern = "Ambos (X-API-Key + Bearer)"
            header_snippet = f'"X-API-Key": MCP_BEARER_TOKEN, "Authorization": f"Bearer {{MCP_BEARER_TOKEN}}"'

        rpc_method = winner.get("request_body", {}).get("method", "tools/list")

        print(f"  FORMATO VENCEDOR  : {auth_pattern}")
        print(f"  METODO RPC        : {rpc_method}")
        print(f"  STATUS            : HTTP {winner.get('status_code')}")
        print(f"\n  Atualizando mcp_client.py automaticamente...")

        _patch_mcp_client(auth_pattern, header_snippet, rpc_method)

    else:
        # Nenhum teste teve sucesso — relatorio tecnico completo
        print("  NENHUM teste retornou sucesso (HTTP 2xx).")
        print("\n  Analise tecnica dos desafios de autenticacao (WWW-Authenticate):")

        seen_www = set()
        for r in results:
            www = r.get("www_authenticate", "")
            if www and www not in seen_www:
                seen_www.add(www)
                print(f"\n    Header WWW-Authenticate:")
                print(f"      {www}")

        print("\n  Interpretacao:")
        for r in results:
            www = r.get("www_authenticate", "")
            if "realm" in www.lower() and "oauth" in www.lower():
                if "resource_metadata" in www.lower():
                    print("    -> O servidor exige fluxo OAuth 2.0 completo (Authorization Code).")
                    print("       A chave fornecida NAO eh um access_token OAuth valido.")
                    print("       Ela pode ser um client_secret ou uma API key de outro sistema.")
                    print("\n    Proximos passos sugeridos:")
                    print("      1. Acesse o portal: https://awardtravelfinder.com")
                    print("      2. Faca login e va em configuracoes de API/Developer")
                    print("      3. Procure por 'Access Token', 'API Token' ou 'Bearer Token'")
                    print("      4. Se so houver 'API Key', tente usar como Bearer token")
                    print("      5. Atualize MCP_BEARER_TOKEN no .env com o token correto")
                break

        resp_bodies = {r["label"]: r.get("response_body") for r in results if r.get("status_code")}
        print("\n  Corpos de resposta por teste:")
        for label, body in list(resp_bodies.items())[:4]:
            print(f"    [{label[:50]}] -> {str(body)[:150]}")


def _patch_mcp_client(auth_pattern: str, header_snippet: str, rpc_method: str) -> None:
    """Atualiza o _build_headers() e o method no mcp_client.py com o padrao vencedor."""
    if not MCP_CLIENT_PATH.exists():
        print(f"  [AVISO] mcp_client.py nao encontrado em {MCP_CLIENT_PATH}. Patch ignorado.")
        return

    content = MCP_CLIENT_PATH.read_text(encoding="utf-8")

    # Patch do _build_headers
    old_return = 'return {\n        "Authorization": f"Bearer {MCP_BEARER_TOKEN}"'
    if auth_pattern == "Bearer" and old_return in content:
        print("  [PATCH] _build_headers() ja usa Bearer — sem alteracoes necessarias.")
    elif auth_pattern == "X-API-Key":
        new_return = '    return {\n        "X-API-Key": MCP_BEARER_TOKEN'
        content = content.replace(
            '    return {\n        "Authorization": f"Bearer {MCP_BEARER_TOKEN}"',
            new_return,
        )
        MCP_CLIENT_PATH.write_text(content, encoding="utf-8")
        print("  [PATCH] mcp_client.py atualizado para X-API-Key.")

    # Patch do metodo RPC
    old_method = '"method": "tools/call"'
    if old_method not in content:
        print(f"  [INFO] Metodo RPC nao alterado (padrao tools/call nao encontrado no arquivo).")
    else:
        print(f"  [INFO] Metodo de invocacao (tools/call) mantido — e o correto para busca.")

    print("  [PATCH] Concluido.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Award Travel Finder — Diagnostico de Autenticacao MCP")
    print(f"Chave testada (primeiros 12 chars): {API_KEY[:12]}...")
    print(f"URL alvo: {MCP_URL}")

    results = run_diagnostics()
    save_log(results)
    analyse_and_patch(results)

    # Codigo de saida: 0 se algum teste teve sucesso, 1 caso contrario
    any_success = any(r.get("success") for r in results)
    sys.exit(0 if any_success else 1)
