import re
import json
import os
from datetime import date, timedelta
from typing import Optional, Dict, Any
from litellm import completion
from pcd.core.schema import ParsedIntent, TripType, CabinClass
from miles_app.iata_resolver import resolve_place_to_codes

GROQ_MODEL = "groq/llama-3.3-70b-versatile"

def _extract_dates(text: str) -> tuple[Optional[date], Optional[date]]:
    """Extrai datas em formatos comuns (dd/mm/aaaa, dd/mm/yy, etc)"""
    # Regex para dd/mm/aaaa ou dd/mm/yy
    date_pattern = r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})"
    matches = re.findall(date_pattern, text)
    
    dates = []
    today = date.today()
    
    for m in matches:
        try:
            d, m, y = map(int, m)
            if y < 100:
                y += 2000
            dates.append(date(y, m, d))
        except ValueError:
            continue
    
    # Ordenar datas para assumir ida e volta
    dates.sort()
    
    depart = dates[0] if dates else None
    return_dt = dates[1] if len(dates) > 1 else None
    
    return depart, return_dt

def _resolve_iata(city_name: str) -> Optional[str]:
    try:
        codes = resolve_place_to_codes(city_name)
        return codes[0] if codes else None
    except Exception:
        return None

def parse_intent_regex(text: str) -> ParsedIntent:
    """Fallback usando Regex e heurísticas básicas"""
    text_lower = text.lower()
    
    intent = ParsedIntent()
    
    # 1. Datas
    intent.date_start, intent.date_return = _extract_dates(text)
    
    # 2. Trip Type
    if any(k in text_lower for k in ["ida e volta", "volta dia", "retorno", "roundtrip"]):
        intent.trip_type = TripType.ROUNDTRIP
    else:
        intent.trip_type = TripType.ONEWAY
        
    # 3. Origem / Destino
    # Padrão "de X para Y" ou "X para Y"
    route_match = re.search(r"(?:de\s+)?(.+?)\s+(?:para|->|to)\s+(.+?)(?:\s+ida|\s+dia|\s+volta|\s+em|\s*$)", text_lower)
    if route_match:
        origin_raw = route_match.group(1).strip()
        dest_raw = route_match.group(2).strip()
        
        intent.origin_city = origin_raw
        intent.origin_iata = _resolve_iata(origin_raw)
        
        intent.destination_city = dest_raw
        intent.destination_iata = _resolve_iata(dest_raw)
    
    # 4. Outros
    if "direto" in text_lower or "sem escala" in text_lower:
        intent.direct_only = True
        
    if "executiva" in text_lower or "business" in text_lower:
        intent.cabin = CabinClass.BUSINESS
    elif "primeira" in text_lower or "first" in text_lower:
        intent.cabin = CabinClass.FIRST
        
    # Detectar adultos
    adult_match = re.search(r"(\d+)\s+adulto", text_lower)
    if adult_match:
        intent.adults = int(adult_match.group(1))
        
    intent.confidence = 0.5
    intent.notes = "Extraído via REGEX (Fallback)"
    
    return intent

def parse_intent_ptbr(text: str, use_llm: bool = False) -> ParsedIntent:
    """Função principal de parsing"""
    if use_llm and os.getenv("GROQ_API_KEY"):
        try:
            prompt = f"""
            Você é um assistente de viagens especialista em extrair dados de voos em Português (Brasil).
            Extraia as seguintes informações do texto do usuário:
            - origin_city
            - origin_iata (3 letras)
            - destination_city
            - destination_iata (3 letras)
            - trip_type ("oneway" ou "roundtrip")
            - date_start (YYYY-MM-DD)
            - date_return (YYYY-MM-DD ou null)
            - adults (número inteiro)
            - cabin ("economy", "business" ou "first")
            - direct_only (booleano)

            Texto do Usuário: "{text}"
            Data de Hoje: {date.today().isoformat()}

            Responda APENAS com um JSON puro, sem explicações.
            """
            
            response = completion(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            data = json.loads(content)
            
            # Converter datas de string para objects
            if data.get("date_start"):
                data["date_start"] = date.fromisoformat(data["date_start"])
            if data.get("date_return"):
                data["date_return"] = date.fromisoformat(data["date_return"])
            
            # Mapear Cabin
            if data.get("cabin"):
                cab = data["cabin"].lower()
                if "bus" in cab: data["cabin"] = CabinClass.BUSINESS
                elif "first" in cab: data["cabin"] = CabinClass.FIRST
                else: data["cabin"] = CabinClass.ECONOMY
            
            # Mapear TripType
            if data.get("trip_type"):
                tt = data["trip_type"].lower()
                data["trip_type"] = TripType.ROUNDTRIP if "round" in tt else TripType.ONEWAY

            data["confidence"] = 0.95
            data["notes"] = "Extraído via Groq IA"
            
            return ParsedIntent(**data)
            
        except Exception as e:
            print(f"Erro no Groq Parser: {e}")
            # Fallback para regex
            return parse_intent_regex(text)
    
    return parse_intent_regex(text)
