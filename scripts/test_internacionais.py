"""
test_internacionais.py
======================
Teste completo das companhias internacionais: TAP, IBERIA, AMERICAN, INTERLINE.
Roda diretamente: python scripts/test_internacionais.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta

# Adiciona raiz do projeto ao path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

from miles_app.buscamilhas_client import (
    BuscaMilhasClient, build_payload,
    COMPANHIAS_INTERNACIONAIS, _env,
    BUSCAMILHAS_ENDPOINT,
)
from miles_app.buscamilhas_offer_parser import extract_rows_from_buscamilhas

# ──────────────────────────────────────────────
# Config de cores para terminal
# ──────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def ok(msg):  print(f"  [OK] {msg}")
def err(msg): print(f"  [ERRO] {msg}")
def info(msg):print(f"  [INFO] {msg}")
def warn(msg):print(f"  [AVISO] {msg}")
def title(msg):print(f"\n{'='*60}\n{msg}\n{'='*60}")


def build_client() -> BuscaMilhasClient:
    chave = _env("BUSCAMILHAS_CHAVE", "") or ""
    senha = _env("BUSCAMILHAS_SENHA", "") or ""
    endpoint = _env("BUSCAMILHAS_ENDPOINT", BUSCAMILHAS_ENDPOINT) or BUSCAMILHAS_ENDPOINT
    return BuscaMilhasClient(chave=chave, senha=senha, endpoint=endpoint,
                              connect_timeout=15, read_timeout=90, max_attempts=1)


# ──────────────────────────────────────────────
# Itinerários de teste
# ──────────────────────────────────────────────
# Data futura (~2 meses à frente)
hoje = date.today()
data_teste = (hoje + timedelta(days=70)).strftime("%d/%m/%Y")

ITINERARIOS = [
    # (companhia, origem, destino, descricao)
    ("TAP",       "GRU", "LIS", "GRU→LIS (rota principal TAP)"),
    ("TAP",       "GRU", "MAD", "GRU→MAD via LIS (escala)"),
    ("TAP",       "VCP", "LIS", "VCP→LIS (Campinas)"),
    ("IBERIA",    "GRU", "MAD", "GRU→MAD (rota IBERIA)"),
    ("IBERIA",    "GRU", "LIS", "GRU→LIS via MAD"),
    ("AMERICAN",  "GRU", "MIA", "GRU→MIA (rota AA)"),
    ("AMERICAN",  "GRU", "JFK", "GRU→JFK via MIA"),
    ("AMERICAN",  "GRU", "DFW", "GRU→DFW hub AA"),
    ("INTERLINE", "GRU", "JFK", "GRU→JFK via PTY/BOG"),
    ("INTERLINE", "GRU", "MIA", "GRU→MIA (interline)"),
    ("INTERLINE", "GRU", "MAD", "GRU→MAD (interline Europa)"),
]


def test_one(client, companhia, origem, destino, descricao, data):
    """Testa um itinerário e retorna resultado resumido."""
    print(f"\n  [{companhia}] {origem}→{destino} ({descricao}) | Data: {data}")

    payload = build_payload(
        companhia=companhia,
        origem=origem,
        destino=destino,
        data_ida=data,
        internacional=True,
        chave=client.chave,
        senha=client.senha,
    )

    t0 = time.time()
    try:
        raw = client.search(payload)
    except Exception as e:
        err(f"Erro de rede/API: {e}")
        return {"status": "ERRO_REDE", "companhia": companhia, "rota": f"{origem}-{destino}", "erro": str(e)}
    elapsed = time.time() - t0

    status = raw.get("Status") or {}
    is_erro = status.get("Erro", False)
    is_suc  = status.get("Sucesso", False)
    alertas = status.get("Alerta") or []

    if is_erro:
        err(f"API retornou Erro=true | Alertas: {alertas} | {elapsed:.1f}s")
        return {"status": "API_ERRO", "companhia": companhia, "rota": f"{origem}-{destino}",
                "alertas": alertas, "tempo": elapsed}

    trechos = raw.get("Trechos") or {}
    total_voos = sum(len((v.get("Voos") or [])) for v in trechos.values())

    if total_voos == 0:
        warn(f"Sucesso mas 0 voos encontrados | {elapsed:.1f}s")
        return {"status": "SEM_VOOS", "companhia": companhia, "rota": f"{origem}-{destino}",
                "tempo": elapsed}

    # Tenta parsear os rows
    try:
        rows = extract_rows_from_buscamilhas(raw, companhia=companhia, trip_type="OW")
        n_rows = len(rows)
        if rows:
            r0 = rows[0]
            milhas = r0.get("Milhas")
            taxas  = r0.get("Taxas (R$)")
            bagagem = r0.get("Bagagem")
            nr_voo  = r0.get("NumeroVoo")
            ok(f"{n_rows} rows parseadas | Melhor: {milhas} milhas + R${taxas} | Bagagem: {bagagem} | Voo: {nr_voo} | {elapsed:.1f}s")
            return {"status": "OK", "companhia": companhia, "rota": f"{origem}-{destino}",
                    "rows": n_rows, "milhas": milhas, "taxas": taxas, "bagagem": bagagem,
                    "tempo": elapsed}
        else:
            warn(f"{total_voos} voos na API mas 0 rows após parse | {elapsed:.1f}s")
            return {"status": "PARSE_VAZIO", "companhia": companhia, "rota": f"{origem}-{destino}",
                    "tempo": elapsed}
    except Exception as e:
        err(f"Erro no parser: {e}")
        return {"status": "PARSE_ERRO", "companhia": companhia, "rota": f"{origem}-{destino}",
                "erro": str(e), "tempo": elapsed}


def main():
    title("CHECKUP — COMPANHIAS INTERNACIONAIS (TAP / IBERIA / AMERICAN / INTERLINE)")

    # Verificar credenciais
    print("\n[1] Verificando credenciais...")
    chave = _env("BUSCAMILHAS_CHAVE", "") or ""
    senha = _env("BUSCAMILHAS_SENHA", "") or ""
    if not chave or not senha:
        err("BUSCAMILHAS_CHAVE ou BUSCAMILHAS_SENHA não configuradas no .env!")
        sys.exit(1)
    ok(f"Credenciais encontradas (chave: {chave[:8]}...)")

    print(f"\n[2] Data de teste: {data_teste}")

    try:
        client = build_client()
        ok("Client instanciado com sucesso")
    except Exception as e:
        err(f"Falha ao criar client: {e}")
        sys.exit(1)

    # ─── Testes por companhia ───
    results = []
    for companhia in COMPANHIAS_INTERNACIONAIS:
        title(f"COMPANHIA: {companhia}")
        rota_count = 0
        for (comp, orig, dest, desc) in ITINERARIOS:
            if comp != companhia:
                continue
            r = test_one(client, companhia, orig, dest, desc, data_teste)
            results.append(r)
            rota_count += 1
            time.sleep(0.5)  # evitar rate limit
        print(f"\n  Testados {rota_count} itinerários para {companhia}")

    # ─── Resumo ───
    title("RESUMO FINAL")
    by_comp: dict = {}
    for r in results:
        c = r["companhia"]
        if c not in by_comp:
            by_comp[c] = {"OK": 0, "SEM_VOOS": 0, "API_ERRO": 0, "ERRO_REDE": 0, "PARSE_ERRO": 0, "PARSE_VAZIO": 0}
        by_comp[c][r["status"]] = by_comp[c].get(r["status"], 0) + 1

    print(f"\n{'Companhia':<12} {'OK':<6} {'SEM_VOOS':<10} {'API_ERRO':<10} {'REDE_ERR':<10} {'PARSE_ERR'}")
    print("-" * 60)
    for comp, counts in by_comp.items():
        line = (f"{comp:<12} {counts.get('OK',0):<6} {counts.get('SEM_VOOS',0):<10} "
                f"{counts.get('API_ERRO',0):<10} {counts.get('ERRO_REDE',0):<10} "
                f"{counts.get('PARSE_ERRO',0) + counts.get('PARSE_VAZIO',0)}")
        oks = counts.get('OK', 0)
        total = sum(counts.values())
        if oks == total:
            print(f"{GREEN}{line}{RESET}")
        elif oks == 0:
            print(f"{RED}{line}{RESET}")
        else:
            print(f"{YELLOW}{line}{RESET}")

    # ─── Itinerários recomendados para usar na UI ───
    title("ITINERÁRIOS CONFIRMADOS (para testar na UI)")
    good = [r for r in results if r["status"] == "OK"]
    if good:
        for r in good:
            print(f"  [OK] [{r['companhia']}] {r['rota']} | "
                  f"{r.get('milhas','?')} milhas + R${r.get('taxas','?')} | "
                  f"Bagagem: {r.get('bagagem','?')}")
    else:
        warn("Nenhum itinerario com resultado OK encontrado.")

    bad = [r for r in results if r["status"] != "OK"]
    if bad:
        print(f"\n  Itinerarios com problema:")
        for r in bad:
            print(f"  [ERRO] [{r['companhia']}] {r['rota']} -> {r['status']}"
                  + (f" | {r.get('alertas','')}" if r.get('alertas') else "")
                  + (f" | {r.get('erro','')}" if r.get('erro') else ""))

    print("\n")
    return results


if __name__ == "__main__":
    main()
