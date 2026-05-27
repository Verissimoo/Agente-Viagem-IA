# Skiplagged Provider

Provider complementar que cobre lacunas dos provedores tradicionais (Kayak, BuscaMilhas, MCP Award, Economilhas):

- **Hidden city**: bilhetes onde o destino oficial é uma cidade além da cidade real do passageiro; ele desembarca na conexão. Frequentemente 30–50% mais baratos que voos diretos para a cidade real.
- **Split cash**: 2 bilhetes separados em dinheiro saindo mais barato que 1 direto.

Diferença vs. provedores existentes: o **Skiplagged não tem chave paga**. Não substitui ninguém; complementa.

## Estrutura

```
backend/app/providers/skiplagged/
├── adapter.py    SkiplaggedAdapter — implementa BaseSearchAdapter
├── client.py     fetch_via_httpx (REST) → fetch_via_playwright (scraping)
└── parser.py     extract_offers — payload bruto → list[UnifiedOffer]
```

## Estratégia de coleta (cascata)

1. **`fetch_via_httpx`** — Tenta 3 endpoints candidatos da API REST/MCP do Skiplagged. Stateless, rápido (<1s). Se algum responder com JSON contendo `itineraries`/`flights`/`trips`/`results`, usa.
2. **`fetch_via_playwright`** — Fallback com browser headless. Abre `https://skiplagged.com/flights/{from}/{to}/{date}`, intercepta XHR via `page.on("response", ...)`, salva o payload mais denso. ~5–10s.
3. **Cache** — Resultado bruto é cacheado por 10min em `backend.app.infrastructure.cache` com chave `skiplagged:{md5(from+to+date)}`. Reutilizado em buscas repetidas do mesmo dia.

Todos os payloads brutos são gravados em `debug/skiplagged/raw_<from>_<to>_<date>_<suffix>_<ts>.json` para inspeção. Útil quando o Skiplagged muda a estrutura — basta abrir o JSON cru e ajustar as chaves no parser.

## Parser — detecção de cenário

Em `parser.py`, função `_detect_scenario(item, segments, requested_destination)`:

| Condição | `scenario` | `layover_city` | `risk_notes` |
|---|---|---|---|
| `item.hidden_city=true` ou destino-final ≠ destino-pedido | `HIDDEN_CITY` | IATA da cidade pedida | "hidden-city: sem bagagem despachada" |
| múltiplos segmentos sem flag de hidden | `SPLIT_CASH` | — | — |
| segmento único | `CASH_DIRECT` | — | — |

A `UnifiedOffer` resultante usa `SourceType.SKIPLAGGED` e converte preço via `fx_rates` (USD/EUR → BRL).

## Feature flag

`SKIPLAGGED_ENABLED=0` desliga o provider sem deploy. Útil para corte rápido se o site mudar e os parsers começarem a vazar erro. O adapter já absorve qualquer exceção e devolve `[]`, então no pior caso a busca volta a se basear apenas nos provedores antigos.

## Como testar localmente

### Unit tests (sem rede)

```powershell
pytest pcd/tests/test_skiplagged_parser.py pcd/tests/test_skiplagged_adapter.py
```

6 testes do parser + 5 do adapter (sucesso, erro, cache, flag desligada, payload vazio).

### Smoke real (com rede)

```powershell
python -c "from skiplagged_client import fetch_skiplagged; r = fetch_skiplagged('GIG','SSA','2026-07-15'); print('Result:', 'None' if r is None else f'OK ({len(r)} keys)')"
```

Saída esperada (se o site responder e o parser reconhecer as chaves):
```
OK (N keys)
```

Se voltar `None`, abrir `debug/skiplagged/raw_*.json` para ver a estrutura real e ajustar o parser.

### End-to-end pelo backend

```powershell
uvicorn backend.app.main:app --reload --port 8000
# Em outro terminal:
curl -X POST http://localhost:8000/api/v1/search ^
     -H "Content-Type: application/json" ^
     -d "{\"origin\":\"GIG\",\"destination\":\"SSA\",\"date_start\":\"2026-07-15\"}"
```

Resposta inclui ofertas agrupadas por `scenario`. Conferir presença de `hidden_city` no JSON.

## Variáveis de ambiente

| Variável | Default | Função |
|---|---|---|
| `SKIPLAGGED_ENABLED` | `1` | `0` desliga o provider |
| `SKIPLAGGED_TIMEOUT` | `25` | Timeout (s) da chamada httpx |
| `SKIPLAGGED_WAIT_MS` | `12000` | Tempo (ms) que o Playwright espera pelas respostas XHR após `goto` |
| `SKIPLAGGED_PARSER_DEBUG` | (off) | `1` imprime contagem de ofertas/descartes |

## Limitações conhecidas

- **Round-trip**: Skiplagged não retorna estrutura inbound. Quando o caller pede `ROUNDTRIP` mas a oferta só traz ida, o parser degrada para `ONEWAY` para não estourar o validator. Funcional, mas incompleto — TODO próxima iteração.
- **Bagagem despachada**: hidden-city quebra bagagem despachada (o passageiro desembarca antes do destino oficial). O `risk_notes` registra isso, o frontend já renderiza o aviso amarelo no card.
- **Endpoints REST**: a lista em `fetch_via_httpx` é palpite — o Skiplagged não publica API oficial. Quando algum responde, ótimo; senão, cai sempre no Playwright (custo: ~5–10s).
- **Anti-bot**: o site pode mostrar CAPTCHA esporadicamente. Sem mitigação ativa hoje; Playwright às vezes ainda passa porque carregamos em modo headless com user-agent padrão.
