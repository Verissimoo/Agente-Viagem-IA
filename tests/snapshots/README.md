# Snapshots de inspeção

Capturas reais da resposta da API para inspeção visual offline.
Útil quando você quer ver o que o backend está mandando pro front
sem subir uvicorn nem abrir o navegador.

## Como capturar

```bash
# Captura uma rota específica
python tests/snapshots/capture.py quote-for-date GRU SSA --date 2026-06-15
python tests/snapshots/capture.py quote-for-date LIS MAD --date 2026-06-15 --return 2026-06-22
python tests/snapshots/capture.py explore GRU SSA --date 2026-06-15 --flex 3

# Captura todas as rotas com presets
python tests/snapshots/capture.py all
```

Arquivos vão pra `fixtures/<rota>__<origin>-<destination>__<date>.json`.

## Como inspecionar

```bash
# Resumo legível: buckets, veredito, validação cruzada
python tests/snapshots/view.py fixtures/quote-for-date__GRU-SSA__2026-06-15.json

# Mostrar campos vazios suspeitos
python tests/snapshots/view.py fixtures/quote-for-date__GRU-SSA__2026-06-15.json --check-empty

# Mostrar voos não validados (1 fonte só)
python tests/snapshots/view.py fixtures/quote-for-date__GRU-SSA__2026-06-15.json --unvalidated-only
```

## Workflow recomendado

1. Mudou backend → `python tests/snapshots/capture.py all` para regerar.
2. Inspeciona o que mudou → `view.py` ou `git diff` nos JSONs.
3. Sobe a UI e confere que bate visualmente.

Os fixtures **NÃO** vão pro git (são grandes e expirantes) — `.gitignore`
ignora `tests/snapshots/fixtures/*.json`.
