"""Cidade multi-aeroporto: busca os top-2 aeroportos e junta as ofertas."""
import backend.app.ai.agents.orchestrator as orch


def test_merge_origin_searches_combines_both_airports(monkeypatch):
    def fake_run_search(*, origin, **kw):
        # VCP traz o voo barato (Azul); GRU traz o caro (LATAM).
        price = 3225 if origin == "VCP" else 5087
        return {"ok": True,
                "ranked_offers": [{"offer_id": f"{origin}-1", "price_brl": price}],
                "money_offers": [{"offer_id": f"{origin}-m", "price_brl": price}],
                "miles_offers": []}

    monkeypatch.setattr(orch, "run_search", fake_run_search)
    merged = orch._merge_origin_searches(["GRU", "VCP"], {"destination": "LIS"})
    ids = {o["offer_id"] for o in merged["ranked_offers"]}
    assert ids == {"GRU-1", "VCP-1"}                  # ambos aeroportos presentes
    prices = {o["price_brl"] for o in merged["ranked_offers"]}
    assert 3225 in prices                              # o voo barato da Azul (VCP) entrou


def test_merge_origin_searches_dedups_and_survives_failure(monkeypatch):
    def fake_run_search(*, origin, **kw):
        if origin == "GRU":
            raise RuntimeError("scrape falhou")        # um aeroporto falha
        return {"ok": True, "ranked_offers": [{"offer_id": "x", "price_brl": 100}],
                "money_offers": [], "miles_offers": []}

    monkeypatch.setattr(orch, "run_search", fake_run_search)
    merged = orch._merge_origin_searches(["GRU", "VCP"], {"destination": "LIS"})
    assert merged["ok"] is True                        # VCP salva a busca
    assert len(merged["ranked_offers"]) == 1


def test_vcp_restricted_to_azul_only(monkeypatch):
    """VCP (hub exclusivo Azul) → busca só Azul milhas + Azul Oficial cash."""
    captured = {}

    def fake_run_search(*, origin, **kw):
        captured[origin] = (kw.get("companhias"), kw.get("always_include"))
        return {"ok": True, "ranked_offers": [{"offer_id": origin}],
                "money_offers": [], "miles_offers": []}

    monkeypatch.setattr(orch, "run_search", fake_run_search)
    orch._merge_origin_searches(["GRU", "VCP"], {"destination": "LIS"})
    assert captured["VCP"] == (["AZUL"], ["AZUL_CASH"])   # só Azul
    assert captured["GRU"] == (None, None)                 # GRU busca normal (tudo)
