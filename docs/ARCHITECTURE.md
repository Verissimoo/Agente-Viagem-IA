# Arquitetura

Documento textual das camadas e responsabilidades. Cada camada só importa
das camadas abaixo dela (regra de dependência unidirecional, validada por
inspeção manual e durante code review).

```
┌────────────────────────────────────────────────────────────────────┐
│  frontend/ (Angular)                                                │
│    features/search-page  ──HTTP──►  ApiService ──►  /api/v1/...    │
└────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼ HTTP (JSON)
┌────────────────────────────────────────────────────────────────────┐
│  backend/app/api/v1/  (transporte)                                  │
│    routes/{search,nlp,health}.py · schemas/  (DTOs Pydantic)        │
│      • Validação de entrada/saída                                   │
│      • Erros HTTP                                                   │
│      • NÃO contém lógica de negócio                                 │
└────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌────────────────────────────────────────────────────────────────────┐
│  backend/app/services/  (use-cases / lógica de negócio)             │
│    search_orchestrator.py    ┐                                     │
│    ranking.py · conversion.py│   Operam sobre UnifiedOffer:        │
│    segment_split.py          │   nunca chamam HTTP nem DB direto.  │
│    miles_match.py            │   Recebem objetos do domain,        │
│    smart_quote.py            │   chamam providers.                 │
│    layover_classifier.py     │                                     │
│    flex_dates.py · formatter ┘                                     │
└────────────────────────────────────────────────────────────────────┘
              │                              │
              ▼                              ▼
┌──────────────────────────────┐   ┌────────────────────────────────┐
│  backend/app/providers/      │   │  backend/app/ai/                │
│    base.py  (BaseSearchAdapter)│   │    summarizer.py              │
│    kayak/        (cash)       │   │      • litellm + Groq          │
│    buscamilhas/  (milhas)     │   │      • SÓ pós-ranking          │
│    mcp_award/    (milhas)     │   │      • Feature flag            │
│    economilhas/  (milhas)     │   └────────────────────────────────┘
│    skiplagged/   (hidden-city)│
│                              │   ┌────────────────────────────────┐
│  Cada um expõe um adapter    │   │  backend/app/nlp/               │
│  que parseia o payload bruto │   │    intent_parser.py             │
│  para UnifiedOffer.          │   │      • Regex/heurísticas PT-BR  │
└──────────────────────────────┘   │      • LLM DESATIVADO           │
              │                    └────────────────────────────────┘
              ▼
┌────────────────────────────────────────────────────────────────────┐
│  backend/app/domain/  (tipos puros, zero I/O)                       │
│    models.py    UnifiedOffer, Segment, Itinerary, Scenario, ...     │
│    errors.py    PcdError, OfflineModeError                          │
│                                                                     │
│  Pode ser importado de qualquer camada acima sem criar              │
│  dependência circular.                                              │
└────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌────────────────────────────────────────────────────────────────────┐
│  backend/app/infrastructure/  (plumbing técnico)                    │
│    cache.py        in-memory TTL + semáforos por provedor          │
│    fx_rates.py     conversão USD/EUR/... → BRL (Frankfurter)       │
│    tracer.py       PipelineTracer (latência por estágio)           │
│    config.py       carga de env vars                               │
└────────────────────────────────────────────────────────────────────┘
```

## Fluxo da busca

1. **Frontend** (Angular `SearchPageComponent`) → submete formulário.
2. **HTTP** `POST /api/v1/search` com `SearchRequestDTO`.
3. **Route** (`api/v1/routes/search.py`) valida DTO → chama `search_orchestrator.run_pipeline(...)`.
4. **Orchestrator** monta `SearchRequest` do domínio, expande datas via `flex_dates`, dispara um `ThreadPoolExecutor` com até 11 adapters em paralelo (Kayak + 8 BuscaMilhas + 2 MCP + Skiplagged).
5. Cada **provider** faz I/O (HTTP/scraping), parseia o payload para `UnifiedOffer` (com `scenario` opcional preenchido pelo Skiplagged) e devolve a lista.
6. **Services** classificam layovers, ranqueiam (`equivalent_brl` normalizado via `conversion`) e selecionam o top-N.
7. **AI summarizer** (opcional, se `include_summary=true` e `ENABLE_AI_SUMMARY=1`) recebe o top-N **já normalizado** e devolve um resumo curto em PT-BR.
8. **Response** `SearchResponseDTO` agrupa as ofertas por `Scenario` (frontend renderiza em sections).

## Cenários comerciais (`Scenario`)

| Valor | Origem | Significado |
|---|---|---|
| `cash_direct` | Kayak | Voo padrão pagando em dinheiro |
| `miles_direct` | BuscaMilhas/MCP/Economilhas | Resgate direto em milhas |
| `hidden_city` | **Skiplagged** | Bilhete com destino oficial além da cidade do passageiro; ele desce na conexão. Sem bagagem despachada. |
| `split_cash` | **Skiplagged** ou segment_split agent | 2 bilhetes em cash mais baratos do que 1 direto |
| `split_miles` | segment_split agent + BuscaMilhas | Combinação de pernas em milhas + cash |

## Falha isolada

Falhas de provedor são absorvidas pelo orchestrator (`_run_one_adapter` no `search_orchestrator.py`) — uma fonte caindo nunca derruba a busca. Skiplagged tem feature flag (`SKIPLAGGED_ENABLED=0`) para corte rápido se o site mudar a estrutura.

## Re-export shims (transição)

Durante a refatoração, módulos antigos (`pcd/core/schema.py`, `kayak_client.py`, etc.) foram reduzidos a shims que fazem `from backend.app.<novo>.<path> import *`. Isso mantém a UI Streamlit legada funcional sem fork. Os shims serão removidos em uma PR dedicada quando o Streamlit for descomissionado em produção.
