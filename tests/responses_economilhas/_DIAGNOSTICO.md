# _DIAGNOSTICO.md — integração Economilhas

Bateria executada em 2026-05-05. Rotas × programas testadas individualmente
com 1 programa por chamada para isolar a estrutura de cada `data`.

## Resumo executivo

| Programa         | Server OK?       | Tem voo na amostra? | Parser nosso lê? | Veredito |
|------------------|------------------|---------------------|------------------|----------|
| **LATAM**        | sim — 200 success | sim (28–42 ofertas)  | **sim** ✓        | Funcionando |
| **SMILES**       | sim — 200 success | sim em GRU-MIA / GRU-REC; vazio em BSB-LIS | **sim** ✓ | Funcionando — em BSB-LIS a Smiles não vende esse trecho |
| **AZUL**         | sim — 200 success | sim em BSB-LIS / GRU-REC (5 journeys); vazio em GRU-MIA | **NÃO** ✗ | **Parser quebrado** |
| **COPA**         | sim em GRU-MIA / GRU-REC; **422** em BSB-LIS | sim (31 solutions GRU-MIA; 1 GRU-REC) | **NÃO** ✗ | **Parser quebrado** |
| **AZUL_INTERLINE** | **403** em rotas internacionais; 200 em GRU-REC | em todas as rotas testadas: `flights: []` | n/a (sem voo) | Cobertura provider muito limitada + parser inexistente |
| **IBERIA**       | sim — 200 success | em todas as rotas: `outbound: []` | n/a (sem voo) | Cobertura provider zero — investigar |
| **BRITISH**      | sim — 200 success | em todas as rotas: `outbound: []` | n/a (sem voo) | Cobertura provider zero — investigar |

> **TL;DR** — O caso reportado (BSB-LIS, 15/06/2026, "só LATAM apareceu")
> é causado por **três fenômenos distintos**:
>
> 1. **AZUL** retorna voos mas nosso parser não lê (correção P1).
>    SMILES legitimamente não voa direto BSB-LIS nessa data — sem bug.
> 2. **COPA / AZUL_INTERLINE** dão erro de provider (422/403) e nossa
>    UI sobe esse erro como "Failed to fetch data from…" — comportamento
>    correto, é instabilidade/cobertura do parceiro Economilhas.
> 3. **IBERIA / BRITISH** retornam `outbound: []` em TODAS as rotas
>    testadas, inclusive rotas onde essas companhias claramente operam
>    (BSB-LIS é rota Iberia, GRU-MIA é rota British/Avios). Suspeita
>    forte de cobertura limitada do parceiro Economilhas (P2 — abrir
>    ticket).

---

## Detalhamento por programa

### ✅ LATAM — funcional

- Sempre 200, `success=true`, `data` grande (329–388 KB).
- Parser atual lê `data.outbound.content[].summary.brands[].price` (LOYALTY_POINTS).
- 28–42 ofertas por busca. Sem ação.

### ✅ SMILES — funcional, mas cobertura por rota

- BSB-LIS: `requestedFlightSegmentList[0].flightList: []`. Calendário
  mostra que voo Smiles existe em outras datas próximas (12, 13, 14, 16,
  17, 18 jun). A 15/06 a Smiles não voa direto.
- GRU-MIA, GRU-REC: dezenas de voos retornados, parser extrai milhas
  puras corretamente.
- **Sugestão de melhoria** (P3): quando `calendarDayList` traz datas
  alternativas, sinalizar isso ao vendedor.

### 🔴 AZUL — parser desalinhado da estrutura real (P1)

**O parser atual procura o campo errado.** Estrutura real (BSB-LIS, 5
journeys disponíveis):

```
data.data.trips[].journeys[].fares[].paxPoints[].levels[].points.amount        ← MILHAS
data.data.trips[].journeys[].fares[].paxPoints[].levels[].points.discountedAmount  ← com desconto
data.data.trips[].journeys[].fares[].paxPoints[].levels[].taxesAndFees         ← TAXAS BRL
data.data.trips[].journeys[].fares[].paxPoints[].levels[].convenienceFee       ← TAXA EXTRA
data.data.trips[].journeys[].segments[].legs[].identifier.std/sta              ← horários
data.data.trips[].journeys[].segments[].legs[].identifier.carrierCode          ← cia
data.data.trips[].journeys[].segments[].legs[].identifier.flightNumber         ← voo
data.data.trips[].journeys[].segments[].legs[].identifier.departureStation/arrivalStation
```

**O que o parser atual procura** (`_parse_azul_data` em
`economilhas_offer_parser.py`):
- `journeys[].fares[].fareInfo.miles/points/loyaltyPoints` ← **não existe**
- `journeys[].legs[]`/`segments[]` no nível errado ← na realidade
  `journeys[].segments[].legs[].identifier.*`
- `journeys[].fares[].taxes` ← **não existe**, taxas estão em
  `paxPoints[].levels[].taxesAndFees`

**Por que o vendedor viu "Sem resultado"**: o parser nunca chega na
estrutura `paxPoints` e devolve lista vazia mesmo com 5 journeys reais.

### 🔴 COPA — parser cai no fallback genérico, perde segmentos (P1)

Estrutura real (GRU-MIA, 31 solutions):

```
data.originDestinations[].solutions[].lowestPriceCoachCabin.miles      ← MILHAS economy
data.originDestinations[].solutions[].lowestPriceCoachCabin.taxes      ← TAXAS BRL
data.originDestinations[].solutions[].lowestPriceBusinessCabin.miles   ← (business)
data.originDestinations[].solutions[].journeyTime                      ← duração total
data.originDestinations[].solutions[].numberOfLayovers                 ← escalas
data.originDestinations[].solutions[].flights[].marketingCarrier.flightNumber/airlineCode
data.originDestinations[].solutions[].flights[].departure.airportCode/flightDate/flightTime
data.originDestinations[].solutions[].flights[].arrival.airportCode/flightDate/flightTime
data.originDestinations[].solutions[].flights[].layoverTime
data.originDestinations[].priceCalendars[]                             ← datas com preço (calendário)
```

**O que o parser atual faz**: cai em `_parse_generic_data` que faz DFS
genérico. Encontra `miles=50000` e `taxes=59.23` mas:
- Não tem rota para extrair `flights[].departure/arrival` → segmentos vão zerados.
- A heurística `_walk_first(["departure", "departureDate", "std", ...])`
  cai no primeiro `flights[].departure` mas em forma de dict, sem
  sinalização de horário separado.
- Resultado: row "PARSER_PENDENTE" sem itinerário.

**Necessário**: parser específico `_parse_copa_data` que entenda
`originDestinations[].solutions[]`.

### 🟡 AZUL_INTERLINE — provider 403 + parser ausente (P2)

- BSB-LIS: provider 403 ("Failed to fetch data from AZUL_INTERLINE").
- GRU-MIA: provider 403 idem.
- GRU-REC: 200 com `departureFlights.flights: []` (vazio). Estrutura nova:

```
data.origin / data.finalDestination
data.departureFlights.date / data.departureFlights.flights[]
data.returnFlights.date / data.returnFlights.flights[]
data.pagination.{page, isFinalPagination}
```

**Conclusão**: a) maior parte das chamadas é provider 403 ou cobertura
zero; b) mesmo onde o provider responde 200, vem `flights: []`. Sem
amostra de voo real é impossível escrever o parser específico.
Sugestão: testar rota onde a Azul Interline tenha voo real (ex.: GRU
para destino LATAM com escala em parceira) ou aguardar provider melhorar.

### 🟡 IBERIA — provider sempre `outbound: []` (P2 — investigar parceiro)

Em **todas as 3 rotas** testadas (33 bytes cada):

```json
{ "outbound": [], "inbound": null }
```

Inclui BSB-LIS (rota óbvia da Iberia BSB→MAD→LIS). O provider 200 mas
com lista vazia consistentemente. Estrutura sugere formato similar
a CASH (`outbound: list, inbound: list|null`), mas sem amostra de voo
não dá para reverse-engineer os campos.

**Suspeita forte**: a Economilhas pode não ter feed Iberia ativo nesta
API key, ou Iberia exige parâmetros adicionais. Recomendação: **abrir
ticket Economilhas confirmando se a key tem cobertura Iberia ativa**.

### 🟡 BRITISH — idêntico ao IBERIA (P2)

Em todas as 3 rotas: `{outbound: [], inbound: null}`. GRU-MIA é rota
British (code-share via American) — esperaríamos ofertas Avios. Mesmo
cenário: cobertura provider em dúvida, abrir ticket.

---

## Quota antes / depois

- Antes: 200 OK
- Depois: 200 OK

Os bodies completos estão em `_quota_before.json` / `_quota_after.json`.
O serviço Economilhas não retornou os campos `limit`/`consumed`/`remaining`
no formato que a heurística do `_DIAGNOSTICO.md` autogerado esperava;
inspecionar os JSONs cru para os números reais.

---

## Lista priorizada de correções

### P1 — Corrigir parsers (data chega correto, parser não extrai)

1. **`_parse_azul_data`** — refazer baseado em
   `data.data.trips[].journeys[].fares[].paxPoints[].levels[].points.amount`
   (preferir `discountedAmount` quando aplicável). Taxas em
   `paxPoints[].levels[].taxesAndFees + convenienceFee`. Segmentos em
   `journeys[].segments[].legs[].identifier.*`. Há 5 journeys com voo
   real em BSB-LIS na amostra — usar como base.
   - Arquivo: `internacional_BSB_LIS_AZUL.json`

2. **`_parse_copa_data`** (novo — atualmente cai no genérico) — escrever
   despachante para `data.originDestinations[].solutions[]`. Milhas em
   `lowestPriceCoachCabin.miles`, taxas em `lowestPriceCoachCabin.taxes`,
   segmentos em `solutions[].flights[]` com `marketingCarrier.airlineCode/flightNumber`,
   `departure.{airportCode,flightDate,flightTime}` e `arrival.*` análogo.
   - Arquivo: `internacional_GRU_MIA_COPA.json`

### P2 — Cobertura/provider (não corrigível no nosso código)

3. **AZUL_INTERLINE**: provider 403 frequente em rotas internacionais.
   Em domésticas vem 200 mas vazio. Verificar contrato com Economilhas.
4. **IBERIA / BRITISH**: provider 200 mas `outbound: []` em todas as
   rotas testadas, inclusive rotas onde essas companhias operam.
   Abrir ticket Economilhas confirmando se a key tem feed Avios ativo.
5. **COPA BSB-LIS 422**: rota fora da malha Copa (opera via PTY).
   Comportamento correto do provider — UI já mostra como falha parcial.

### P3 — Melhoria UX

6. **SMILES sem voo + calendarDayList**: quando `flightList: []` mas
   `calendarDayList` tem datas próximas, exibir hint na UI ("Smiles
   não voa exatamente nessa data; mais próxima: 14/jun ou 16/jun").

---

## Validação rápida das hipóteses (antes de codar P1)

```bash
python -c "
import json
from economilhas_offer_parser import extract_rows_from_economilhas
for prog in ['AZUL','COPA']:
    fname = ('internacional_BSB_LIS_AZUL.json' if prog=='AZUL'
             else 'internacional_GRU_MIA_COPA.json')
    j = json.load(open(f'tests/responses_economilhas/{fname}',encoding='utf-8'))
    rows, _ = extract_rows_from_economilhas(j['response']['body'], 'OW')
    print(prog, 'rows extraidas:', len(rows))
"
```

Expectativa antes da correção: `0` rows para ambos. Após P1, deve passar
a `>0`. Esses arquivos viram o regression test natural.

---

## Arquivos gerados nesta bateria

- `_quota_before.json`, `_quota_after.json` — verificação de cobrança.
- `<rota>_<programa>.json` × 21 — payload + status + body cru por chamada.
- `_SUMMARY.md` — tabela compacta gerada automaticamente.
- `_PARSER_NOTES.md` — caminhos de campos hierárquicos por programa.
- Este arquivo (`_DIAGNOSTICO.md`) — relatório priorizado.

---

> **Próximo passo recomendado**: implementar P1 (AZUL e COPA) usando os
> arquivos de amostra acima como ground-truth, e depois acionar P2
> abrindo ticket com Economilhas sobre a cobertura Iberia/British/Azul
> Interline antes de qualquer outro investimento de parser.
