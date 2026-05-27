# Agente Viagem IA

Sistema de busca inteligente de passagens aéreas que combina:

- **Provedores de milhas**: BuscaMilhas (LATAM, GOL, AZUL, TAP, IBERIA, AMERICAN, INTERLINE, COPA), MCP Award Finder (genérico + Qatar), Economilhas.
- **Provedor de cash**: Kayak (via RapidAPI).
- **Skiplagged**: fonte complementar gratuita que descobre **hidden-city flights** (passageiro desembarca na conexão) e **quebra de trecho em cash** que os provedores tradicionais não exploram.
- **IA pós-ranking**: resumo em PT-BR gerado por Groq via `litellm` (apenas no resultado final — não em coleta nem parsing).

Arquitetura: **FastAPI** (backend) + **Angular** (frontend). Streamlit antigo permanece em `legacy_streamlit/` apenas para validação manual durante a transição.

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.11 · FastAPI · uvicorn · pydantic v2 |
| Frontend | Angular 21 · TypeScript · SCSS |
| Scraping Skiplagged | Playwright + httpx (cascata) |
| LLM (summarizer) | Groq via `litellm` (claude/llama 3.3 70B) |
| Cache | In-memory TTL 10min com semáforos por provedor |

## Estrutura do repositório

```
backend/app/
├── main.py                              # FastAPI entry
├── api/v1/                              # Routes + DTOs HTTP
├── domain/                              # Tipos puros (UnifiedOffer, Segment, ...)
├── services/                            # Lógica de negócio (orchestrator, ranking, segment_split, ...)
├── providers/                           # 1 pasta por fonte externa
│   ├── kayak/{adapter,client,parser}.py
│   ├── buscamilhas/{adapter,client,parser,iata_resolver}.py
│   ├── mcp_award/{adapter,client,parser}.py
│   ├── economilhas/{client,parser}.py
│   └── skiplagged/{adapter,client,parser}.py
├── infrastructure/                      # Cache, FX rates, tracer, config
├── ai/summarizer.py                     # LLM apenas no resumo final
└── nlp/intent_parser.py                 # Regex/heurísticas PT-BR (LLM desabilitado)

frontend/                                # Angular app
├── src/app/features/search-page/        # Form + render dos resultados
├── src/app/shared/flight-card/          # Card de cada oferta
├── src/app/core/api.service.ts          # HttpClient → /api/v1
└── src/app/models/flight.ts             # Espelha os DTOs do backend

legacy_streamlit/                        # UI antiga em Streamlit (DEPRECATED)
pcd/, miles_app/, *_client.py, *_parser.py   # Shims de re-export (compat)
docs/                                    # Arquitetura + provider docs
tests/, pcd/tests/                       # Testes unitários
```

## Rodar localmente

### Backend (FastAPI)

```powershell
# 1. Virtualenv
python -m venv .venv
.\.venv\Scripts\activate           # Windows
# source .venv/bin/activate         # macOS/Linux

# 2. Dependências
pip install -r requirements.txt
playwright install chromium

# 3. Variáveis
cp .env.example .env
# editar .env

# 4. Subir API
uvicorn backend.app.main:app --reload --port 8000
```

- API: http://localhost:8000/api/v1
- Docs Swagger: http://localhost:8000/docs
- Health: http://localhost:8000/api/v1/health

### Frontend (Angular)

```powershell
cd frontend
npm install
ng serve --open
```

Acessa em http://localhost:4200. O frontend já está apontado para `http://localhost:8000/api/v1` em dev (ver `frontend/src/environments/environment.ts`).

### UI legada (Streamlit, opcional)

Mantida durante a transição. A partir da raiz do repositório:

```powershell
streamlit run legacy_streamlit/streamlit_app_multiagent.py
```

Veja [legacy_streamlit/README_DEPRECATION.md](legacy_streamlit/README_DEPRECATION.md).

## Variáveis de ambiente

Ver `.env.example` para a lista completa. Mínimo:

| Variável | Quando é necessário |
|---|---|
| `RAPIDAPI_KEY` | Kayak (cash) |
| `BUSCAMILHAS_CHAVE` + `BUSCAMILHAS_SENHA` | BuscaMilhas (milhas) |
| `ECONOMILHAS_API_KEY` | Economilhas (milhas, opcional) |
| `MCP_BEARER_TOKEN` | MCP Award Finder (opcional) |
| `GROQ_API_KEY` | Summarizer pós-ranking (opcional) |
| `SKIPLAGGED_ENABLED` | `1` (default) — `0` desliga o provider |
| `ENABLE_AI_SUMMARY` | `1` ativa o resumo via LLM no /search |

> **Groq, não Grok**. O projeto usa Groq Cloud (console.groq.com) via litellm.

## Deploy

`Procfile` e `railway.toml` já configurados para FastAPI:

```
web: uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Healthcheck Railway: `/api/v1/health` (responde 200 com lista de adapters registrados).

Para subir o frontend em produção: build com `ng build` e servir `frontend/dist/` por trás de Nginx ou similar; apontar para o domínio do backend via `environment.prod.ts`.

## Testes

```powershell
# Backend
pytest pcd/tests/

# Frontend
cd frontend
ng test
```

## Documentação adicional

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Diagrama textual das camadas + responsabilidades.
- [docs/SKIPLAGGED_PROVIDER.md](docs/SKIPLAGGED_PROVIDER.md) — Como o provider Skiplagged funciona, cache, feature flag, debug.
- [legacy_streamlit/README_DEPRECATION.md](legacy_streamlit/README_DEPRECATION.md) — Status do Streamlit legado.
