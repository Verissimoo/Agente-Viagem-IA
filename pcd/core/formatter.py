from typing import List, Dict, Tuple, Any
from pcd.core.schema import UnifiedOffer, LayoverCategory

def format_duration(minutes: int) -> str:
    if not minutes:
        return ""
    h = minutes // 60
    m = minutes % 60
    if h == 0:
        return f"{m}m"
    if m == 0:
        return f"{h}h"
    return f"{h}h {m}m"

def _format_offer_row(offer: UnifiedOffer) -> Dict[str, Any]:
    # Check type of layover
    is_direct = offer.layover_out == LayoverCategory.DIRECT
    if offer.inbound:
        is_direct = is_direct and offer.layover_in == LayoverCategory.DIRECT
        
    layover_str = "Direto" if is_direct else "Com Conexão"
    
    # Check times
    dur_out = offer.outbound.duration_min if offer.outbound else 0
    dur_in = offer.inbound.duration_min if offer.inbound else 0
    total_dur = (dur_out or 0) + (dur_in or 0)
    
    # Financials
    price_str = f"R$ {offer.price_brl:.2f}" if offer.price_brl is not None else f"{offer.miles} milhas"
    taxes_str = f"R$ {offer.taxes_brl:.2f}" if offer.taxes_brl is not None else ""
    eq_brl_str = f"R$ {offer.equivalent_brl:.2f}" if offer.equivalent_brl is not None else ""
    
    return {
        "fonte": offer.source.value,
        "companhia": offer.airline,
        "tipo_viagem": offer.trip_type.value,
        "escalas": layover_str,
        "duracao_total": format_duration(total_dur),
        "preco_base": price_str,
        "taxas": taxes_str,
        "equivalente_brl": eq_brl_str,
        "link": offer.deeplink
    }

def build_ui_report(
    top_offers: List[UnifiedOffer], 
    best_offer: UnifiedOffer, 
    justifications: List[str]
) -> Tuple[Dict[str, Any], str]:
    """
    Recebe os resultados do ranking e converte num JSON de tabela 
    e num texto amigável.
    """
    
    # 1. Report JSON
    rows = [_format_offer_row(o) for o in top_offers]
    report_json = {
        "ranked_offers": rows,
        "justifications": justifications
    }
    
    # 2. Resumo Textual
    lines = ["✈️  Resumo das Melhores Opções de Voo"]
    lines.append("=" * 40)
    
    if justifications:
        lines.append("💡 Por que esta é a melhor opção?")
        for j in justifications:
            lines.append(f"  • {j}")
            
    lines.append("-" * 40)
    lines.append("🏆 Top Ofertas Encontradas:")
    
    for i, row in enumerate(rows, 1):
        lines.append(
            f"{i}. {row['companhia']} ({row['fonte']}) - {row['tipo_viagem'].upper()}"
        )
        lines.append(
            f"   {row['preco_base']} | Taxas: {row['taxas'] or 'Isento'} | Eqv: {row['equivalente_brl']} | {row['escalas']} | {row['duracao_total']}"
        )
        
    report_text = "\n".join(lines)
    
    return report_json, report_text
