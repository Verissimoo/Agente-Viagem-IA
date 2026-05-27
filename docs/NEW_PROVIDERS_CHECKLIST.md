# Integração de Providers Novos — Checklist

Este documento descreve o que é necessário para adicionar cada um dos provedores solicitados (Aeroplan, LifeMiles, Atmos) ao Agente Viagem.

## Status atual da malha de providers

| Programa / Provider | Status hoje | Caminho de integração |
|---|---|---|
| LATAM Pass | ✅ Ativo | BuscaMilhas (`BUSCAMILHAS_LATAM`) |
| GOL Smiles | ✅ Ativo | BuscaMilhas (`BUSCAMILHAS_GOL`) |
| Azul TudoAzul | ✅ Ativo | BuscaMilhas (`BUSCAMILHAS_AZUL`) |
| Azul Pelo Mundo (Interline) | ✅ Ativo | BuscaMilhas (`BUSCAMILHAS_INTERLINE`) |
| TAP Miles&Go | ✅ Ativo | BuscaMilhas (`BUSCAMILHAS_TAP`) |
| American AAdvantage | ✅ Ativo | BuscaMilhas (`BUSCAMILHAS_AMERICAN`) |
| Iberia Plus / Avios | ✅ Ativo | BuscaMilhas (`BUSCAMILHAS_IBERIA`) + MCP Award |
| British Airways Avios | ✅ Ativo | MCP Award (`british_airways`) |
| Qatar Privilege Club | ✅ Ativo | MCP Award (`MCP_QATAR`) — adapter dedicado |
| Cathay Asia Miles | ✅ Ativo | MCP Award (`cathay_pacific`) |
| Copa ConnectMiles | ✅ Ativo | BuscaMilhas (`BUSCAMILHAS_COPA`) |
| Virgin Atlantic Flying Club | ✅ Coberto | MCP Award (`virgin_atlantic`) |
| **Air Canada Aeroplan** | ❌ Pendente | Ver seção abaixo |
| **Avianca LifeMiles** | ❌ Pendente | Ver seção abaixo |
| **Atmos** | ❓ Programa não identificado — confirmar nome correto |

---

## Aeroplan (Air Canada)

### Opções de integração viáveis

**Opção 1 — Via MCP Award Travel Finder (mais rápido)**

Se o MCP Award Finder oferece Aeroplan como provider, basta adicionar uma nova classe de adapter herdando `McpAwardAdapter` com `provider_id="air_canada"`. Verificar via:

```bash
curl -H "Authorization: Bearer $MCP_BEARER_TOKEN" https://awardtravelfinder.com/api/v1/providers
```

Esforço estimado: **3-4 horas** (adapter + registro no orchestrator + rates.json).

**Opção 2 — Scraping direto Aeroplan.com**

Aeroplan tem busca pública em https://www.aircanada.com/aeroplan/redeem. Estratégia similar ao Skiplagged:
- Playwright headless + interceptação de XHR
- Endpoint XHR é provavelmente `/aeroplan/redeem/api/...`
- Cloudflare/anti-bot precisa ser navegado

Esforço estimado: **2-3 dias** (scraper + parser + testes).

**Opção 3 — Seats.aero API (paga)**

[seats.aero](https://seats.aero) agrega Aeroplan e cobra ~$50/mês. API REST oficial.

Esforço estimado: **1 dia** (cliente HTTP + parser + chave de API).

### O que você precisa providenciar

- [ ] Decidir entre opções 1, 2 ou 3.
- [ ] **Se opção 1**: confirmar com o suporte do MCP Award Finder se `aeroplan` ou `air_canada` está disponível como provider.
- [ ] **Se opção 2**: nada (sem credencial). Risco: anti-bot, IP block, instabilidade.
- [ ] **Se opção 3**: criar conta em seats.aero, gerar API key, definir orçamento mensal.
- [ ] Adicionar entrada `AEROPLAN` em [`backend/app/services/rates.json`](../backend/app/services/rates.json) — valor médio BRL/milha do Aeroplan no Brasil (sondar com mercado).
- [ ] Adicionar `BUSCAMILHAS_AEROPLAN` ou `AEROPLAN` ao enum `SourceType` em [`backend/app/domain/models.py`](../backend/app/domain/models.py).
- [ ] Mapear Aeroplan → carriers cobertos em `PROGRAM_COVERAGE` (Star Alliance: AC, UA, LH, TK, NH, SQ, etc.) em [`backend/app/services/miles_match.py`](../backend/app/services/miles_match.py).

---

## LifeMiles (Avianca)

### Opções de integração viáveis

**Opção 1 — Via MCP Award Travel Finder**

Mesmo procedimento: ver se MCP suporta `avianca` ou `lifemiles`.

**Opção 2 — Scraping direto lifemiles.com**

LifeMiles tem busca pública em https://www.lifemiles.com/fly/redeem. Tecnologia parecida com a do Aeroplan.

**Opção 3 — Seats.aero**

Cobre LifeMiles.

### O que você precisa providenciar

Mesmas check-marks do Aeroplan, trocando o nome do programa:

- [ ] Definir opção (MCP / Scraping / Seats.aero).
- [ ] Acesso ou contrato (se MCP/Seats.aero).
- [ ] Entradas em `rates.json`, `SourceType`, `PROGRAM_COVERAGE`.
- [ ] LifeMiles é Star Alliance: cobertura similar ao Aeroplan + Avianca (AV).

---

## "Atmos" — preciso confirmar

Não encontrei programa de milhas conhecido chamado "Atmos". Pode estar se referindo a:

- **123Milhas (extinto)** — marketplace de milhas, fechou em 2023
- **Smiles & Fly / Smiles Click** — features dentro do Smiles
- **iupp** — programa de cartão Itaú
- **Livelo** — programa Bradesco/Banco do Brasil
- **Smiles Travel** — sub-produto do Smiles

**Você poderia me confirmar:** qual é exatamente o "Atmos"? URL do site ou nome completo do programa ajudaria a planejar a integração.

---

## Multi-trecho (Multi-City)

Suportar itinerários `A → B → C` em vez de só `A → B` ou `A ↔ B`.

### Por que é grande

- Mexe na estrutura central do `SearchRequest` (precisa virar `legs: List[LegDefinition]`).
- Cada adapter precisa entender múltiplas pernas (Kayak suporta nativo, BuscaMilhas e Economilhas tipicamente não — viraria várias buscas A→B + B→C concatenadas).
- Ranking precisa pontuar combinações entre pernas, não ofertas isoladas.
- Cache key muda.
- Frontend: formulário precisa permitir N pernas dinamicamente.

### Esforço estimado

| Componente | Esforço |
|---|---|
| Refactor de `SearchRequest` em `domain/models.py` | 4-6h |
| Adapter Kayak — suportar multi-leg | 1-2 dias |
| BuscaMilhas/MCP/Economilhas — quebrar em N buscas | 1-2 dias |
| Ranking — pontuar combinações entre pernas | 1 dia |
| Frontend — form dinâmico de N pernas | 1 dia |
| Testes end-to-end | 2 dias |

**Total: ~10 dias de esforço.**

Sugiro só priorizar depois que a base atual estiver validada em produção com vendedores reais.

---

## Tabela resumo — esforço por feature pendente

| Feature | Esforço | Bloqueio |
|---|---|---|
| Aeroplan via MCP | 3-4h | Confirmar se MCP suporta |
| Aeroplan via Scraping | 2-3 dias | Nenhum (mas instável) |
| Aeroplan via Seats.aero | 1 dia | Conta + ~$50/mês |
| LifeMiles via MCP | 3-4h | Confirmar se MCP suporta |
| LifeMiles via Scraping | 2-3 dias | Nenhum (mas instável) |
| LifeMiles via Seats.aero | 1 dia | Mesma conta acima |
| Atmos | ? | Confirmar qual é o programa |
| Multi-trecho | ~10 dias | Nenhum técnico — só decisão de prioridade |

---

## O caminho recomendado

Para entrar rápido com os 3 novos programas:

1. **Hoje mesmo**: testar `MCP_BEARER_TOKEN` chamando o endpoint de providers do MCP Award para ver o que está disponível.
2. **Se MCP cobrir Aeroplan + LifeMiles**: ~1 dia para adicionar os dois adapters.
3. **Se não cobrir**: avaliar Seats.aero como ponte rápida (~1 dia + custo recorrente) vs scraping próprio (2-3 dias + manutenção contínua).
4. **Atmos**: aguardar confirmação do nome correto.
5. **Multi-trecho**: postergar para depois.
