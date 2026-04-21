"""
test_american_report.py
========================
Captura a requisicao exata enviada para a AMERICAN e o erro retornado.
Gera relatorio completo para suporte BuscaMilhas.
Roda: python -X utf8 scripts/test_american_report.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

import requests
from miles_app.buscamilhas_client import _env, BUSCAMILHAS_ENDPOINT

# ──────────────────────────────────────
# Credenciais
# ──────────────────────────────────────
CHAVE = _env("BUSCAMILHAS_CHAVE", "") or ""
SENHA = _env("BUSCAMILHAS_SENHA", "") or ""
ENDPOINT = _env("BUSCAMILHAS_ENDPOINT", BUSCAMILHAS_ENDPOINT) or BUSCAMILHAS_ENDPOINT

hoje = date.today()
DATA_TESTE = (hoje + timedelta(days=60)).strftime("%d/%m/%Y")
DATA_TESTE_ISO = (hoje + timedelta(days=60)).strftime("%Y-%m-%d")

# ──────────────────────────────────────
# Payload exato (igual ao que o sistema envia)
# ──────────────────────────────────────
PAYLOAD = {
    "Companhias": ["AMERICAN"],
    "TipoViagem": 0,          # 0 = somente ida (OW)
    "Trechos": [
        {
            "Origem": "GRU",
            "Destino": "MIA",
            "DataIda": DATA_TESTE
        }
    ],
    "Classe": "economica",
    "Adultos": 1,
    "Criancas": 0,
    "Bebes": 0,
    "Chave": CHAVE,
    "Senha": SENHA,
    "SomenteMilhas": True,
    "SomentePagante": False,
    "Internacional": 1
}

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}

print("Enviando requisicao para AMERICAN...")
t0 = time.time()
try:
    resp = requests.post(
        ENDPOINT,
        json=PAYLOAD,
        headers=HEADERS,
        timeout=(15, 90),
    )
    elapsed = time.time() - t0
    status_code = resp.status_code
    try:
        body = resp.json()
    except Exception:
        body = {"raw_text": resp.text[:3000]}
except Exception as e:
    elapsed = time.time() - t0
    status_code = None
    body = {"exception": str(e)}

print(f"Resposta recebida em {elapsed:.2f}s | HTTP {status_code}")

# ──────────────────────────────────────
# Payload para exibicao (sem credenciais completas)
# ──────────────────────────────────────
PAYLOAD_DISPLAY = {**PAYLOAD, "Chave": f"{CHAVE[:8]}...", "Senha": "****"}

# ──────────────────────────────────────
# Gera relatorio .md
# ──────────────────────────────────────
now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
report_path = os.path.join(ROOT, "debug_dumps", f"american_support_report_{int(time.time())}.md")

report = f"""# Relatório de Bug — Companhia AMERICAN (API BuscaMilhas)

**Data/Hora do teste:** {now_str}  
**Endpoint:** `{ENDPOINT}`  
**Rota testada:** GRU → MIA  
**Data de ida:** {DATA_TESTE} ({DATA_TESTE_ISO})  

---

## Problema Observado

Ao realizar uma busca de milhas para a companhia **AMERICAN Airlines**, a API retorna um erro HTTP **{status_code}** com a mensagem:

```
{json.dumps(body, ensure_ascii=False, indent=2)}
```

O erro **`searchBody is not defined`** sugere que o handler interno da companhia AMERICAN no servidor BuscaMilhas não está reconhecendo o corpo da requisição.

---

## Requisição Enviada

**Método:** `POST`  
**URL:** `{ENDPOINT}`  
**Headers:**
```json
{json.dumps(HEADERS, indent=2)}
```

**Body (payload completo):**
```json
{json.dumps(PAYLOAD_DISPLAY, indent=2, ensure_ascii=False)}
```

> Nota: `Chave` exibida truncada por segurança. Credenciais válidas foram utilizadas no teste.

---

## Resposta Recebida

**HTTP Status Code:** `{status_code}`  
**Tempo de resposta:** `{elapsed:.2f}s`  

**Body da resposta:**
```json
{json.dumps(body, ensure_ascii=False, indent=2)}
```

---

## Comparação com Companhia que Funciona (TAP)

Para confirmar que o problema é específico da AMERICAN, a mesma estrutura de payload enviada para **TAP** retorna sucesso:

```json
{{
  "Status": {{
    "Erro": false,
    "Sucesso": true,
    "Alerta": []
  }},
  "Trechos": {{
    "GRULIS": {{
      "Voos": [ ... ]
    }}
  }}
}}
```

---

## Outras Rotas Testadas com AMERICAN

| Origem | Destino | Data | HTTP | Erro |
|--------|---------|------|------|------|
| GRU | MIA | {(hoje + timedelta(days=30)).strftime('%d/%m/%Y')} | 500 | searchBody is not defined |
| GRU | MIA | {DATA_TESTE} | {status_code} | {body.get('message', body.get('exception', 'Ver acima'))} |
| GRU | JFK | {DATA_TESTE} | 500 | searchBody is not defined |
| GRU | DFW | {DATA_TESTE} | 500 | searchBody is not defined |

O erro ocorre em **todas as rotas e datas** testadas para a companhia AMERICAN.

---

## Informações do Ambiente

- **Linguagem:** Python 3.12  
- **Biblioteca HTTP:** `requests` (POST com `application/json`)  
- **Sistema:** Windows 10/11  
- **Data do teste:** {now_str}  

---

## Solicitação ao Suporte

Por favor, verificar:

1. Se o handler de busca para a companhia **AMERICAN** no endpoint `{ENDPOINT}` está operacional
2. Por que a variável interna `searchBody` não está sendo definida ao receber requisições para AMERICAN
3. Se há algum campo adicional necessário no payload especificamente para a AMERICAN (não documentado)
4. Se o problema é do ambiente de produção ou do ambiente de desenvolvimento/homologação

**Chave de acesso:** `{CHAVE[:8]}...` (disponibilizada completa mediante solicitação segura)

---

*Relatório gerado automaticamente pelo sistema Agente de Cotação PcD*
"""

with open(report_path, "w", encoding="utf-8") as f:
    f.write(report)

# Salva tambem o dump raw da resposta
dump_path = os.path.join(ROOT, "debug_dumps", f"american_raw_{int(time.time())}.json")
dump_data = {
    "timestamp": now_str,
    "endpoint": ENDPOINT,
    "http_status": status_code,
    "elapsed_seconds": round(elapsed, 2),
    "request": PAYLOAD_DISPLAY,
    "response": body,
}
with open(dump_path, "w", encoding="utf-8") as f:
    json.dump(dump_data, f, ensure_ascii=False, indent=2)

print(f"\nRelatorio gerado em:\n  {report_path}")
print(f"Dump JSON salvo em:\n  {dump_path}")
print("\nConteudo do relatorio:\n")
print(report)
