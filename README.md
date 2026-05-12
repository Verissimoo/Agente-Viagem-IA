# Agente de Cotação PcD (Multiagente)

Aplicação Streamlit que orquestra agentes de busca de tarifas em **dinheiro**
(Kayak via RapidAPI) e **milhas** (BuscaMilhas, Economilhas, MCP Award) para
produzir cotações comparativas em BRL.

## Stack

- **Frontend / UI**: Streamlit
- **Linguagem**: Python 3.12
- **LLM** (intent parser): Groq Cloud via `litellm`
- **Fontes de dados**: Kayak (RapidAPI), BuscaMilhas, Economilhas, MCP Award

## Rodar localmente

```bash
# 1. Criar e ativar virtualenv
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # macOS/Linux

# 2. Instalar dependências
pip install -r requirements.txt

# 3. Configurar variáveis
cp .env.example .env
# editar .env com as credenciais

# 4. Executar
streamlit run streamlit_app_multiagent.py
```

Acessa em http://localhost:8501.

## Deploy no Railway

O projeto está configurado para deploy direto no [Railway](https://railway.app)
via Nixpacks (sem Dockerfile). Arquivos relevantes:

- `Procfile` — comando de start
- `railway.toml` — configuração de build, healthcheck e restart policy
- `nixpacks.toml` — fixa Python 3.12 e gcc para build de wheels
- `runtime.txt` — versão Python (`python-3.12`)
- `.streamlit/config.toml` — headless, `showErrorDetails = true`
- `.env.example` — template de todas as variáveis de ambiente

### Passo a passo

1. **Push do código para o GitHub** (público ou privado).
2. Acessar [railway.app](https://railway.app) e conectar com GitHub.
3. **New Project → Deploy from GitHub repo** → selecionar o repositório.
4. Railway detecta automaticamente como projeto Python via Nixpacks.
5. Ir em **Variables** e adicionar TODAS as variáveis listadas no `.env.example`
   (RAPIDAPI_KEY, BUSCAMILHAS_CHAVE, BUSCAMILHAS_SENHA, ECONOMILHAS_API_KEY,
   MCP_BEARER_TOKEN, GROQ_API_KEY, etc).
6. Aguardar o primeiro deploy (~3-5 minutos).
7. Em **Settings → Networking**, clicar em **Generate Domain** para obter a
   URL pública (algo como `xxxx.up.railway.app`).
8. (Opcional) **Settings → Networking → Custom Domain** para apontar um
   domínio próprio.

### Healthcheck

Streamlit expõe nativamente `/_stcore/health`. Já configurado no
`railway.toml` — Railway vai marcar o serviço como saudável só quando o
endpoint responder 200.

### Logs

Diferente do Streamlit Cloud, Railway entrega **stdout/stderr completos**
em **Deployments → View logs**. O `showErrorDetails = true` em
`.streamlit/config.toml` garante que tracebacks reais apareçam, sem o
"redacted to prevent data leaks".

### Recursos

Plano gratuito do Railway (~US$5/mês de créditos) cobre:
- 8 GB RAM (vs. 1 GB do Streamlit Cloud)
- 8 vCPU compartilhadas
- Filesystem efêmero gravável (`/tmp` e CWD)
- Auto-deploy via `git push`

## Variáveis de ambiente

Ver `.env.example` para a lista completa. Mínimo para subir:

- `RAPIDAPI_KEY` — chave Kayak (RapidAPI)
- `BUSCAMILHAS_CHAVE` + `BUSCAMILHAS_SENHA` — credenciais BuscaMilhas
- `GROQ_API_KEY` — para o intent parser em PT-BR
- `ECONOMILHAS_API_KEY` — opcional, provedor alternativo de milhas
- `MCP_BEARER_TOKEN` — opcional, MCP Award Search

> **Nota:** o projeto usa **Groq** (cloud de inferência LLM, console.groq.com),
> não **Grok** (xAI). Variável correta: `GROQ_API_KEY`.

## Estrutura

```
streamlit_app_multiagent.py     # ponto de entrada Streamlit
pcd/                            # núcleo de negócio
├── agents/                     # smart_quote, segment_split, miles_match
├── adapters/                   # kayak, buscamilhas, mcp_award
├── core/                       # schema, ranking, conversion, formatter
├── nlp/                        # intent_parser (Groq + regex fallback)
└── cache.py                    # cache TTL em memória
miles_app/                      # cliente BuscaMilhas + iata_resolver
ui/                             # styles, formatters, renderer
kayak_client.py                 # cliente RapidAPI/Kayak
economilhas_client.py           # cliente Economilhas
mcp_client.py                   # cliente MCP Award
```

## Cache

O cache é em memória (`pcd/cache.py`), TTL de 10 minutos. No Railway o
processo persiste entre requests, mas reinicia em deploys (igual ao
Streamlit Cloud). Próximo passo: migrar para Redis quando a separação
backend acontecer.
