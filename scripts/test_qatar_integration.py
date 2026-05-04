import sys
from datetime import date
from pathlib import Path

# Adiciona o diretório raiz ao path
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pcd.run import run_pipeline
from pcd.core.schema import SourceType

def test_qatar_researcher():
    print("=== Testando Pesquisador Qatar (McpQatarAdapter) ===")
    
    # Rota onde sabemos que a Qatar opera
    prompt = "GRU para DOH ida 2026-08-25"
    
    # Executa o pipeline ativando apenas a Qatar
    res = run_pipeline(
        prompt=prompt,
        origin="GRU",
        destination="DOH",
        date_start=date(2026, 8, 25),
        companhias=["QATAR"],
        use_fixtures=False # Busca real para validar o integrador
    )
    
    print(f"\nResultados encontrados: {len(res.miles_offers)}")
    
    for o in res.miles_offers:
        print(f"\nOferta: {o.airline}")
        print(f"  Source: {o.source}")
        print(f"  Milhas: {o.miles} ({o.miles_program})")
        print(f"  Taxas : R$ {o.taxes_brl:.2f}")
        print(f"  Eq BRL: R$ {o.equivalent_brl:.2f}")
        
        if o.source == SourceType.MCP_QATAR:
            print("  [OK] SourceType.MCP_QATAR identificado corretamente!")
        else:
            print(f"  [AVISO] SourceType esperado MCP_QATAR, veio {o.source}")

if __name__ == "__main__":
    test_qatar_researcher()
