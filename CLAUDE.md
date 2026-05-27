# CLAUDE.md

Este arquivo orienta o Claude (Code, IDE, ou outro agente) ao trabalhar neste repositório. Leia antes de qualquer task.

## O que é o projeto

**Agente Viagem IA** é um sistema de busca inteligente de passagens aéreas voltado para vendedores B2B. Consulta múltiplos provedores em paralelo (cash, milhas, e hidden-city) e devolve o ranking unificado em BRL.

- **Backend**: Python 3.11 + FastAPI + Pydantic v2
- **Frontend**: Angular 21 (standalone components, SCSS)
- **LLM**: Groq via `litellm` — usado **apenas no resumo final**, nunca em coleta/parsing
- **Scraping**: Playwright + httpx (cascata) — só Skiplagged usa
- **Deploy**: Railway via Nixpacks (`Procfile` + `railway.toml`)

## Arquitetura

Dependência unidirecional: `api → services → providers → infrastructure`, com `domain/` importável de qualquer camada.

```
backend/app/
├── main.py                      # FastAPI entry · CORS · routers
├── api/v1/
│   ├── routes/                  # search.py · health.py · nlp.py
│   └── schemas/                 # DTOs Pydantic (HTTP transport)
├── domain/                      # Tipos puros (UnifiedOffer, Scenario, ...)
├── services/                    # Use-cases (orchestrator, ranking, segment_split, ...)
├── providers/                   # Uma pasta por fonte externa
│   ├── base.py                  # BaseSearchAdapter (contrato)
│   ├── kayak/                   # adapter · client · parser
│   ├── buscamilhas/             # + iata_resolver
│   ├── mcp_award/
│   ├── economilhas/             # client · parser (sem adapter — usado via pipeline dedicado)
│   └── skiplagged/              # COMPLEMENTAR — hidden city + split cash
├── infrastructure/              # cache · fx_rates · tracer · config
├── ai/summarizer.py             # Único ponto LLM (pós-ranking)
└── nlp/intent_parser.py         # Regex/heurísticas PT-BR

frontend/
└── src/app/
    ├── core/api.service.ts      # HttpClient → /api/v1
    ├── features/search-page/    # Form + render dos resultados
    ├── shared/flight-card/      # Card de oferta com badge de cenário
    └── models/flight.ts         # TS mirror dos DTOs do backend

tests/unit/                      # pytest com unittest discovery
├── ai/test_summarizer.py
├── providers/skiplagged/{test_adapter,test_parser}.py
└── services/{test_ranking,test_layover_classifier,test_flex_dates,test_intent_parser}.py

docs/                            # ARCHITECTURE.md · SKIPLAGGED_PROVIDER.md
```

## Comandos essenciais

Use sempre o `.venv` local — **nunca** instale no Python global.

### Backend
```powershell
.\.venv\Scripts\activate              # Windows
# source .venv/bin/activate           # macOS/Linux

uvicorn backend.app.main:app --reload --port 8000
# Docs:    http://localhost:8000/docs
# Health:  http://localhost:8000/api/v1/health

pytest tests/unit/ -v                 # rodar testes
pytest tests/unit/providers/skiplagged/ -v   # subset

playwright install chromium           # primeira vez (provider Skiplagged)
```

### Frontend
```powershell
cd frontend
npm install                            # primeira vez
ng serve                               # http://localhost:4200
ng build                               # produção → dist/frontend/
ng test                                # Karma + Jasmine
```

## Regras de ouro

1. **LLM apenas em `backend/app/ai/summarizer.py`.** Coleta e parsing usam regex/heurísticas. Nunca adicione `litellm`/`openai` em provider, parser, ou service.
2. **Falha de provider nunca derruba o pipeline.** O orchestrator (`services/search_orchestrator.py`) absorve exceções por adapter. Replicar esse padrão em providers novos.
3. **Skiplagged é complementar, não substituto.** Roda em paralelo aos provedores de milhas. Tem feature flag (`SKIPLAGGED_ENABLED=0` desliga). Ver `docs/SKIPLAGGED_PROVIDER.md`.
4. **Todo provider produz `UnifiedOffer`.** O parser do provider é responsável pela normalização (preço em BRL via `fx_rates`, derivação de stops, classificação de cenário). O ranking opera sobre `UnifiedOffer` cego à fonte.
5. **DTOs HTTP ≠ Domain models.** `api/v1/schemas/` define o que entra/sai pela HTTP. `domain/models.py` é interno. Não exponha `UnifiedOffer` diretamente em response sem necessidade — prefira mapear para DTO.
6. **Dependência só desce.** `domain/` não importa nada do projeto. `services/` pode importar `domain/`, `providers/`, `infrastructure/`. `api/` é o topo, importa qualquer camada abaixo. Nunca o contrário.
7. **Cache curto, sempre.** Tarifas são voláteis — fares podem mudar em minutos. TTL default: 90s para cash, 180s para milhas. Toda `UnifiedOffer` carrega `captured_at` (timestamp UTC). Antes de fechar venda real, o vendedor precisa usar **Atualizar preços** (flag `force_refresh=true` no `/search`) para invalidar o cache e refazer toda a busca. Nunca aumente o TTL global sem aval explícito.

## Padrões de código

### Python
- **Tipagem obrigatória** em assinaturas públicas (Pydantic models já dão isso de graça).
- **f-strings** sempre. Sem `.format()` ou `%`.
- **Imports absolutos** a partir de `backend.app...`. Não use relativos (`from ..foo import bar`).
- **Sem comentários óbvios.** Comente o *porquê*, não o *o quê*. Identificadores fazem o resto.
- **Docstrings curtas (1-3 linhas) em funções públicas.** Sem blocos verbosos.
- **Validação Pydantic v2** com `@model_validator(mode='after')` para invariantes cruzados (já existem em `UnifiedOffer`).
- **Concorrência**: `ThreadPoolExecutor` para fan-out de adapters síncronos. `asyncio` só dentro de FastAPI handlers.
- **Logs**: `print()` é aceitável para diagnósticos pontuais; em código de pipeline preferir `PipelineTracer.log_event`.

### TypeScript/Angular
- **Standalone components**, signals para estado local, `inject()` para DI (evite construtor com tipos).
- **Models em `models/flight.ts`** espelham os DTOs do backend manualmente. Se mexer no DTO Pydantic, atualize aqui.
- **Templates HTML** separados (não inline). SCSS com `:host` para escopo.

## Adicionar um provider novo

1. Criar `backend/app/providers/<nome>/{__init__.py,adapter.py,client.py,parser.py}`.
2. Adapter herda `BaseSearchAdapter` e implementa `search(request, use_fixtures, debug_dump) -> List[UnifiedOffer]`.
3. Client faz I/O (httpx/requests/playwright). Cache via `from backend.app.infrastructure.cache import cached_call`.
4. Parser converte payload bruto → `UnifiedOffer` com `source=SourceType.<NOVO>`. Adicione o valor no enum em `domain/models.py`.
5. Registrar no `_ADAPTER_MAP` em `services/search_orchestrator.py`. Se for sempre ativo, adicionar em `_ALWAYS_INCLUDE`.
6. Escrever 3-5 testes em `tests/unit/providers/<nome>/`: sucesso, erro absorvido, cache, payload vazio.

## Adicionar um cenário comercial novo

1. Adicionar valor em `Scenario` enum (`domain/models.py`).
2. Parser do provider deve setar `offer.scenario` + `offer.risk_notes` quando aplicável.
3. Frontend: adicionar em `SCENARIO_LABEL` e `SCENARIO_ORDER` em `features/search-page/search-page.ts`.

## Tarefas comuns (receitas)

| Quero... | Faça |
|---|---|
| Subir API local | `uvicorn backend.app.main:app --reload --port 8000` |
| Rodar testes rápidos | `pytest tests/unit/ -q` |
| Inspecionar JSON cru do Skiplagged | Olhar em `debug/skiplagged/raw_*.json` após uma busca |
| Desligar Skiplagged em prod | `SKIPLAGGED_ENABLED=0` no env |
| Habilitar resumo LLM | `ENABLE_AI_SUMMARY=1` + `GROQ_API_KEY` no env |
| Forçar offline (sem rede) | `PCD_OFFLINE=1` — adapters levantam `OfflineModeError` |
| Atualizar tabela de milhas | Editar `backend/app/services/rates.json` (faixas por programa) OU usar `PUT /api/v1/rates` |
| Desligar cache (debug) | `CACHE_DISABLED=1` no env — todo `cached_call` faz round-trip |
| Diminuir TTL do cash | `CACHE_CASH_TTL_S=60` (default 90) no env |
| Forçar refresh em produção | `POST /api/v1/search` com `force_refresh: true` (chamado pelo botão "Atualizar preços" da UI) |

## Variáveis de ambiente

Ver `.env.example`. Mínimos para o backend rodar sem erro de import:
- Nenhum obrigatório (todos os providers que precisam de credencial degradam graciosamente para `[]`).

Para experiência completa:
- `RAPIDAPI_KEY` (Kayak), `BUSCAMILHAS_CHAVE`/`SENHA`, `ECONOMILHAS_API_KEY`, `MCP_BEARER_TOKEN`, `GROQ_API_KEY`.

## O que **NÃO** fazer

- Não recrie `pcd/`, `miles_app/`, `streamlit_app_*.py` ou arquivos clientes na raiz (`kayak_client.py`, etc.). Era estrutura legada removida em maio/2026.
- Não adicione import de `litellm` em provider, parser ou service. LLM **só** em `backend/app/ai/`.
- Não chame Skiplagged sem timeout/try-catch. O adapter já protege; novos call-sites devem respeitar.
- Não modifique `UnifiedOffer` sem atualizar `frontend/src/app/models/flight.ts` e o resumo em `docs/ARCHITECTURE.md`.
- Não commit `debug/`, `.env`, `node_modules/`, `dist/`, `__pycache__/` (todos no `.gitignore`).

## Pontos de extensão futuros (fora do MVP)

- Redis em vez de cache in-memory (quando houver múltiplas instâncias).
- WebSocket no `/search` para resultados progressivos (atualmente retorna tudo no fim).
- OpenAPI → geração automática dos models TS (`openapi-typescript`).
- Autenticação por vendedor (multi-tenant).

## Onde aprofundar

- `docs/ARCHITECTURE.md` — diagrama textual + fluxo end-to-end de um request.
- `docs/SKIPLAGGED_PROVIDER.md` — como Skiplagged funciona, debug, feature flag.
- Código com mais lógica: `services/search_orchestrator.py`, `services/segment_split.py`, `services/ranking.py`.
