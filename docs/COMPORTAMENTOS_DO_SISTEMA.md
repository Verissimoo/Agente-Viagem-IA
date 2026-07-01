# COMPORTAMENTOS DO SISTEMA — o que esperamos que aconteça

> **Este é o documento-mãe de comportamento.** Sempre que definirmos uma regra de
> "o sistema deve fazer X", ela entra aqui — pra não se perder em refactor, troca de
> branch ou conversa nova. Leia antes de mexer no fluxo de busca/apresentação/auth.
> Detalhe técnico de busca vive em [`REGRAS_DE_BUSCA.md`](REGRAS_DE_BUSCA.md);
> guardrails de conteúdo em [`GUARDRAILS.md`](GUARDRAILS.md).
>
> **Processo (combinado):** toda regra de comportamento nova → documentar aqui no
> mesmo PR. Documentação de comportamento é obrigatória, não opcional.

---

## 1. Resultados de busca (cards)

- **Máximo de PROGRAMAS diferentes, a melhor tarifa de cada.** O vendedor quer ver o
  leque (LATAM Pass, Smiles, LifeMiles, Aeroplan, Miles&Smiles, Qatar/Avios, Alaska/
  Atmos…), não 5 cards do mesmo programa. Diversificação por programa
  (`smart_diversify` no fluxo normal; `_direct_miles_per_carrier` no split). Até 8
  cards (`MAX_RESULT_CARDS`).
- **Nunca 2 cards idênticos.** Não preencher slots repetindo um programa só pra
  "completar" — se só 3 fontes responderam, mostra 3.
- **Programa em DESTAQUE, companhia do voo ABAIXO.** No card de milhas, o nome do
  programa (o que o vendedor emite) é o título em destaque; a cia operadora vem
  embaixo (`✈ LATAM`). Cash (sem programa) mostra a companhia no título.
- **Recomendada = a mais barata SEGURA** (milhas / cash / quebra de trecho nacional).
  Hidden city e skip-split nunca lideram (são arriscados) — vão por último, marcados.
- **Ida-e-volta sem tarifa casada → NUNCA zerar.** Numa busca RT, ofertas só-ida são
  filtradas (não confundir meia-viagem com viagem inteira). Mas se as fontes só
  devolverem só-ida (ex.: long-haul KIX→GRU sem RT casado), **mostrar as opções de
  IDA com aviso claro** ("só ida; volta à parte") é obrigatório — melhor que cair no
  watchdog "problema entregando a cotação". O sistema nunca mostra erro genérico
  quando há opções reais pra exibir.
- **Mensagem ao cliente NUNCA cita nome de fonte/provedor** (Kayak, Skiplagged,
  AwardTool, seats.aero…). Painéis internos (status, health-check, validação) PODEM.

## 2. Cobertura de fontes (todas as do "motor de status")

- **Tudo que aparece no painel de status de programas é consultado na busca.** Sem
  buracos: BuscaMilhas (por cia) + QATAR + MCP_AWARD (rota intl) + seats.aero +
  AwardTool + Skiplagged + Kayak + Azul Cash.
- **Rota internacional inclui QATAR + MCP_AWARD** (`_companies_for_route`). Sem isso,
  GRU→DOH nunca consultava o award real da Qatar (vinha milhas estimadas).
- **United MileagePlus é SUPRIMIDO** dos resultados (`EXCLUDED_MILES_PROGRAMS`,
  default `MileagePlus`) — sem disponibilidade no momento. Reativar = env vazio.

### AwardTool — funcional via real-time automático
- **Programas = VAZIO (todos).** É a chave de cache que o site mantém quente. Lista
  explícita = chave fria = vazio. **Nunca** especificar programas.
- **Cache-primeiro → real-time-se-vazio** (`_run_search`): primeiro lê o cache do
  AwardTool (de graça); se vier vazio (rota fria), **liga o "Real-time Search"
  automaticamente** e crawleia ao vivo. Comprovado: rota fria GRU→DOH voltava 0,
  agora volta ~26 ofertas (Aeroplan/Alaska/Turkish/Smiles/Etihad) em ~25s.
- **O segredo do real-time:** clicar o `MuiSwitch-switchBase` (o elemento que dispara
  o onChange do React) — NÃO o texto nem o `<input>` escondido com force-click (esses
  não disparam a re-busca). Se a UI do AwardTool mudar, é aqui que quebra.
- **Custo:** o real-time é o "36-Entry" e **consome crédito por crawl**. Por isso o
  cache-primeiro (nosso TTL 180s + o cache deles) economiza: só gasta crédito quando
  a rota está fria dos dois lados. Monitorar o saldo de créditos da conta Pro.
- **Latência:** rota fria = +~25-40s (crawl ao vivo). Rota quente = instantâneo.
- **NÃO roda em rota DOMÉSTICA.** Os programas do AwardTool (Aeroplan/LifeMiles/…)
  não cobrem voo BR interno — é redundante com o BuscaMilhas (Smiles/LATAM/Azul) e o
  crawl segura um slot de browser por ~25-40s. Como só há `PLAYWRIGHT_MAX_BROWSERS`
  slots globais (default 2, anti-crash de RAM), o AwardTool doméstico ROUBAVA o slot
  do **Skiplagged** (a fonte de hidden-city doméstico) → Skiplagged voltava vazio.
  Fix: AwardTool pula doméstico (adapter). Reativar: `AWARDTOOL_DOMESTIC=1`.
- **Contenção de browser:** AwardTool + Kayak + Skiplagged dividem `PLAYWRIGHT_MAX_
  BROWSERS` (2) slots. Com o AwardTool fora do doméstico, Kayak+Skiplagged cabem em 2.
  Em host com RAM (upgrade), `PLAYWRIGHT_MAX_BROWSERS=3` alivia também o internacional.
- **ToS:** automatizar o AwardTool é ToS-sensível — uso gentil + cache obrigatórios.
- **Futuro:** quando houver `SEATS_AERO_API_KEY` (API sem browser/crédito/ToS), migrar
  o award pra lá. Hoje a key não está disponível pro Brasil → AwardTool é a fonte.

## 3. Radar de datas PRIMEIRO (flexibilidade alta)

- Flex > 3 dias → **primeiro** acha o melhor dia de preço de mercado (matriz Kayak +
  Skiplagged), pra a rota original **e** pra a quebra de trecho (leg internacional),
  e só então roda a busca cara naquele(s) dia(s). Detalhe em `REGRAS_DE_BUSCA.md §1`.

## 4. Quebra de trecho internacional (só-ida)

- Direto + **hub-split** (nacional até GRU/VCP + internacional separado) + skip-split.
- **Hub == origem → pular** (GRU→DOH com hub GRU não tem perna nacional; evita
  GRU→GRU sem sentido).
- **Uma opção por PROGRAMA** (não por companhia) — award multi-programa no mesmo voo
  (Qatar via LifeMiles vs Aeroplan vs Privilege) não pode colapsar numa só.
- **Cada perna validada em milhas** (ex.: BSB→GRU LATAM + GRU→DOH Qatar).
- Liga com `INTERNATIONAL_SPLIT_ENABLED=1` (ambiente do vendedor DEVE ter). Sem isso,
  internacional vira só Skiplagged sem validação. Detalhe em `REGRAS_DE_BUSCA.md §2`.

## 5. Hidden city — bilhete oficial em milhas

- Achou hidden city → cotar o **mesmo bilhete oficial** (rota vendida, ex.: GRU→SHJ)
  **em milhas na cia do voo** e mostrar (`miles_same_ticket`). Busca suplementar leve
  (sem AwardTool) pra não estourar o orçamento. Detalhe em `REGRAS_DE_BUSCA.md §3-4`.

## 6. Autenticação

- **Credenciais vivem no Postgres** (`chat.users.password_hash`), não em arquivo. O
  FS do Railway é efêmero → arquivo sumia no redeploy e o login dava 401. `_ensure_
  auth_schema` cria a coluna no boot (sem migration manual).
- **Recuperação:** re-registrar um e-mail cujo perfil já existe reaproveita o mesmo
  `user_id` (preserva threads/cotações). Há também **reset de senha** (sem SMTP).
- **`CHAT_DEV_AUTH_SECRET` fixo no Railway** (senão derruba sessões a cada deploy).

## 7. Status ao vivo + auditoria

- Painel de processamento **granular**: por provedor × perna ("GRU→DOH · ✓ Qatar
  (milhas) — 3 ofertas"). Depois do resultado, o vendedor pode reabrir o andamento
  interno ("🔍 Ver andamento da cotação").

## 8. Preço/cache

- **Cache curto** (cash 90s, milhas 180s) — tarifas mudam em minutos. Não aumentar
  TTL global sem aval. Botão "Atualizar preços" (`force_refresh`) invalida e refaz.

---

## Config / flags (referência rápida)

| Env | Default | Efeito |
|---|---|---|
| `MAX_RESULT_CARDS` | `8` | Máx. de cards (mais = mais programas distintos). |
| `INTERNATIONAL_SPLIT_ENABLED` | `0` | **`1` no vendedor** — liga radar+quebra+validação intl. |
| `EXCLUDED_MILES_PROGRAMS` | `MileagePlus` | Programas suprimidos (United fora). |
| `SEATS_AERO_API_KEY` | (vazia) | **Setar** → award determinístico (LifeMiles/Aeroplan/…). |
| `AWARDTOOL_ENABLED` | `0` | Award via browser; intermitente + ToS-sensível. |
| `ECONOMILHAS_ENABLED` | `1` | `0` desliga (sem créditos). |
| `CHAT_DEV_AUTH_SECRET` | (random) | **Fixo no Railway** senão derruba sessões. |
| `SPLIT_ALWAYS_INCLUDE` / `SUPPLEMENTARY_ALWAYS_INCLUDE` | ver código | Fontes nas buscas internas (split usa todas; suplementar é leve). |

## Pendências / próximos (revisar amanhã)
- [ ] **`SEATS_AERO_API_KEY`** — caminho definitivo pro award multi-programa.
- [ ] Validar em produção: login dos usuários travados volta (re-registro/reset).
- [ ] Observar comportamento geral das mudanças no dia seguinte.
