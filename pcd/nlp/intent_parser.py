import re
import json
import os
from datetime import date, timedelta
from typing import Optional, Dict, Any
from litellm import completion
from pcd.core.schema import ParsedIntent, TripType, CabinClass
from miles_app.iata_resolver import resolve_city_to_iatas, normalize_city_key

IATA_STOPWORDS = {"UMA", "IDA", "VOL", "PRA", "COM", "TEM", "SAO", "DIA", "PRO", "VOU", "ELA", "ELE", "EST", "NOS", "POR"}

def clean_text_ptbr(text: str) -> str:
    """
    Remove frases de preenchimento iniciais e normaliza o texto.
    Ex: 'Quero uma passagem de Brasília para Lisboa' -> 'de Brasília para Lisboa'
    """
    if not text:
        return ""
    
    t = text.lower().strip()
    # Normalizar pontuação básica para espaços
    t = re.sub(r"[,.;!?]", " ", t)
    
    # Expressões de preenchimento para remover (somente no início)
    filler_prefixes = [
        r"^quero\s+(?:uma\s+|um\s+)?(?:passagem|voo|viagem|cotacao|cotar)?(?:\s+de\s+|\s+para\s+|\s+pra\s+)?",
        r"^preciso\s+(?:de\s+)?(?:uma\s+|um\s+)?(?:passagem|voo|viagem)?",
        r"^gostaria\s+(?:de\s+)?(?:uma\s+|um\s+)?(?:passagem|voo|viagem)?",
        r"^me\s+ve\s+(?:uma\s+|um\s+)?(?:passagem|voo|viagem)?",
        r"^(?:uma\s+|um\s+)?(?:passagem|voo|viagem|cotacao)\s+(?:de\s+|para\s+|pra\s+)?",
        r"^busque\s+(?:de\s+|para\s+|pra\s+)?",
        r"^procure\s+(?:de\s+|para\s+|pra\s+)?",
    ]
    
    for pattern in filler_prefixes:
        new_t = re.sub(pattern, "", t).strip()
        # Se removeu algo, mas sobrou "de " ou "para " no início que foi removido acidentalmente, 
        # ou se o texto mudou, precisamos ter cuidado para manter os delimitadores.
        # Na verdade, o ideal é remover o máximo e depois o regex de extração (Part B) cuida do resto.
        if new_t != t:
            # Se o texto original começava com "de " ou "para ", garanta que não perdemos o delimitador
            # se ele for necessário para o regex. Entretanto, o regex posterior aceita sem "de".
            t = new_t
            break

    # Se o texto ficou "brasília para lisboa...", mantemos.
    return t

def _extract_dates(text: str) -> tuple[Optional[date], Optional[date]]:
    """Extrai datas em formatos comuns (dd/mm/aaaa, dd/mm/yy, etc)"""
    # Regex para dd/mm/aaaa ou dd/mm/yy
    date_pattern = r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})"
    matches = re.findall(date_pattern, text)
    
    dates = []
    
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

def parse_intent_regex(text: str) -> ParsedIntent:
    """Fallback usando Regex e heurísticas básicas"""
    text_clean = clean_text_ptbr(text)
    text_lower = text_clean.lower()
    
    intent = ParsedIntent()
    
    # 1. Datas
    intent.date_start, intent.date_return = _extract_dates(text_lower)
    
    # 2. Trip Type
    if any(k in text_lower for k in ["ida e volta", "volta dia", "retorno", "roundtrip"]):
        intent.trip_type = TripType.ROUNDTRIP
    else:
        intent.trip_type = TripType.ONEWAY
        
    # 3. Origem / Destino (Regex com prioridade e lookahead)
    # Padrão 1: de <orig> para <dest> (com lookahead para descartar datas/flex)
    # Padrão 2: <orig> para <dest>
    # Padrão 3: <orig> -> <dest>
    
    patterns = [
        # de X para Y
        r"\bde\s+(?P<orig>.+?)\s+(?:para|pra|to)\s+(?P<dest>.+?)(?=\s+(?:dia|em|na|no|ida|volta|data|flex|±|com|para\s+o|as|às)\b|\s+\d+|$)",
        # X para Y (sem o 'de' inicial)
        r"^(?P<orig>.+?)\s+(?:para|pra|to)\s+(?P<dest>.+?)(?=\s+(?:dia|em|na|no|ida|volta|data|flex|±|com|para\s+o|as|às)\b|\s+\d+|$)",
        # X -> Y
        r"(?P<orig>.+?)\s*(?:->|=>)\s*(?P<dest>.+?)(?=\s+(?:dia|em|na|no|ida|volta|data|flex|±|com|para\s+o|as|às)\b|\s+\d+|$)",
    ]
    
    extracted = False
    for p in patterns:
        match = re.search(p, text_lower)
        if match:
            origin_raw = match.group("orig").strip()
            dest_raw = match.group("dest").strip()
            
            # Resolver cidades
            origin_iatas = resolve_city_to_iatas(origin_raw)
            dest_iatas = resolve_city_to_iatas(dest_raw)
            
            # Anti-IATA Falso (Stopwords)
            def filter_iatas(codes):
                return [c for c in codes if c.upper() not in IATA_STOPWORDS]

            origin_iatas = filter_iatas(origin_iatas)
            dest_iatas = filter_iatas(dest_iatas)

            intent.origin_city = origin_raw.title()
            intent.origin_iata = origin_iatas[0] if origin_iatas else None
            
            intent.destination_city = dest_raw.title()
            intent.destination_iata = dest_iatas[0] if dest_iatas else None
            
            # Se resolveu ambos os IATAs, aumenta a confiança
            if intent.origin_iata and intent.destination_iata:
                intent.confidence = 0.85
            else:
                intent.confidence = 0.6
                
            extracted = True
            break
    
    # 4. Outros
    if "direto" in text_lower or "sem escala" in text_lower:
        intent.direct_only = True
        
    if "executiva" in text_lower or "business" in text_lower:
        intent.cabin = CabinClass.BUSINESS
    elif "primeira" in text_lower or "first" in text_lower:
        intent.cabin = CabinClass.FIRST
        
    # Detectar flexibilidade
    flex_match = re.search(r"(?:flex(?:ível)?|±|mais\s+ou\s+menos)\s*(\d+)\s*dia", text_lower) or \
                 re.search(r"(\d+)\s*dia(?:s)?\s*(?:de\s*)?flex", text_lower)
    if flex_match:
        intent.flex_days = int(flex_match.group(1))
        
    # Detectar volta flexível
    if "volta flex" in text_lower or "retorno flex" in text_lower:
        intent.flex_return = True
        
    # Detectar adultos
    adult_match = re.search(r"(\d+)\s*(?:adulto|pessoa|passageiro)", text_lower)
    if adult_match:
        intent.adults = int(adult_match.group(1))
        
    if not extracted:
        intent.confidence = 0.2
        intent.notes = "Extraído via REGEX (Fallback - Baixa Confiança)"
    else:
        intent.notes = "Extraído via REGEX (Fallback - Refinado)"
    
    return intent

def parse_intent_ptbr(text: str, use_llm: bool = False) -> ParsedIntent:
    """Função principal de parsing"""
    if use_llm and os.getenv("GROQ_API_KEY"):
        try:
            # Usar texto levemente limpo para o LLM também não se perder
            text_for_llm = clean_text_ptbr(text)
            
            prompt = f"""
            Você é um assistente de viagens especialista em extrair dados de voos em Português (Brasil).
            Extraia as seguintes informações do texto do usuário:
            - origin_city (Nome da cidade)
            - origin_iata (3 letras, ex: BSB)
            - destination_city (Nome da cidade)
            - destination_iata (3 letras, ex: GRU)
            - trip_type ("oneway" ou "roundtrip")
            - date_start (YYYY-MM-DD)
            - date_return (YYYY-MM-DD ou null)
            - adults (número inteiro)
            - cabin ("economy", "business" ou "first")
            - direct_only (booleano)
            - flex_days (número inteiro de flexibilidade na IDA, ex: "±3 dias" -> 3)
            - flex_return (booleano, True se mencionar "volta flexível" ou similar)

            Texto do Usuário: "{text_for_llm}"
            Data de Hoje: {date.today().isoformat()}

            IMPORTANTE: Se a cidade tiver múltiplos aeroportos (ex: São Paulo), tente sugerir o código da cidade ou o principal. 
            Se não tiver certeza da IATA, foque no nome da cidade.

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
            if data.get("date_start") and isinstance(data["date_start"], str):
                data["date_start"] = date.fromisoformat(data["date_start"])
            if data.get("date_return") and isinstance(data["date_return"], str):
                data["date_return"] = date.fromisoformat(data["date_return"])
            
            # Mapear Cabin
            if data.get("cabin"):
                cab = str(data["cabin"]).lower()
                if "bus" in cab: data["cabin"] = CabinClass.BUSINESS
                elif "first" in cab: data["cabin"] = CabinClass.FIRST
                else: data["cabin"] = CabinClass.ECONOMY
            
            # Mapear TripType
            if data.get("trip_type"):
                tt = str(data["trip_type"]).lower()
                data["trip_type"] = TripType.ROUNDTRIP if "round" in tt else TripType.ONEWAY

            # Especial: Se IATA não veio do LLM, tenta resolver pelo nome da cidade
            if not data.get("origin_iata") and data.get("origin_city"):
                codes = resolve_city_to_iatas(data["origin_city"])
                # Filtrar stopwords
                codes = [c for c in codes if c.upper() not in IATA_STOPWORDS]
                if codes: data["origin_iata"] = codes[0]
            
            if not data.get("destination_iata") and data.get("destination_city"):
                codes = resolve_city_to_iatas(data["destination_city"])
                # Filtrar stopwords
                codes = [c for c in codes if c.upper() not in IATA_STOPWORDS]
                if codes: data["destination_iata"] = codes[0]

            data["confidence"] = 0.95
            data["notes"] = "Extraído via Groq IA"
            
            return ParsedIntent(**data)
            
        except Exception as e:
            print(f"Erro no Groq Parser: {e}")
            # Fallback para regex
            return parse_intent_regex(text)
    
    return parse_intent_regex(text)
