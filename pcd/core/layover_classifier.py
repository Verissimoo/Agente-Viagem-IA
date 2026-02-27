from typing import List

from pcd.core.schema import UnifiedOffer, LayoverCategory

def classify_offer(offer: UnifiedOffer) -> UnifiedOffer:
    """
    Função pura que classifica o layover_category de uma offer (Outbound e Inbound)
    com base no número de stops (ou deduzindo de len(segments)-1).
    """
    # Outbound
    stops_out = offer.stops_out
    if stops_out is None and offer.outbound is not None:
        stops_out = offer.outbound.stops
        
    if stops_out is not None:
        offer.layover_out = LayoverCategory.CONNECTION if stops_out >= 1 else LayoverCategory.DIRECT

    # Inbound
    if offer.inbound is not None:
        stops_in = offer.stops_in
        if stops_in is None:
            stops_in = offer.inbound.stops
            
        if stops_in is not None:
            offer.layover_in = LayoverCategory.CONNECTION if stops_in >= 1 else LayoverCategory.DIRECT

    # A validação do schema.py já cuida de setar o stops_out e stops_in pelo @model_validator,
    # caso as props venham None de quem instanciou. 
    # Nossa classificação puramente força o LayoverCategory baseado nos valores mais atuais.
    
    return offer

def classify_many(offers: List[UnifiedOffer]) -> List[UnifiedOffer]:
    """
    Aplica a classificação para uma lista de ofertas.
    """
    return [classify_offer(o) for o in offers]
