"""Interpretação do pedido por LLM — extrai os BLOCOS DE FILTRO estruturados a
partir de linguagem natural (rota, janelas de ida/volta, flex, mala, direto,
horário, cabine, passageiros).

Princípio de segurança: a LLM ENTENDE a intenção e devolve JSON; o código
DETERMINÍSTICO valida o factual — IATA pela tabela oficial (nunca confia em
código vindo da LLM) e datas (formato/coerência). Se a LLM cair ou vier
incoerente, o intake usa o regex (`nlp/intent_parser`) como fallback.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SCHEMA_PROMPT = """Você extrai a intenção de cotação de passagens aéreas de uma conversa em PT-BR e devolve SOMENTE um objeto JSON (sem texto antes/depois, sem markdown).

HOJE é {today}. Resolva datas relativas a partir de hoje. Datas sem ano: use o ano que mantém a data no FUTURO.

Schema (use null quando o cliente não mencionou):
{{
  "origin_city": "string ou null",        // NOME da cidade/aeroporto de origem (ex.: "Brasília"). NUNCA invente código IATA.
  "destination_city": "string ou null",   // NOME do destino
  "trip_type": "oneway" ou "roundtrip",
  "depart": {{"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}} ou null,   // janela de IDA; data única → from == to
  "return": {{"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}} ou null,   // janela de VOLTA (só em roundtrip)
  "flexible_dates": true/false,            // true se deu janela/intervalo/flex de datas
  "trip_duration_days": número ou null,    // "viagem de 5 dias"
  "baggage_checked": true/false/null,      // true se pediu mala despachada (23kg); false se "só mão"; null se não falou
  "direct_only": true/false,               // true se pediu voo direto/sem escala
  "time_preference": "manha"/"tarde"/"noite"/"madrugada" ou null,
  "cabin": "economy"/"business"/"first",
  "adults": número, "children": número, "infants": número,
  "notes": "string ou null"                // observações livres (cliente VIP, programa preferido)
}}

Regras de interpretação:
- "ida entre 10 e 12 de julho e volta 20 ou 21 de julho" → trip_type=roundtrip, depart={{from:2026-07-10,to:2026-07-12}}, return={{from:2026-07-20,to:2026-07-21}}, flexible_dates=true.
- "viagem de N dias" + uma JANELA de ida ("entre X e Y") → trip_type=roundtrip, depart={{from:X, to:Y}}, trip_duration_days=N, return=null, flexible_dates=true. NÃO calcule a volta — o sistema desliza a duração dentro da janela. Ex.: "viagem de 10 dias entre os dias 10 e 25 de setembro" → depart={{from:2026-09-10, to:2026-09-25}}, trip_duration_days=10, return=null, flexible_dates=true.
- "viagem de N dias a partir de X" (sem janela) → depart={{from:X, to:X}}, trip_duration_days=N, return=null.
- "só ida" / "somente ida" → trip_type=oneway, return=null.
- "a data mais barata entre X e Y" → janela X..Y, flexible_dates=true.
- Default: adults=1, children=0, infants=0, cabin="economy", direct_only=false.
- Devolva SOMENTE o JSON."""


def interpret(conversation_text: str, *, today: date) -> Optional[Dict[str, Any]]:
    """Chama a LLM e devolve o dict bruto interpretado (nomes de cidade + datas
    ISO + flags). None se a LLM falhar/indisponível ou o JSON for inválido."""
    if not conversation_text.strip():
        return None
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from backend.app.ai.agents.llm import get_cached_chat_model

        model = get_cached_chat_model(primary=False)
        sys = SystemMessage(content=_SCHEMA_PROMPT.format(today=today.isoformat()))
        user = HumanMessage(content=f"Conversa do vendedor:\n{conversation_text}\n\nJSON:")
        resp = model.invoke([sys, user])
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        return _parse_json(text)
    except Exception as e:
        logger.warning("LLM interpreter indisponível/falhou (%s) — usando regex", e)
        return None


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    # Remove cercas de markdown.
    t = re.sub(r"```(?:json)?", "", t).strip()
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def to_slots(raw: Dict[str, Any], *, today: date) -> Dict[str, Any]:
    """Converte o JSON bruto da LLM em slots VALIDADOS. IATA sempre pela tabela
    oficial (nunca da LLM). Datas validadas. Devolve dict parcial de slots."""
    from backend.app.nlp.intent_parser import IATA_STOPWORDS
    from backend.app.providers.buscamilhas.iata_resolver import resolve_city_to_iatas

    def _iatas(city: Any, n: int = 2) -> list:
        """Top-N aeroportos da cidade (resolver já vem ordenado por relevância).
        Cidade multi-aeroporto (São Paulo=GRU/VCP) → busca os 2 principais."""
        if not city:
            return []
        codes = [c for c in (resolve_city_to_iatas(str(city)) or []) if c.upper() not in IATA_STOPWORDS]
        return codes[:n]

    def _d(s: Any) -> Optional[date]:
        try:
            d = date.fromisoformat(str(s)[:10])
        except (ValueError, TypeError):
            return None
        return d if d >= today else None  # ignora datas no passado (erro da LLM)

    out: Dict[str, Any] = {}

    oi_list = _iatas(raw.get("origin_city"))
    di_list = _iatas(raw.get("destination_city"))
    if oi_list:
        out["origin_iata"] = oi_list[0]
        out["origin_city"] = str(raw.get("origin_city"))
        if len(oi_list) > 1:
            out["origin_iatas"] = oi_list          # cidade multi-aeroporto
    if di_list:
        out["destination_iata"] = di_list[0]
        out["destination_city"] = str(raw.get("destination_city"))
        if len(di_list) > 1:
            out["destination_iatas"] = di_list

    dep = raw.get("depart") or {}
    ret = raw.get("return") or {}
    dep_from, dep_to = _d(dep.get("from")), _d(dep.get("to"))
    ret_from, ret_to = _d(ret.get("from")), _d(ret.get("to"))

    if dep_from:
        out["date_start"] = dep_from.isoformat()
        if dep_to and dep_to > dep_from:
            out["date_end"] = dep_to.isoformat()
            out["flex_mode"] = "range"
    if ret_from:
        out["return_from"] = ret_from.isoformat()
        out["return_to"] = (ret_to or ret_from).isoformat()
        out["trip_type"] = "roundtrip"
        if ret_to and ret_to > ret_from:
            out["flex_mode"] = "range"

    if raw.get("trip_type") in ("oneway", "roundtrip"):
        out.setdefault("trip_type", raw["trip_type"])
    if raw.get("flexible_dates") and out.get("date_end"):
        out["flex_mode"] = "range"

    dur = raw.get("trip_duration_days")
    if isinstance(dur, int) and dur > 0:
        out["trip_duration_days"] = dur
        out.setdefault("trip_type", "roundtrip")
        # Duração + JANELA de ida = viagem deslizante (10→20, 11→21, …). A volta
        # é derivada (ida+duração), então NÃO fixa volta — senão vira RT fixo e
        # só uma combinação é varrida. (Se não há janela de ida, mantém a volta.)
        if out.get("date_end"):
            out.pop("return_from", None)
            out.pop("return_to", None)
            out["flex_mode"] = "range"

    if raw.get("baggage_checked") is not None:
        out["baggage_checked"] = bool(raw["baggage_checked"])
    if raw.get("direct_only") is not None:
        out["direct_only"] = bool(raw["direct_only"])
    if raw.get("time_preference") in ("manha", "tarde", "noite", "madrugada"):
        out["time_preference"] = raw["time_preference"]
    if raw.get("cabin") in ("economy", "business", "first"):
        out["cabin"] = raw["cabin"]
    for k in ("adults", "children", "infants"):
        v = raw.get(k)
        if isinstance(v, int) and v >= 0:
            out[k] = v
    if raw.get("notes"):
        out["notes"] = str(raw["notes"])
    return out
