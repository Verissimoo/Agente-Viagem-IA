"""Sanitização de ofertas para apresentação ao vendedor.

Recebe a oferta crua (`UnifiedOffer` dict) e devolve a versão "apresentável":
- Remove `source` (provider name)
- Remove `deeplink` (URL que revelaria origem)
- Substitui `scenario` por rótulo neutro
- Converte códigos IATA de cias (G3, LA) em nomes amigáveis (GOL, LATAM)
- Mantém: preço, datas, duração, escalas, companhia, milhas, taxas, risk_notes

A UI usa apenas o dict sanitizado para renderizar cards.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.app.ai.agents.airlines import (
    carrier_to_program,
    miles_program_name,
    prettify_carrier,
)


# Cenário interno → label do mercado (mantemos o termo técnico, é informação
# do vendedor). Só escondemos NOMES de provider/fonte, não o tipo da oferta.
_SCENARIO_LABEL = {
    "cash_direct": "Cash direto",
    "miles_direct": "Milhas",
    "hidden_city": "Hidden City",
    "split_cash": "Split de trecho",
    "split_miles": "Split de trecho (milhas)",
    "azul_official": "Azul Oficial",
}

# Justificativa curta de por que aquela oferta entrou no ranking — vai
# para o relatório PDF e pro card. Curtinho, em PT-BR, sem citar fonte.
_SCENARIO_WHY = {
    "cash_direct": "Tarifa em dinheiro publicada direto pela cia. Sem ressalvas operacionais.",
    "miles_direct": "Emissão em milhas + taxas, na cia escolhida.",
    "hidden_city": (
        "Tarifa otimizada via hidden city — o bilhete tem destino final em outra cidade, "
        "e o passageiro desembarca na conexão (que é a cidade que ele realmente quer). "
        "Risco: não despachar bagagem; bilhete só pode ser usado nesse sentido."
    ),
    "split_cash": (
        "Split de trecho — comprar dois bilhetes separados (origem→hub + hub→destino) "
        "sai mais barato que o bilhete direto. Risco: bagagem não é transferida "
        "automaticamente entre os bilhetes."
    ),
    "split_miles": (
        "Split de trecho em milhas — combinar duas emissões diferentes pra "
        "reduzir o total de milhas+taxas. Mesmas observações do split em cash."
    ),
    "azul_official": (
        "Tarifa CASH oficial da Azul via canal de agência — emissão direta "
        "no sistema da cia, com markup pré-incluso pra revenda. Sem risco "
        "operacional (não é hidden city nem split). Geralmente competitiva "
        "frente ao site público da Azul."
    ),
}


def _compute_miles_equivalent_brl(
    miles: Optional[int], taxes_brl: Optional[float], airline: str,
    program: str = "",
) -> Optional[float]:
    """Converte milhas + taxas em equivalente BRL usando rates.json.

    Lazy import pra evitar ciclo com services.
    """
    if not miles:
        return None
    try:
        from backend.app.services.conversion import miles_to_brl
    except Exception:
        return None
    try:
        miles_value_brl = miles_to_brl(miles, airline=airline or "", program=program or "")
        return float(miles_value_brl) + float(taxes_brl or 0)
    except Exception:
        return None


def sanitize_offer(offer: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Retorna versão segura da oferta para o vendedor (sem provider/jargão).

    Mantém `risk_notes` — vendedor precisa saber riscos para informar o cliente.
    Calcula `equivalent_brl` se a oferta é em milhas e não veio pré-calculado.
    """
    if not offer:
        return None
    out = dict(offer)
    out.pop("source", None)
    out.pop("deeplink", None)
    # Estimativas internas que não interessam ao vendedor
    out.pop("miles_equivalent_program", None)
    out.pop("trace_path", None)

    # Cia: código IATA (G3) → nome amigável (GOL).
    # Pra milhas, mostra o programa (Smiles) em vez da cia, se disponível.
    original_carrier = out.get("airline")
    if original_carrier:
        out["airline_code"] = str(original_carrier).upper()
        out["airline"] = prettify_carrier(original_carrier) or original_carrier
        # Pra ofertas em milhas: nome do programa fica em campo separado
        if out.get("miles") is not None:
            prog = miles_program_name(original_carrier) or carrier_to_program(original_carrier)
            if prog:
                out["miles_program_label"] = prog

    # Segmentos: prettify carrier de cada também (display nos itinerários)
    for itin_key in ("outbound", "inbound"):
        itin = out.get(itin_key)
        if isinstance(itin, dict) and itin.get("segments"):
            new_segs = []
            for seg in itin["segments"]:
                seg_copy = dict(seg)
                c = seg.get("carrier")
                if c:
                    seg_copy["carrier_code"] = str(c).upper()
                    seg_copy["carrier"] = prettify_carrier(c) or c
                new_segs.append(seg_copy)
            out[itin_key] = {**itin, "segments": new_segs}

    scenario = out.pop("scenario", None)
    # Inferência: se o parser do provider não setou scenario, deduzimos pelos dados.
    if not scenario:
        if offer.get("miles") is not None:
            scenario = "miles_direct"
        elif offer.get("price_brl") is not None:
            scenario = "cash_direct"
    out["category"] = _SCENARIO_LABEL.get(scenario, "Padrão") if scenario else "Padrão"
    out["category_why"] = _SCENARIO_WHY.get(scenario, "") if scenario else ""

    # Equivalent_brl pra ofertas em milhas: usa tabela do rates.json
    # (LATAM tem faixas por volume; demais cias têm taxa única).
    # Permite o vendedor ver "X milhas + R$ Y ≈ R$ Z total"
    if out.get("miles") and out.get("equivalent_brl") is None:
        eq = _compute_miles_equivalent_brl(
            out.get("miles"), out.get("taxes_brl"),
            airline=str(out.get("airline") or ""),
            program=str(out.get("miles_program") or ""),
        )
        if eq is not None:
            out["equivalent_brl"] = round(eq, 2)

    # Gerar id determinístico estável para referência (vendedor seleciona pelo id).
    out.setdefault("offer_id", _hash_offer(offer))

    return out


def sanitize_offers(offers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [s for o in offers if (s := sanitize_offer(o)) is not None]


def _hash_offer(offer: Dict[str, Any]) -> str:
    """ID curto e estável por oferta. Sem informação sensível dentro."""
    import hashlib
    key = "|".join(
        str(offer.get(k, "")) for k in
        ("airline", "price_brl", "miles", "outbound", "inbound", "captured_at")
    )
    return "o_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
