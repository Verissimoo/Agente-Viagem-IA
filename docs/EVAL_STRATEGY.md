# EVAL_STRATEGY — como garantimos que a IA não quebra

> "Quando pessoas importantes vão testar o sistema, na primeira requisição normal
> delas o sistema quebra em algum fluxo não pensado — e isso é horrível pra nossa
> imagem."

Esse doc descreve como blindamos o chat contra isso, do jeito que as empresas que
operam IA em produção fazem: **datasets de cenário + gate determinístico no CI +
eval com LLM-juiz + red-teaming**, com cada furo virando teste de regressão.

## O problema

O fluxo de cotação é uma conversa. Ele tem **estado** (slots preenchidos aos
poucos, `awaiting_field`, flex, etc.). Bugs aparecem nas **transições** que
ninguém pensou: o vendedor responde "06/08" quando perguntamos a volta, ou "de
GRU" quando perguntamos a origem, e o parser joga no slot errado → **loop**. Um
teste unitário de função não pega isso; precisa simular a **conversa inteira**.

## As duas camadas

### Camada 1 — Cenários determinísticos (gate de CI) ✅ ativo

`tests/scenarios/` — roda no `pytest`, **sem rede**, **LLM mockado**. Força o
caminho regex/heurístico e dirige o `intake_node` turno-a-turno, afirmando o
comportamento: slots certos, sem loop, chega na busca, pede o que falta sem
quebrar.

- `harness.py` — `run_intake(turns, ...)` simula a conversa; `ConversationResult`
  expõe `.slots`, `.awaiting`, `.reached_search`, `.asked_twice("...")`.
- `test_intake_scenarios.py` — **frases em linguagem natural** como o vendedor
  escreve. Cobre: cada obrigatório respondido solto (origem/destino/ida/volta/pax),
  frases completas one-shot, construção em vários turnos, flex/duração, e o
  loop-guard.
- `test_safety_scenarios.py` — jailbreak, conteúdo nocivo, off-topic, e o
  **anti-falso-positivo** (viagem legítima nunca bloqueada).

**Por que mockar o LLM?** Determinismo. O gate de CI tem que ser reproduzível e
rápido (~1s), sem custo de token e sem flutuar com a temperatura do modelo. Ele
trava o **piso** de comportamento: o que é regex/heurística **tem** que funcionar
sozinho, porque é o nosso fallback quando o LLM cai.

> **Regra de ouro:** achou um furo testando o sistema? Vira um cenário aqui.
> Nunca mais regride. É assim que a suíte cresce de verdade — com bug real.

Rodar: `pytest tests/scenarios/ -q`

### Camada 2 — Eval com LLM-juiz (red-team periódico) 🔜 scaffold

`tests/scenarios/live_eval.py` — roda **com o LLM real** e usa um **segundo LLM
como juiz** ("LLM-as-judge") pra avaliar respostas que não dá pra checar por
igualdade exata: a resposta é educada? recusou o que tinha que recusar? não
vazou provider? não entrou em loop? Não roda no CI (custa token, é não-determinístico)
— roda sob demanda / agendado (ex.: nightly).

Isso é o que cobre o que o regex não alcança: frases criativas, ataques novos,
naturalidade da recusa, cidade solta sem "X para Y" estruturado.

## Como as empresas de IA fazem (e o que adotamos)

| Prática de mercado | Nosso equivalente |
|---|---|
| **Golden dataset** de conversas rotuladas | `test_intake_scenarios.py` |
| **Gate de CI** determinístico (mock do modelo) | Camada 1 |
| **LLM-as-judge** pra qualidade subjetiva | Camada 2 (`live_eval.py`) |
| **Red-teaming** / adversarial | `test_safety_scenarios.py` + red-team da Camada 2 |
| **Regression suite** (todo bug vira teste) | "achou furo → vira cenário" |
| **Guardrails** in/out + auditoria | `chat/security/*` + `GUARDRAILS.md` |
| **Canary / shadow** antes de 100% | (futuro — ver abaixo) |

## O "modelo de IA validador focado no nosso contexto" (plano)

> Pergunta do time: *"se tiver até algum modelo de IA de validação de chat de LLM,
> focado pro nosso contexto, seria elevar o nível."*

Sim — é a evolução natural da Camada 2. Três níveis, do mais barato pro mais robusto:

1. **Juiz por prompt (agora).** Um LLM com um **prompt-rubrica do nosso contexto**
   (atendente de passagens B2B): dada `(mensagem_do_vendedor, resposta_da_IA)`,
   devolve `{on_topic, refused_correctly, leaked_provider, looped, polite, score}`.
   Zero treino, roda em `live_eval.py`. É o que o scaffold já faz.

2. **Classificador de intenção dedicado (médio prazo).** Um modelo leve
   (ou few-shot fixo) que roda **online** antes do grafo e rotula a mensagem em
   `cotacao | saudacao | off_topic | nocivo | injecao`. Complementa o regex do
   `content_safety` pegando o que o padrão não vê — só promovido a online depois
   de validado offline com precisão/recall medidos no nosso dataset.

3. **Guarda fino-ajustado (longo prazo).** Se o volume justificar, fine-tune de um
   guard pequeno (estilo Llama Guard) **nos nossos dados rotulados** — recusas,
   bypasses reais coletados pela auditoria, cotações legítimas. Aí sim "focado no
   nosso contexto" de verdade. Pré-requisito: dataset rotulado grande o bastante
   (vem da auditoria + dos cenários).

**Caminho recomendado:** ficar no nível 1 até termos métricas, coletar exemplos
reais via `audit.log("guardrail.*")`, e só então decidir entre 2 e 3 com número na
mão. Não fine-tunar antes de ter dado.

## Métricas que importam

- **Cobertura de slot**: % de respostas-soltas (por campo) capturadas sem re-perguntar.
- **Taxa de loop**: conversas que repetem a mesma pergunta (meta: 0).
- **Recall de segurança**: % de ataques recusados (red-team).
- **Falso-positivo de segurança**: % de viagem legítima bloqueada (meta: ~0).
- **Naturalidade / educação** (juiz): nota média das recusas.

## Como contribuir com um cenário

```python
# tests/scenarios/test_intake_scenarios.py
def test_meu_furo(monkeypatch):
    r = run_intake(["a frase exata que quebrou"], monkeypatch,
                   slots0={...}, awaiting0="campo_que_perguntamos")
    assert r.slots.get("campo") == "esperado"
    assert not r.asked_twice("trecho da pergunta")  # sem loop
    assert r.reached_search
```

Rodar a Camada 1 inteira: `pytest tests/scenarios/ -q`
