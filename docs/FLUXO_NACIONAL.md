# Fluxo de cotação — NACIONAL (doméstico)

> Documento de referência do funcionamento atual para rotas **domésticas**
> (origem e destino no Brasil, ex.: BSB→SSA). Base para depois detalhar o
> **internacional**. Atualizado conforme as correções validadas em conversa.
>
> Rota é classificada por `ai/agents/routes.py::classify_route` → `domestic`
> quando origem E destino são IATAs brasileiros.

---

## 1. Interpretação do pedido (entrada do vendedor)

Entrada em **linguagem natural** (chat). Dois caminhos:

- **LLM (interpretação) — primário** (`ai/agents/interpreter.py`, em integração):
  a LLM lê a conversa e devolve JSON com TODOS os blocos de filtro: rota,
  janela de ida, janela de volta (separadas), flex, mala, voo direto,
  preferência de horário, cabine, passageiros, duração. **Nunca** inventa IATA
  nem datas — o código valida (IATA pela tabela oficial, datas).
- **Regex (`nlp/intent_parser.py`) — fallback** quando a LLM cai.

Regras de parsing já endurecidas:
- `voo de ida` / `ida` sem `volta` ⇒ só-ida (não confunde a 2ª data com volta).
- `DD de MES a/ao/até DD de MES` ⇒ range (cada data com seu mês).
- Janela de ida × janela de volta separadas ("ir 10‑12, voltar 25‑26").
- **Reset de nova busca**: uma mensagem com rota completa zera datas/flex/janelas
  anteriores — evita "thread envenenado" (slots velhos grudando).

Slots resultantes: `origin_iata`, `destination_iata`, `date_start`, `date_end`,
`return_from`, `return_to`, `flex_mode`, `trip_duration_days`, `trip_type`,
`baggage_checked`, `direct_only`, `time_preference`, `cabin`, `adults/children/infants`.

---

## 2. Fontes consultadas (providers)

Rodam em paralelo, com **orçamento de tempo** (`SEARCH_ADAPTER_BUDGET_S`, default 30s):
se um provider trava, segue com o parcial.

- **BuscaMilhas** — milhas das cias BR: **GOL/Smiles**, **LATAM Pass**, **TudoAzul** (+ Azul cash oficial).
- **Economilhas** — agregador de milhas (quando a quota permite).
- **Skiplagged** — **hidden city** (o coração do doméstico) + split cash. Só‑ida.
- **Kayak** — cash de mercado (benchmark) e **radar** de datas flexíveis.

---

## 3. Datas flexíveis

- **Ida‑e‑volta com flex** → **radar Kayak primeiro**: gera o cross‑product
  ida×volta (cap ~16, amostrando), o Kayak (barato) acha as combinações mais
  baratas, e só nas **top 2‑3** roda a busca cara (milhas + skip). Cobre TODAS
  as combinações na varredura; cota completo só nas melhores.
- **Só‑ida com flex** → `build_date_plan` busca todas as datas num único pool
  paralelo e o ranking pega a mais barata. (Radar NÃO é usado aqui — medimos
  que fica mais lento; o pool único já é eficiente.)

---

## 4. Hidden city (principal valor do doméstico)

Hidden city = bilhete com destino oficial além do que o cliente quer; ele
**desembarca na escala** (destino real) e descarta o resto. É **SEMPRE só‑ida**.

**Validação em milhas (regra de ouro):** busca o **bilhete OFICIAL** e filtra só
voos que **passam pela escala** onde o cliente desce. **Sem fallback**: se nenhum
voo em milhas passa por lá, NÃO valida (nunca casa com o voo errado).

**Dois valores no card** (ambos via dado real validado):
- `miles_alternative` — award **direto até o destino real** (quando é mais barato).
- `miles_same_ticket` — o **mesmo bilhete oficial em milhas** passando pela escala.

**Ida‑e‑volta** = hidden city é só‑ida, então o RT real é **dois bilhetes só‑ida
somados** (`services/roundtrip_hidden_city.py`): busca ida (O→D) e volta (D→O)
**separadas**, pega a melhor opção validada de cada perna (hidden city OU direto,
o mais barato) e **soma**. Roda em RT fixo E flex.

**Card hidden city** sempre mostra o **itinerário completo** (incluindo o destino
oficial do bilhete, mesmo descartado) — banner "Bilhete oficial: O → destino · cliente desce em X".

---

## 5. Conversão de milhas → BRL

Tabela em `services/rates.json` (faixas por programa). Atual: **GOL/Smiles 0,020**,
**LATAM** em faixas (0,030 ≤17k … 0,025), Azul 0,014, etc. O valor convertido é
**calculado no backend** e entregue pronto ao LLM (o LLM NUNCA recalcula).
Recarrega ao reiniciar o servidor ou via `PUT /api/v1/rates`.

---

## 6. Bagagem despachada (23kg)

Fonte de verdade: BuscaMilhas. Regras (`services/baggage.py`):
- **Smiles (GOL) doméstico** sem tier nos dados → **R$130 por trecho/passageiro**.
- Dado real (tier com mala) quando vem → usa.
- **Hidden city → não permite** despachar (a mala iria pro destino oficial).
- Capturado por regex/LLM (`baggage_checked`).

---

## 7. Quebra de trecho (split)

**NÃO se aplica ao doméstico no fluxo do chat.** Split via hub (GRU) compensa em
internacional; no doméstico é desvio que raramente vale → o presenter **pula** a
validação de split em milhas e o otimizador Kayak. (Skiplagged SPLIT_CASH cru
ainda pode aparecer se existir, mas sem validação extra.) Detalhe completo do
split fica no documento do **internacional**.

---

## 8. Apresentação (presenter, LLM)

- O LLM **só formata** — nunca calcula valor nem inventa programa. Recebe:
  valor em BRL pré‑calculado (`≈ R$`), programa único (`[Smiles]`, `[LATAM Pass]`).
- **Numa busca ida‑e‑volta, só apresenta ofertas coerentes (com volta)**;
  one‑way (skiplagged) entra só via o card combinado de junção.
- **Prioriza milhas** e lidera pela opção **mais barata VALIDADA** (incluindo
  hidden city validado). Cash do skip é referência pequena.
- **Cards ordenados por custo recomendado** → o mais barato é o 1º (badge
  RECOMENDADA), batendo com o texto.
- Preferência de horário = **suave** (prioriza, não exclui).
- Revalidações do presenter têm orçamento próprio (`PRESENTER_VALIDATION_BUDGET_S`,
  e hidden city `HIDDEN_CITY_VALIDATION_BUDGET_S`).

---

## 9. Robustez

- **Orçamento por fase** (adapters e revalidações) → resultado parcial em vez de travar.
- **Watchdog**: se intake completo mas zero ofertas → mensagem específica (não o
  genérico) quando o orchestrator já avisou.
- **Reset de nova busca** no intake (não herda slots velhos).

---

## 10. Pontos abertos / próximos

- Latência do RT flex com hidden city (várias buscas completas) — otimizar.
- Integração final da camada LLM no intake + filtro de horário.
- **Internacional**: detalhar split (hub GRU), milhas internacionais, validação
  por perna — em documento separado.
