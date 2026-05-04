from typing import List, Tuple
from pcd.core.schema import UnifiedOffer, LayoverCategory
from pcd.core.layover_classifier import classify_offer
from pcd.core.conversion import offer_equivalent_brl

def rank_offers(offers: List[UnifiedOffer], top_n: int = 5) -> Tuple[List[UnifiedOffer], UnifiedOffer, List[str]]:
    """
    Compara ofertas financeiras e em milhas, aplicando custo fictício da milha e penalidades
    por conexão para encontrar a melhor opção.

    Returns:
       (top_n_offers, best_offer, justifications)
    """

    if not offers:
        return [], None, ["Nenhuma oferta encontrada."]

    scored_offers = []

    for offer in offers:
        # Garante a classificação do layover (necessário para filtros UI)
        classify_offer(offer)

        if offer.price_brl is None and offer.miles is None:
            continue

        # Custo equivalente em BRL via tabela única (pcd/core/conversion.py)
        offer.equivalent_brl = offer_equivalent_brl(offer)
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
