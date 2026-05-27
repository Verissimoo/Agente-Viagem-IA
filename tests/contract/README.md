# Contract tests

Asserções de **regras de negócio** sobre as respostas da API — não testam
implementação interna, testam o que o frontend espera do JSON.

Pra cada bug que aparecer na UI ("custo real vazio", "milhas sem preço",
"badge ✓ sumiu") adicione um teste aqui que falharia se o backend
regredir.

## Como rodar
```bash
pytest tests/contract/ -v
```

Todos os testes nesse diretório usam `TestClient` do FastAPI e fazem chamadas
HTTP reais ao backend. Eles podem demorar 1-3 min cada (até ~10s contra
provedores externos) — não rodam por default no CI rápido, só no full.

Para rodar sem hits externos, use fixtures (passe `use_fixtures=True` no payload).

## Estrutura
- `test_quote_for_date_business.py` — invariantes do Phase 2 (Veredito,
  Ranking, Buckets, cross-validate, real_cost preenchido sempre que há milhas).
- `test_explore_business.py` — Phase 1 (calendário só Kayak, trio de cards, stability).
- `test_buscamilhas_extraction.py` — campos obrigatórios extraídos do BuscaMilhas
  (carrier IATA real, miles, taxes, flight_number, dep/arr DT).
