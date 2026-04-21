from datetime import date
from pcd.run import run_pipeline

def test_range_flexibility():
    prompt = "Brasília para São Paulo entre 19/10/2026 e 21/10/2026"
    print(f"--- Iniciando teste de Range: {prompt} ---")
    
    # Simulando os parâmetros que o Streamlit passaria
    res = run_pipeline(
        prompt=prompt,
        top_n=5,
        use_fixtures=False, # Usando Kayak real (Buscamilhas está desativado no run.py)
        date_start=date(2026, 10, 19),
        date_end=date(2026, 10, 21),
        flex_mode="range"
    )
    
    print(f"\n--- Resultados Consolidados ---")
    money_count = len(res.money_offers)
    miles_count = len(res.miles_offers)
    print(f"Total de ofertas em dinheiro: {money_count}")
    print(f"Total de ofertas em milhas: {miles_count}")
    
    dates_found = {}
    for o in res.money_offers + res.miles_offers:
        d = o.outbound.segments[0].departure_dt.date().isoformat()
        src = o.source.value
        if d not in dates_found:
            dates_found[d] = set()
        dates_found[d].add(src)
    
    for d in sorted(dates_found.keys()):
        sources = ", ".join(sorted(list(dates_found[d])))
        print(f" - {d}: [{sources}]")

if __name__ == "__main__":
    test_range_flexibility()
