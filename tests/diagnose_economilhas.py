"""
diagnose_economilhas.py
-----------------------
Script de diagnóstico isolado para entender quais programas Economilhas
estão de fato disponíveis para uma rota/data e como vem a resposta crua
de cada um. Não toca nos módulos do sistema — só faz HTTP direto e
grava arquivos em tests/responses_economilhas/.

Uso:
    python tests/diagnose_economilhas.py

Cobertura:
  - quota antes da bateria
  - 3 rotas × 7 programas = 21 chamadas individuais (1 programa por request)
  - quota depois da bateria
  - throttling de 2s entre chamadas (rate limit-friendly)

Saída:
  - <rota>_<programa>.json   — payload + status + body cru
  - _quota_before.json / _quota_after.json
  - _SUMMARY.md              — tabela compacta para varredura visual
  - _PARSER_NOTES.md         — caminhos de campos por programa que retornou
  - _DIAGNOSTICO.md          — relatório priorizado para próxima fase
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Carrega .env sem importar nenhum módulo do projeto.
try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except Exception:
    pass


ENDPOINT_SEARCH = "https://api.economilha.com/flights/search"
ENDPOINT_QUOTA  = "https://api.economilha.com/quota"
ACCEPT_VERSION  = "application/vnd.economilha.v1+json"

OUT_DIR = Path(__file__).parent / "responses_economilhas"
OUT_DIR.mkdir(parents=True, exist_ok=True)

THROTTLE_S = 2.0   # gap entre chamadas — pedido explícito do task


TEST_ROUTES: List[Dict[str, str]] = [
    {"origin": "BSB", "destination": "LIS", "date": "2026-06-15", "name": "internacional_BSB_LIS"},
    {"origin": "GRU", "destination": "MIA", "date": "2026-06-15", "name": "internacional_GRU_MIA"},
    {"origin": "GRU", "destination": "REC", "date": "2026-06-15", "name": "domestica_GRU_REC"},
]

PROGRAMS_TO_TEST: List[str] = [
    "SMILES", "LATAM", "AZUL", "AZUL_INTERLINE", "COPA", "IBERIA", "BRITISH",
]


def _api_key() -> str:
    key = os.getenv("ECONOMILHAS_API_KEY", "").strip()
    if not key:
        print("ERRO: ECONOMILHAS_API_KEY não configurada no .env.")
        sys.exit(1)
    return key


def _mask(key: str) -> str:
    if not key:
        return ""
    return f"{key[:8]}…{key[-4:]}" if len(key) > 14 else "***"


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _http_request(
    url: str,
    method: str,
    api_key: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = 100,
) -> Tuple[int, float, Any, str]:
    """Faz a requisição e devolve (status, elapsed_ms, body_parsed, raw_text).

    body_parsed é o JSON quando possível, senão None. raw_text sempre tem
    o conteúdo bruto (útil quando vem html de proxy ou similar)."""
    data_bytes = None
    if payload is not None:
        data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        method=method,
        headers={
            "x-api-key": api_key,
            "Accept": ACCEPT_VERSION,
            "Content-Type": "application/json",
        },
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            elapsed = (time.perf_counter() - t0) * 1000.0
            try:
                body = json.loads(raw)
            except Exception:
                body = None
            return r.status, elapsed, body, raw
    except urllib.error.HTTPError as e:
        elapsed = (time.perf_counter() - t0) * 1000.0
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        try:
            body = json.loads(raw) if raw else None
        except Exception:
            body = None
        return e.code, elapsed, body, raw
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return -1, elapsed, None, f"{type(e).__name__}: {str(e)}"


def _build_payload(program: str, route: Dict[str, str]) -> Dict[str, Any]:
    return {
        "airlineLoyalty": [program.upper()],
        "priceType":  "MILES",
        "tripType":   "ONE_WAY",
        "cabinType":  "ECONOMY",
        "origin":     route["origin"].upper(),
        "destination":route["destination"].upper(),
        "departureDate": route["date"],
        "passengers": {"adults": 1, "children": 0, "infants": 0},
    }


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _quota_call(api_key: str, suffix: str) -> Dict[str, Any]:
    status, elapsed, body, raw = _http_request(ENDPOINT_QUOTA, "GET", api_key)
    out = {
        "timestamp": _now_iso(),
        "request": {
            "endpoint": ENDPOINT_QUOTA,
            "method": "GET",
            "headers": {"x-api-key": _mask(api_key), "Accept": ACCEPT_VERSION},
        },
        "response": {
            "status_code": status,
            "elapsed_ms": round(elapsed, 1),
            "body": body if body is not None else raw,
        },
    }
    _save_json(OUT_DIR / f"_quota_{suffix}.json", out)
    return out


def _correlation_id(body: Any, raw: str) -> Optional[str]:
    if isinstance(body, dict):
        for k in ("correlationId", "correlation_id", "requestId", "traceId"):
            if k in body and isinstance(body[k], str):
                return body[k]
    return None


def _summarise_data_field(data: Any) -> Tuple[str, int, str]:
    """Devolve (kind, size_bytes, top_keys_csv) do `data` por programa."""
    if data is None:
        return "null", 0, ""
    raw = json.dumps(data, ensure_ascii=False)
    size = len(raw)
    if isinstance(data, dict):
        keys = list(data.keys())
        return "dict", size, ", ".join(keys[:10])
    if isinstance(data, list):
        return f"list[{len(data)}]", size, ""
    return type(data).__name__, size, ""


def _classify_failure(status: int, body: Any, raw: str) -> str:
    """Heurística de classificação para o relatório."""
    if status == -1:
        return f"network_error: {raw[:120]}"
    if status >= 500:
        return f"http_{status}_server"
    if status == 402:
        return "quota_exceeded"
    if status == 401:
        return "auth_error"
    if status == 429:
        return "rate_limited"
    if status >= 400:
        return f"http_{status}_client"

    # 2xx — checa estrutura
    if not isinstance(body, dict):
        return "non_json_2xx"
    results = body.get("results")
    if not isinstance(results, list) or not results:
        return "empty_results"
    item = results[0]
    if not isinstance(item, dict):
        return "malformed_result_item"
    if not item.get("success"):
        err = item.get("error") or {}
        msg = err.get("message") if isinstance(err, dict) else str(err)
        prov = err.get("providerStatusCode") if isinstance(err, dict) else None
        return f"provider_failure(prov_status={prov}): {str(msg)[:120]}"
    if item.get("data") in (None, {}, []):
        return "success_but_data_empty"
    return "ok"


def _run_one_call(
    api_key: str, program: str, route: Dict[str, str],
) -> Dict[str, Any]:
    payload = _build_payload(program, route)
    status, elapsed, body, raw = _http_request(ENDPOINT_SEARCH, "POST", api_key, payload=payload)
    cid = _correlation_id(body, raw)
    diagnosis = _classify_failure(status, body, raw)

    record = {
        "timestamp": _now_iso(),
        "program": program,
        "route":   route,
        "request": {
            "endpoint": ENDPOINT_SEARCH,
            "method":   "POST",
            "headers":  {"x-api-key": _mask(api_key),
                         "Accept":    ACCEPT_VERSION,
                         "Content-Type": "application/json"},
            "payload":  payload,
        },
        "response": {
            "status_code": status,
            "elapsed_ms":  round(elapsed, 1),
            "correlationId": cid,
            "body": body if body is not None else raw,
        },
        "diagnosis": diagnosis,
    }
    fname = f"{route['name']}_{program}.json"
    _save_json(OUT_DIR / fname, record)
    return record


# ──────────────────────────────────────────────────────────────────
# Relatórios
# ──────────────────────────────────────────────────────────────────
def _format_summary(records: List[Dict[str, Any]]) -> str:
    lines = []
    lines.append("# _SUMMARY.md — diagnose_economilhas\n")
    lines.append(f"Gerado em: {_now_iso()}\n")
    lines.append("| Programa | Rota | HTTP | success | data? | erro/observação | data bytes |")
    lines.append("|---|---|---:|:---:|:---:|---|---:|")
    for rec in records:
        program = rec["program"]
        route = rec["route"]
        rote_lbl = f"{route['origin']}->{route['destination']} ({route['date']})"
        status = rec["response"]["status_code"]
        body = rec["response"]["body"]
        success_flag = ""
        data_kind = ""
        data_size = 0
        notes = rec["diagnosis"]
        if isinstance(body, dict):
            results = body.get("results") or []
            if results and isinstance(results[0], dict):
                it = results[0]
                success_flag = "✓" if it.get("success") else "✗"
                kind, size, _ = _summarise_data_field(it.get("data"))
                data_kind = "sim" if (it.get("data") not in (None, {}, [])) else "—"
                data_size = size
        lines.append(
            f"| `{program}` | {rote_lbl} | {status} | {success_flag or '—'} | {data_kind or '—'} "
            f"| {notes} | {data_size} |"
        )
    return "\n".join(lines) + "\n"


def _walk_paths(node: Any, prefix: str, depth: int, out: List[str], max_depth: int = 4) -> None:
    if depth > max_depth:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                out.append(f"{path}: {type(v).__name__}({len(v)})")
                _walk_paths(v if isinstance(v, dict) else (v[0] if v else {}), path, depth + 1, out, max_depth)
            else:
                out.append(f"{path}: {type(v).__name__}")
    elif isinstance(node, list) and node:
        # toma o primeiro item como amostra
        _walk_paths(node[0], prefix + "[0]", depth + 1, out, max_depth)


def _format_parser_notes(records: List[Dict[str, Any]]) -> str:
    lines = []
    lines.append("# _PARSER_NOTES.md — caminhos do `data` por programa que retornou voo\n")
    lines.append(f"Gerado em: {_now_iso()}\n")
    seen_programs: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        program = rec["program"]
        body = rec["response"]["body"]
        if not isinstance(body, dict):
            continue
        results = body.get("results") or []
        if not results or not isinstance(results[0], dict):
            continue
        it = results[0]
        if not it.get("success"):
            continue
        data = it.get("data")
        if data in (None, {}, []):
            continue
        if program not in seen_programs:
            seen_programs[program] = {"data": data, "route": rec["route"]}
    if not seen_programs:
        lines.append("Nenhum programa retornou `success=true` com `data` preenchido.\n")
        return "\n".join(lines) + "\n"

    for program, info in seen_programs.items():
        route = info["route"]
        data = info["data"]
        lines.append(f"## {program} — amostra de `{route['name']}`\n")
        lines.append("Top-level keys:")
        if isinstance(data, dict):
            lines.append("```")
            lines.append(", ".join(data.keys()))
            lines.append("```")
            lines.append("Hierarquia (até profundidade 4):")
            paths: List[str] = []
            _walk_paths(data, "", 0, paths, max_depth=4)
            lines.append("```")
            for p in paths[:60]:
                lines.append(p)
            lines.append("```")
        else:
            lines.append(f"`data` é {type(data).__name__}, conteúdo: {str(data)[:300]}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _format_diagnostico(
    records: List[Dict[str, Any]],
    quota_before: Dict[str, Any],
    quota_after: Dict[str, Any],
) -> str:
    lines = []
    lines.append("# _DIAGNOSTICO.md — Economilhas\n")
    lines.append(f"Gerado em: {_now_iso()}\n")

    # Agrupa por programa
    per_program: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        per_program.setdefault(r["program"], []).append(r)

    classified: Dict[str, List[str]] = {
        "ok": [], "provider": [], "parser": [], "request": [],
    }
    for prog, recs in per_program.items():
        diags = [r["diagnosis"] for r in recs]
        if any(d == "ok" for d in diags):
            classified["ok"].append(prog)
        elif any("provider_failure" in d or "http_5" in d for d in diags):
            classified["provider"].append(prog)
        elif any("success_but_data_empty" in d or "empty_results" in d for d in diags):
            classified["parser"].append(prog)
        else:
            classified["request"].append(prog)

    lines.append("## Resumo executivo\n")
    lines.append(f"- ✅ **Funcionando 100%**: {', '.join(classified['ok']) or '—'}")
    lines.append(f"- 🔥 **Provider parceiro indisponível** (não temos como corrigir): {', '.join(classified['provider']) or '—'}")
    lines.append(f"- 🛠 **Resposta vem mas parser falha** (corrigir parser): {', '.join(classified['parser']) or '—'}")
    lines.append(f"- ❓ **Outros problemas (rede/auth/payload)**: {', '.join(classified['request']) or '—'}")
    lines.append("")

    lines.append("## Tabela detalhada por programa × rota\n")
    lines.append("| Programa | Rota | HTTP | success | Diagnóstico |")
    lines.append("|---|---|---:|:---:|---|")
    for r in records:
        rt = r["route"]
        body = r["response"]["body"]
        success_flag = "—"
        if isinstance(body, dict):
            results = body.get("results") or []
            if results and isinstance(results[0], dict):
                success_flag = "✓" if results[0].get("success") else "✗"
        lines.append(
            f"| `{r['program']}` | {rt['origin']}->{rt['destination']} | {r['response']['status_code']} | "
            f"{success_flag} | {r['diagnosis']} |"
        )
    lines.append("")

    lines.append("## Quota — antes e depois\n")
    def _q_str(q):
        body = q.get("response", {}).get("body")
        if isinstance(body, dict):
            return json.dumps({k: body[k] for k in body if k in (
                "limit", "consumed", "remaining", "available", "used",
                "monthlyLimit", "usageByCompany", "byCompany"
            )}, ensure_ascii=False)
        return str(body)[:300]
    lines.append(f"- **antes**: `{_q_str(quota_before)}` (HTTP {quota_before.get('response',{}).get('status_code')})")
    lines.append(f"- **depois**: `{_q_str(quota_after)}` (HTTP {quota_after.get('response',{}).get('status_code')})")
    lines.append("")

    lines.append("## Lista priorizada de correções\n")
    if classified["parser"]:
        lines.append("### P1 — corrigir parser (data chegou mas não foi extraído)")
        for p in classified["parser"]:
            lines.append(f"- `{p}`: ver `_PARSER_NOTES.md` e ajustar `economilhas_offer_parser._parse_{p.lower()}_data`.")
    if classified["provider"]:
        lines.append("\n### P2 — provider parceiro (aguardar / abrir ticket Economilhas)")
        for p in classified["provider"]:
            lines.append(f"- `{p}`: instabilidade do upstream — não é nosso bug. Reverificar em algumas horas.")
    if classified["request"]:
        lines.append("\n### P3 — investigar request/cliente")
        for p in classified["request"]:
            lines.append(f"- `{p}`: ver detalhes do registro em `tests/responses_economilhas/`.")
    if classified["ok"]:
        lines.append("\n### Sem ação")
        for p in classified["ok"]:
            lines.append(f"- `{p}`: já volta com voos e parser extrai. Manter monitoramento.")
    return "\n".join(lines) + "\n"


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────
def main():
    api_key = _api_key()
    print(f"[diagnose] API key: {_mask(api_key)}")
    print(f"[diagnose] Saída: {OUT_DIR}")

    print("[diagnose] GET /quota antes da bateria…")
    quota_before = _quota_call(api_key, "before")
    print(f"  -> status {quota_before['response']['status_code']}")
    time.sleep(THROTTLE_S)

    records: List[Dict[str, Any]] = []
    total = len(TEST_ROUTES) * len(PROGRAMS_TO_TEST)
    idx = 0
    for route in TEST_ROUTES:
        for program in PROGRAMS_TO_TEST:
            idx += 1
            print(f"[diagnose] {idx:02d}/{total} {program:14s} {route['origin']}->{route['destination']} ({route['date']})…", end="", flush=True)
            rec = _run_one_call(api_key, program, route)
            print(f" status {rec['response']['status_code']}, {rec['response']['elapsed_ms']}ms — {rec['diagnosis']}")
            records.append(rec)
            time.sleep(THROTTLE_S)

    print("[diagnose] GET /quota depois da bateria…")
    quota_after = _quota_call(api_key, "after")
    print(f"  -> status {quota_after['response']['status_code']}")

    # Relatórios
    (OUT_DIR / "_SUMMARY.md").write_text(_format_summary(records), encoding="utf-8")
    (OUT_DIR / "_PARSER_NOTES.md").write_text(_format_parser_notes(records), encoding="utf-8")
    (OUT_DIR / "_DIAGNOSTICO.md").write_text(_format_diagnostico(records, quota_before, quota_after), encoding="utf-8")
    print(f"[diagnose] Pronto. Relatórios em {OUT_DIR}")


if __name__ == "__main__":
    main()
