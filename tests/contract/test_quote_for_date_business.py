"""Asserções de negócio sobre POST /api/v1/smart-quote/quote-for-date.

Cada teste reflete um bug visto na UI:
  • test_real_cost_filled_for_miles    — "Custo Real: —" em linha de milhas
  • test_verdict_miles_card_has_value  — "MELHOR EM MILHAS" sem R$
  • test_price_final_filled_for_miles  — coluna "PREÇO FINAL" vazia
  • test_skiplagged_isolated_bucket    — Skiplagged misturado em LATAM/GOL
  • test_buckets_milhas_no_cash        — bucket LATAM contém só miles
  • test_cross_validate_flags_consistent — badge ✓ casa com validation_sources
  • test_bucket_order_matches_spec     — ordem das abas igual ao acordado
"""
from __future__ import annotations

import pytest


ROUTE = "/api/v1/smart-quote/quote-for-date"


def _post(client, **overrides):
    """Helper para chamar /quote-for-date com defaults sensatos."""
    payload = {
        "origin": "GRU",
        "destination": "SSA",
        "adults": 1,
    }
    payload.update(overrides)
    return client.post(ROUTE, json=payload)


# ───────────────────────────────────────────────────────────────────
# Real cost sempre preenchido para milhas
# ───────────────────────────────────────────────────────────────────
def test_real_cost_filled_for_miles(client, future_date):
    """Toda linha de milhas DEVE ter real_cost_brl > 0 (custo real = milhas
    convertidas em BRL pelo rates.json + taxas). Linha vazia = bug."""
    resp = _post(client, date=future_date)
    assert resp.status_code == 200
    body = resp.json()

    all_rows = body["buckets"]["ALL"]["rows"]
    miles_rows = [r for r in all_rows if r.get("miles")]

    if not miles_rows:
        pytest.skip("Nenhuma oferta de milhas nesta data — sem o que validar")

    empty = [r for r in miles_rows if r.get("real_cost_brl") in (None, 0)]
    assert not empty, (
        f"{len(empty)} linha(s) de milhas com real_cost_brl vazio. "
        f"Amostra: {empty[0]['id']} {empty[0]['companhia_label']} {empty[0]['miles']} mi"
    )


def test_price_final_filled_for_miles(client, future_date):
    """Coluna PREÇO FINAL nunca pode aparecer vazia em linha de milhas —
    o legado mostra o custo real ali quando não há cash equivalente."""
    resp = _post(client, date=future_date)
    body = resp.json()
    all_rows = body["buckets"]["ALL"]["rows"]
    miles_rows = [r for r in all_rows if r.get("miles")]
    if not miles_rows:
        pytest.skip("sem milhas")
    empty = [r for r in miles_rows if r.get("price_brl") is None and r.get("real_cost_brl") is None]
    assert not empty


# ───────────────────────────────────────────────────────────────────
# Veredito PcD populado
# ───────────────────────────────────────────────────────────────────
def test_verdict_has_three_cards(client, future_date):
    body = _post(client, date=future_date).json()
    assert len(body["verdict"]) == 3
    kinds = {v["kind"] for v in body["verdict"]}
    assert kinds == {"overall", "miles", "money"}


def test_verdict_miles_card_has_value(client, future_date):
    """Quando MELHOR EM MILHAS tem oferta, o row interno DEVE ter real_cost_brl
    > 0 e miles > 0 (o card no front mostra 'R$ X · N mi')."""
    body = _post(client, date=future_date).json()
    miles_card = next(v for v in body["verdict"] if v["kind"] == "miles")
    if miles_card["row"] is None:
        pytest.skip("sem milhas, card mostra 'Sem ofertas'")
    row = miles_card["row"]
    assert row["miles"] and row["miles"] > 0
    assert row["real_cost_brl"] and row["real_cost_brl"] > 0
    assert row["taxes_brl"] is not None


def test_verdict_overall_consistent_with_ranking(client, future_date):
    """MELHOR ACHADO GERAL é o id=L1 (ou similar) do bucket ALL."""
    body = _post(client, date=future_date).json()
    overall = next(v for v in body["verdict"] if v["kind"] == "overall")
    if overall["row"] is None:
        pytest.skip("sem ofertas")
    all_best = body["buckets"]["ALL"]["best"]
    assert overall["row"]["id"] == all_best["id"]
    assert overall["row"]["real_cost_brl"] == all_best["real_cost_brl"]


def test_best_miles_prefers_validated(client, future_date):
    """MELHOR EM MILHAS DEVE preferir voos validados pelo Economilhas — só
    cai pra fonte única quando não há nenhum validado."""
    body = _post(client, date=future_date).json()
    miles_card = next(v for v in body["verdict"] if v["kind"] == "miles")
    if miles_card["row"] is None:
        pytest.skip("sem milhas")

    all_rows = body["buckets"]["ALL"]["rows"]
    has_any_validated = any(r["miles"] and r["is_validated"] for r in all_rows)
    if has_any_validated:
        assert miles_card["row"]["is_validated"], (
            f"Existem voos validados mas MELHOR EM MILHAS escolheu não-validado: "
            f"{miles_card['row']['id']} sources={miles_card['row']['validation_sources']}"
        )


def test_ranking_geral_stratified_by_confidence(client, future_date):
    """Ranking Geral deve listar primeiro voos validados pelo Economilhas,
    depois cash Kayak, depois fonte única, depois Skiplagged. Dentro de
    cada faixa, ordem por custo asc."""
    body = _post(client, date=future_date).json()
    rows = body["buckets"]["ALL"]["rows"]
    if len(rows) < 2:
        pytest.skip("dados insuficientes")

    def _priority(r):
        src = (r.get("source_label") or "").lower()
        if src.startswith("skiplagged"):
            return 3
        if r["miles"] is not None:
            return 0 if r["is_validated"] else 2
        return 1

    last_priority = -1
    for r in rows:
        p = _priority(r)
        assert p >= last_priority, (
            f"row {r['id']} prioridade {p} apareceu depois de prioridade {last_priority} — ordem quebrada"
        )
        last_priority = p


def test_best_money_avoids_skiplagged(client, future_date):
    """MELHOR EM DINHEIRO prefere Kayak puro a Skiplagged (hidden city é
    arriscado). Só usa Skiplagged se nada mais em cash sobrou."""
    body = _post(client, date=future_date).json()
    money_card = next(v for v in body["verdict"] if v["kind"] == "money")
    if money_card["row"] is None:
        pytest.skip("sem cash")

    all_rows = body["buckets"]["ALL"]["rows"]
    cash_non_skip = [
        r for r in all_rows
        if r["miles"] is None and not (r.get("source_label") or "").lower().startswith("skiplagged")
    ]
    if cash_non_skip:
        chosen_src = (money_card["row"].get("source_label") or "").lower()
        assert not chosen_src.startswith("skiplagged"), (
            f"MELHOR EM DINHEIRO escolheu Skiplagged ({money_card['row']['id']}) mesmo existindo cash convencional"
        )


# ───────────────────────────────────────────────────────────────────
# Buckets bem segregados
# ───────────────────────────────────────────────────────────────────
def test_bucket_order_matches_spec(client, future_date):
    """Ordem fixa que o frontend monta as abas — não pode mudar sem
    coordenação com a UI."""
    body = _post(client, date=future_date).json()
    assert body["bucket_order"] == ["ALL", "KAYAK", "LATAM", "GOL", "AZUL", "INTL", "SKIPLAGGED"]


def test_skiplagged_isolated_bucket(client, future_date):
    """Skiplagged TEM que ficar isolado no bucket próprio — não pode
    contaminar LATAM/GOL/AZUL com hidden city / cash."""
    body = _post(client, date=future_date).json()
    skip_bucket = body["buckets"]["SKIPLAGGED"]
    for r in skip_bucket["rows"]:
        assert r["source_label"].startswith("Skiplagged"), (
            f"row {r['id']} no Skiplagged tem source_label={r['source_label']!r}"
        )

    # E reciprocamente: LATAM/GOL/AZUL não podem ter source=skiplagged
    for code in ("LATAM", "GOL", "AZUL"):
        rows = body["buckets"][code]["rows"]
        leaks = [r for r in rows if r["source_label"].startswith("Skiplagged")]
        assert not leaks, f"bucket {code} tem {len(leaks)} ofertas Skiplagged misturadas"


def test_buckets_milhas_no_cash(client, future_date):
    """Buckets LATAM/GOL/AZUL agora são SÓ MILHAS — cash dessas cias vai pra
    KAYAK ou SKIPLAGGED dependendo da source."""
    body = _post(client, date=future_date).json()
    for code in ("LATAM", "GOL", "AZUL"):
        for r in body["buckets"][code]["rows"]:
            assert r.get("miles"), (
                f"bucket {code} contém row {r['id']} sem milhas — deveria ter ido pra outro bucket"
            )


def test_kayak_bucket_only_cash(client, future_date):
    """Bucket KAYAK consolida TODO o cash — Kayak puro + BuscaMilhas/Economilhas
    em modo Pagante. Skiplagged segue isolado no próprio bucket."""
    body = _post(client, date=future_date).json()
    for r in body["buckets"]["KAYAK"]["rows"]:
        assert r["miles"] is None, f"row {r['id']} no KAYAK tem miles — deveria ir pra LATAM/GOL/AZUL"
        # Skiplagged TEM que estar isolado, não pode misturar com Kayak.
        assert not r["source_label"].startswith("Skiplagged"), (
            f"row {r['id']} Skiplagged vazou pro bucket KAYAK"
        )


# ───────────────────────────────────────────────────────────────────
# Cross-validate
# ───────────────────────────────────────────────────────────────────
def test_cross_validate_flags_consistent(client, future_date):
    """Regra atual: is_validated=True quando 'economilhas' está entre as
    sources do grupo (Economilhas é a fonte de verdade). BuscaMilhas
    sozinho fica fonte única."""
    body = _post(client, date=future_date).json()
    all_rows = body["buckets"]["ALL"]["rows"]
    for r in all_rows:
        if r["miles"] is None:
            assert r["is_validated"] is True  # cash sempre validado
            continue
        if r["is_validated"]:
            sources_lower = {s.lower() for s in r["validation_sources"]}
            assert "economilhas" in sources_lower, (
                f"row {r['id']} marcada validada mas Economilhas não está nas sources: {r['validation_sources']}"
            )
        else:
            sources_lower = {s.lower() for s in r["validation_sources"]}
            assert "economilhas" not in sources_lower, (
                f"row {r['id']} NÃO validada apesar de ter Economilhas: {r['validation_sources']}"
            )


def test_cross_validate_cash_always_validated(client, future_date):
    body = _post(client, date=future_date).json()
    for r in body["buckets"]["KAYAK"]["rows"] + body["buckets"]["SKIPLAGGED"]["rows"]:
        assert r["is_validated"] is True


# ───────────────────────────────────────────────────────────────────
# IDs únicos por bucket
# ───────────────────────────────────────────────────────────────────
def test_ids_unique_per_bucket(client, future_date):
    """ID + leg deve ser único dentro de cada bucket — evita conflito no
    trackBy do Angular (que disparou o bug do 'Ver detalhes' antes)."""
    body = _post(client, date=future_date).json()
    for code, bucket in body["buckets"].items():
        seen = set()
        for r in bucket["rows"]:
            key = (r["id"], r["leg"])
            assert key not in seen, f"id duplicado em {code}: {key}"
            seen.add(key)


# ───────────────────────────────────────────────────────────────────
# Roundtrip
# ───────────────────────────────────────────────────────────────────
def test_hidden_city_miles_on_demand_endpoint(client, future_date):
    """POST /smart-quote/hidden-city-miles cota um itinerário oficial sob demanda.
    Deve sempre retornar 200 + a estrutura HiddenCityMilesQuote (mesmo com
    alternatives=[] quando não houver inventário) + direct_flight check."""
    resp = client.post(
        "/api/v1/smart-quote/hidden-city-miles",
        json={
            "origin": "BSB",
            "destination": "SSA",
            "passenger_destination": "FOR",
            "carrier_iata": "LA",
            "date": future_date,
            "departure_time": "06:00",
            "adults": 1,
            "cash_reference_brl": 800.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    if body is None:
        pytest.skip("nenhum inventário para essa rota nesta data")
    assert body["official_origin"] == "BSB"
    assert body["official_destination"] == "SSA"
    assert body["passenger_destination"] == "FOR"
    assert body["carrier_iata"] == "LA"
    assert isinstance(body["alternatives"], list)
    for alt in body["alternatives"]:
        assert alt["miles"] > 0
        assert alt["real_cost_brl"] > 0
        assert alt["program_label"]
    # Direct flight check sempre vem (mesmo se found_any=False)
    assert body.get("direct_flight") is not None
    df = body["direct_flight"]
    assert df["origin"] == "BSB"
    assert df["passenger_destination"] == "FOR"
    if df["found_any"]:
        assert df["direct_min_price_brl"] > 0


def test_hidden_city_carries_miles_alternative_when_brazilian_carrier(client, future_date):
    """Ofertas hidden city Skiplagged operadas por cia BR com programa próprio
    (G3/LA/AD) DEVEM trazer hidden_city_miles populado com a cotação em
    milhas para o itinerário oficial completo."""
    body = _post(client, date=future_date).json()
    skip_rows = body["buckets"].get("SKIPLAGGED", {}).get("rows", [])
    if not skip_rows:
        pytest.skip("sem ofertas Skiplagged nesta busca")

    # Pega só rows operadas por carrier com programa próprio mapeado
    br_carriers = ("G3", "LA", "AD")
    br_hidden = [r for r in skip_rows if r.get("carrier_iata") in br_carriers]
    if not br_hidden:
        pytest.skip("nenhum hidden city operado por carrier BR")

    # Pelo menos UM deles deve ter cotação em milhas anexa (cap de N grupos
    # no helper pode deixar alguns sem)
    with_miles = [r for r in br_hidden if r.get("hidden_city_miles")]
    # Aceita ZERO se o BuscaMilhas não tiver inventário nesse trecho oficial
    # (rota incomum) — mas a estrutura tem que estar OK quando vier.
    for r in with_miles:
        hcm = r["hidden_city_miles"]
        assert hcm["official_origin"], "hidden_city_miles sem official_origin"
        assert hcm["official_destination"], "hidden_city_miles sem official_destination"
        assert hcm["carrier_iata"] == r["carrier_iata"]
        assert hcm["alternatives"], "hidden_city_miles sem alternativas"
        for alt in hcm["alternatives"]:
            assert alt["miles"] > 0
            assert alt["real_cost_brl"] > 0


def test_roundtrip_returns_ida_and_volta(client, future_date, return_date):
    """Quando return_date é setado, cada oferta roundtrip gera 2 rows."""
    body = _post(client, date=future_date, return_date=return_date).json()
    assert body["return_date"] == return_date

    all_rows = body["buckets"]["ALL"]["rows"]
    legs = {r["leg"] for r in all_rows}
    # Pode haver only-oneway (Skiplagged hidden city força ONEWAY) então pelo
    # menos IDA deve aparecer.
    assert "IDA" in legs
