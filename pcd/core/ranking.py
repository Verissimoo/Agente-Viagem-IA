import os
from typing import List, Tuple
from pcd.core.schema import UnifiedOffer, LayoverCategory, SourceType
from pcd.core.layover_classifier import classify_offer

def rank_offers(offers: List[UnifiedOffer], top_n: int = 5) -> Tuple[List[UnifiedOffer], UnifiedOffer, List[str]]:
    """
    Compara ofertas financeiras e em milhas, aplicando custo fictício da milha e penalidades 
    por conexão para encontrar a melhor opção.
    
    Returns:
       (top_n_offers, best_offer, justifications)
    """
    
    if not offers:
        return [], None, ["Nenhuma oferta encontrada."]

    try:
        cpm = float(os.getenv("COST_PER_MILE_BRL", "0.0285"))
    except ValueError:
        cpm = 0.0285
        
    scored_offers = []
    LATAM_COST_PER_MILE_BRL = 0.0285

    for offer in offers:
        # Garante a classificação do layover (necessário para filtros UI)
        classify_offer(offer)
        
        # 1. Base cost (Miles vs Currency)
        if offer.price_brl is not None:
            base_cost = offer.price_brl
        elif offer.miles is not None:
            taxes = offer.taxes_brl or 0.0
            
            # Regra Fixa LATAM: Se for LATAM, ignorar config geral e usar 0.0285
            current_cpm = LATAM_COST_PER_MILE_BRL if offer.source == SourceType.MOBLIX_LATAM else cpm
            
            base_cost = (offer.miles * current_cpm) + taxes
        else:
            continue
            
        # Salva o valor final consolidado (sem penalidades)
        offer.equivalent_brl = base_cost
        scored_offers.append(offer)

    # Ordenar ASC
    scored_offers.sort(key=lambda x: x.equivalent_brl)
    
    top = scored_offers[:top_n]
    best = top[0] if top else None
    
    justifications = []
    if best:
        is_miles = best.miles is not None and best.price_brl is None
        val_type = "milhas" if is_miles else "dinheiro"
        
        justifications.append(f"A melhor oferta encontrada foi em {val_type} voando {best.airline}.")
        
        if is_miles:
            justifications.append(f"O custo equivalente do milheiro (R$ 0,0285 para LATAM) somado às taxas ficou em R$ {best.equivalent_brl:.2f}.")
        else:
            justifications.append(f"O valor total é de R$ {best.equivalent_brl:.2f}.")
            
        if best.layover_out == LayoverCategory.DIRECT and (not best.inbound or best.layover_in == LayoverCategory.DIRECT):
            justifications.append("Este itinerário é composto apenas por voos diretos.")
        else:
            justifications.append("Este itinerário inclui conexões.")

    return top, best, justifications
