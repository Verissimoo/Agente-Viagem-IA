from datetime import date, timedelta
from typing import List, Tuple, Dict, Any
from pcd.core.schema import SearchRequest, UnifiedOffer, TripType

def expand_dates(base_date: date, flex_days: int) -> List[date]:
    """
    Retorna uma lista de datas expandidas em torno da data base.
    Garante que não voltará para antes de 'hoje'.
    """
    if flex_days <= 0:
        return [base_date]
    
    dates = []
    today = date.today()
    for i in range(-flex_days, flex_days + 1):
        dt = base_date + timedelta(days=i)
        if dt >= today:
            dates.append(dt)
            
    return sorted(list(set(dates)))

def build_date_plan(request: SearchRequest) -> List[SearchRequest]:
    """
    Gera uma lista de requests clonados baseados na flexibilidade.
    """
    # Limites rígidos de segurança
    flex = min(request.flex_days or 0, 3)
    
    depart_dates = expand_dates(request.date_start, flex)
    
    # Se flex_return=True e for Roundtrip, expandimos volta também
    # mas limitamos rigorosamente para evitar (2N+1)^2
    if request.trip_type == TripType.ROUNDTRIP and request.flex_return and request.return_start:
        # Limite N=2 se flex_return
        flex_r = min(flex, 2)
        return_dates = expand_dates(request.return_start, flex_r)
        
        plan = []
        for d in depart_dates:
            for r in return_dates:
                # Regra: volta deve ser >= ida
                if r >= d:
                    new_req = request.model_copy()
                    new_req.date_start = d
                    new_req.date_end = d
                    new_req.return_start = r
                    new_req.return_end = r
                    plan.append(new_req)
        return plan
    else:
        # Apenas expande ida
        plan = []
        for d in depart_dates:
            new_req = request.model_copy()
            new_req.date_start = d
            new_req.date_end = d
            # Se for RT, mantém a volta fixa conforme original
            plan.append(new_req)
        return plan

def compute_best_day(offers: List[UnifiedOffer]) -> Tuple[date, float, str, Dict[str, float], Dict[str, int]]:
    """
    Analisa as ofertas e identifica o melhor dia para viajar.
    Retorna: (best_date, best_value, source, date_best_map, offers_by_date)
    """
    if not offers:
        return None, 0.0, "", {}, {}
    
    date_best_map = {}
    offers_by_date = {}
    
    for o in offers:
        # Usar a data do primeiro segmento da ida
        d_str = o.outbound.segments[0].departure_dt.date().isoformat()
        val = o.equivalent_brl or 0.0
        
        if d_str not in date_best_map or val < date_best_map[d_str]:
            date_best_map[d_str] = val
            
        offers_by_date[d_str] = offers_by_date.get(d_str, 0) + 1
        
    # Identificar o melhor dia absoluto
    best_date_str = min(date_best_map, key=date_best_map.get)
    best_val = date_best_map[best_date_str]
    best_date = date.fromisoformat(best_date_str)
    
    # Encontrar a fonte do melhor dia
    # (Poderia haver empate de dias, pegamos o primeiro que bate o valor no mapa)
    best_source = ""
    for o in offers:
        if o.outbound.segments[0].departure_dt.date() == best_date and o.equivalent_brl == best_val:
            best_source = o.source.value
            break
            
    return best_date, best_val, best_source, date_best_map, offers_by_date
