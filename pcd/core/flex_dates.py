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

def compute_search_dates(
    mode: str, 
    base_date: date, 
    flex_days: int = 0, 
    range_start: date = None, 
    range_end: date = None
) -> List[date]:
    """
    Retorna a lista exata de datas a serem pesquisadas baseado no modo.
    Guardrail: Máximo 15 datas.
    """
    dates = []
    
    if mode == "range" and range_start and range_end:
        start = range_start
        end = range_end
        if end < start:
            end = start
        
        diff = (end - start).days
        if diff > 14: # Guardrail 15 dias
            end = start + timedelta(days=14)
            diff = 14
        
        dates = [start + timedelta(days=i) for i in range(diff + 1)]
        
    elif mode == "plusminus" or (flex_days > 0 and mode == "none"):
        # Se flex_days estiver presente mas modo for none (config manual antiga), assume plusminus
        flex = min(flex_days or 0, 3) # Limite de ±3
        dates = expand_dates(base_date, flex)
        
    else:
        dates = [base_date]
        
    return sorted(list(set(dates)))

def build_date_plan(request: SearchRequest) -> List[SearchRequest]:
    """
    Gera uma lista de requests clonados baseados na flexibilidade.
    Suporta os modos: none, plusminus, range.
    """
    plan = []
    
    # Obter a lista de datas centralizada
    depart_dates = compute_search_dates(
        mode=request.flex_mode,
        base_date=request.date_start,
        flex_days=request.flex_days,
        range_start=request.date_start, # No modo range, date_start é o início
        range_end=request.date_end      # No modo range, date_end é o fim
    )

    # Gerar plano baseado nas datas de ida encontradas
    for d in depart_dates:
        # Se flex_return=True e for Roundtrip, expandimos volta também (apenas para plusminus por simplicidade de custo)
        if request.trip_type == TripType.ROUNDTRIP and request.flex_return and request.return_start and request.flex_mode == "plusminus":
            flex_r = min(request.flex_days or 0, 2)
            return_dates = expand_dates(request.return_start, flex_r)
            for r in return_dates:
                if r >= d:
                    new_req = request.model_copy()
                    new_req.date_start = d
                    new_req.date_end = d
                    new_req.return_start = r
                    new_req.return_end = r
                    plan.append(new_req)
        else:
            new_req = request.model_copy()
            new_req.date_start = d
            new_req.date_end = d
            # Se for RT, mantém a volta fixa original
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
