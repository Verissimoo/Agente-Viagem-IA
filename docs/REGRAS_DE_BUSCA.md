# REGRAS DE BUSCA — referencial de negócio (não regredir)

> Este documento fixa **regras de comportamento** que o sistema **NÃO pode deixar
> de fazer**. Elas já existem no código; serviram de base pro produto e se perdem
> facilmente em refactors. Antes de mexer no fluxo de busca, leia isto.
> Complementa `ARCHITECTURE.md` e `SKIPLAGGED_PROVIDER.md`.

---

## Princípio geral

Para uma cotação, o sistema **sempre** combina três coisas, nesta ordem:

1. **Achar a melhor DATA** (barata) com referenciais leves (Kayak / Skiplagged).
2. **Aprofundar a busca** só nessa(s) data(s) — milhas multi-programa + quebra de trecho.
3. **Validar** cada perna/bilhete em milhas antes de apresentar.

Nunca pular direto pro passo 2 numa rota internacional só-ida.

---

## 1. Radar de datas PRIMEIRO (flexibilidade alta)

**Regra:** quando a flexibilidade de datas é alta (`flex_span > 3` dias), o sistema
**primeiro** varre o intervalo com o **radar barato** (matriz flex do Kayak; Skiplagged
como referência) pra descobrir o **melhor dia de preço de mercado** — e só então roda
a cotação cara (milhas + validação) naquele(s) dia(s).

- O radar roda pra **as duas visões**: a **rota original** (ex.: `BSB→DOH`) **e a
  quebra de trecho** (cada hub → destino, ex.: `GRU→DOH`). As datas baratas podem
  diferir entre elas.
- Só as **top 2-3 datas** entram na busca cara. Varrer todas as datas estoura quota
  e tempo.
- Implementação: `services/date_radar.py` (`scan_dates`), `services/international_split.py`
  (`radar_international`, matriz Kayak por rota), `ai/agents/orchestrator.py` (decisão
  de Fase 1 / confirmação).

**Por quê:** milhas e scraping são caros e voláteis. Gastar isso em 6 datas é
desperdício; o radar de mercado aponta onde vale aprofundar.

---

## 2. Quebra de trecho INTERNACIONAL (só-ida)

**Regra:** rota internacional só-ida → o sistema orquestra e apresenta, em paralelo:

| Tipo | O que é | Exemplo |
|---|---|---|
| **Direto** | Voo único, milhas por **cada companhia** que retornou | `BSB→DOH` em LATAM Pass / Smiles / … |
| **Hub-split** | Nacional até o hub + internacional do hub, **cada perna em milhas** | `BSB→GRU` (LATAM milhas) + `GRU→DOH` (Qatar) |
| **Skip-split** | Hidden city / split via Skiplagged (complementar) | hidden city `BSB→…` desce em `DOH` |

Detalhes obrigatórios:

- **Milhas em TODOS os PROGRAMAS** que deram resultado, não só a mais barata
  (`_direct_miles_per_carrier`, agrupado por **programa**). Award multi-programa
  (AwardTool/seats.aero) traz vários programas no MESMO voo (ex.: Qatar via
  LifeMiles 60k vs Aeroplan 87k vs Privilege 200k) — agrupar por companhia
  colapsava tudo numa só; por programa preserva cada um. O vendedor quer o leque.
- **Hubs de quebra:** `GRU` e `VCP` (VCP = Azul, cash direto + milhas). Ver `HUBS`.
- **Hub == origem → pular.** Se a origem já é o hub (ex.: `GRU→DOH` com hub `GRU`),
  **não há perna nacional**: a quebra viraria `GRU→DOH` (cópia do direto) +
  `GRU→GRU` (sem sentido). `quote_international` pula hub `== origin` ou `== destination`.
- **Cobertura: TODAS as fontes do motor de status.** As buscas internas do split
  consultam **todos** os provedores do health-check — BuscaMilhas (por cia) +
  `QATAR` + `MCP_AWARD` (rota intl) + `SEATS_AERO` + `AWARDTOOL` + `SKIPLAGGED` +
  `KAYAK` + `AZUL_CASH` (`SPLIT_ALWAYS_INCLUDE`). Sem buracos: se aparece no painel
  de status de programas, é consultado na busca.
- **Quando o itinerário só existe saindo de um hub** (ex.: `BSB→DOH` só tem voo
  real saindo de `GRU`): o sistema **deve** associar isso e buscar nos providers
  o trecho internacional `GRU→DOH` **em milhas** (Qatar, etc.), e depois **juntar
  um nacional** `BSB→GRU`. É isso que transforma "só achei Skiplagged" em uma
  quebra de trecho de verdade, validada.
- **Companhia por perna aparece no card e na recomendação.** Se o melhor resultado
  é split, a recomendação cita as duas cias (ex.: "LATAM + Qatar Airways").
- **Cash sem programa plugado:** se o Kayak acha uma cia internacional barata que
  **não temos em milhas**, mostra o cash **+10%** (`KAYAK_MARKUP`) e marca "mais
  barato, mas fora dos nossos programas de milhas" (`_direct_cash_unplugged`).

**Feature flag:** `INTERNATIONAL_SPLIT_ENABLED=1` liga todo esse fluxo. Com `=0`
(default), o internacional cai no `run_search` normal e volta **só Skiplagged cash,
sem validação** — que é o sintoma de "a quebra de trecho parou de funcionar".
**Ambiente do vendedor deve ter `=1`.**

**Latência:** ~2min (piso de scraping externo). Aceitável porque roda só na melhor data.

> **AwardTool DENTRO das buscas internas do split** (decisão do vendedor: cobrir
> todos os programas). É Playwright (~40-90s/crawl) → numa quebra com vários hubs
> soma minutos + RAM (vários Chromium). Mitigado pelo "hub == origem → pular"
> (menos buscas). Em host apertado (Railway, OOM), tirar via
> `SPLIT_ALWAYS_INCLUDE="ECONOMILHAS,SEATS_AERO,SKIPLAGGED,AZUL_CASH,KAYAK"`.

---

## 3. Validação por perna / por bilhete (em milhas)

**Regra:** antes de apresentar, o sistema **pega a opção** e valida em milhas:

- **Split:** valida **cada perna** na sua data e cia operadora. Ex.: para
  `BSB→GRU (LATAM) + GRU→DOH (Qatar)`, valida `BSB→GRU` em **LATAM Pass** (e diz
  quantas milhas) e busca `GRU→DOH` no **provider da Qatar**. Implementação:
  `validate_split_with_supplementary` / `supplementary_miles_search_for_split`.
- **Hidden city:** valida o **mesmo bilhete oficial** em milhas na cia do voo
  (`miles_same_ticket`) — ver seção 4.
- **Sem fallback errado:** se nenhum voo em milhas do bilhete oficial passa pela
  cidade onde o passageiro desce, **não validar** (devolver um voo qualquer que
  não para lá é bug recorrente — já corrigido antes).

> **Gotcha de timeout (jun/2026):** as buscas suplementares são `run_search`
> novos com orçamento curto (~18-35s). Com AwardTool ligado elas estouravam e
> voltavam vazias → o card perdia o valor em milhas. Fix: usam
> `SUPPLEMENTARY_ALWAYS_INCLUDE` (Economilhas + seats.aero, sem AwardTool/Skiplagged).
> Ver `ai/agents/hidden_city_validator.py`.

---

## 4. Hidden city — bilhete oficial em milhas (`miles_same_ticket`)

**Regra:** hidden city achado → cotar o **bilhete oficial completo** (a rota
vendida, não o destino real) **em milhas, na cia do voo**, e mostrar esse valor.

- Ex.: hidden `GRU→SHJ` (cliente desce em `DOH`) → cotar `GRU→SHJ` em milhas
  LATAM (passando por DOH) e exibir `miles_same_ticket`.
- É **diferente** de `miles_alternative` (award **direto** ao destino real DOH,
  tirado do pool já coletado — instantâneo).
- Prioriza a **mesma cia** do voo; filtra quem passa pela escala onde o cliente desce.

Ver `[[hidden-city-same-ticket-miles]]` (memória) e seção 3 (timeout).

---

## 5. Skiplagged — regras de validação (complementar)

Skiplagged é **fonte complementar** (hidden city + split cash). Mostra que a quebra
é **viável** e **quais companhias** operam cada trecho — usar isso como **sinal**
pra direcionar a busca em milhas.

- **One-way:** validar a oferta **pela escala** (o passageiro voa até a conexão e
  descarta o resto). Validar com o voo físico certo (mesma escala), nunca com um
  voo qualquer da rota.
- **Round-trip = 2 bilhetes só-ida.** Hidden city é one-way por natureza; ida e
  volta são dois bilhetes separados, cada um validado por escala.
- **Bug recorrente do fallback:** não cair num voo que não passa pela cidade de
  desembarque real só pra "ter um número". Sem match correto → não validar.

Ver `docs/SKIPLAGGED_PROVIDER.md` e `[[skiplagged-hidden-city-validation]]`.

---

## 6. Config — flags e budgets relevantes

| Env | Default | Efeito |
|---|---|---|
| `INTERNATIONAL_SPLIT_ENABLED` | `0` | **`1` liga** radar+quebra+validação no internacional só-ida. **Vendedor: `1`.** |
| `SPLIT_ALWAYS_INCLUDE` | `ECONOMILHAS,SEATS_AERO,AWARDTOOL,SKIPLAGGED,AZUL_CASH,KAYAK` | Fontes nas buscas internas do split (cobre todo o motor de status). Tire AwardTool se faltar RAM. |
| `SUPPLEMENTARY_ALWAYS_INCLUDE` | `ECONOMILHAS,SEATS_AERO` | Fontes na validação suplementar (sem AwardTool/Skiplagged). |
| `AWARDTOOL_ENABLED` | `0` | Award via Playwright (~40-90s). Pesado — fora de split/validação. |
| `HIDDEN_CITY_VALIDATION_BUDGET_S` | `35` | Orçamento da validação de hidden city. |
| `PRESENTER_VALIDATION_BUDGET_S` | `18` | Orçamento das demais validações no presenter. |
| `EXCLUDED_MILES_PROGRAMS` | `MileagePlus` | Programas suprimidos dos resultados (ex.: United, sem disponibilidade). |

---

## Checklist anti-regressão (rodar mentalmente a cada mexida no fluxo)

- [ ] Flex alto → o radar de data roda **antes** da busca cara (rota original **e** quebra)?
- [ ] Internacional só-ida → aparece **direto + hub-split** (não só Skiplagged)?
- [ ] Milhas de **todas** as cias que retornaram, não só a mais barata?
- [ ] Split mostra **cia por perna** (ex.: LATAM + Qatar) no card **e** na recomendação?
- [ ] Cada perna **validada em milhas** (com o número de milhas), sem fallback errado?
- [ ] Hidden city mostra o **mesmo bilhete oficial em milhas** (`miles_same_ticket`)?
- [ ] As buscas suplementares/split **não** puxam AwardTool (senão estouram o tempo)?
