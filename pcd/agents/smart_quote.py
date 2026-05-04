"""
Cotação Inteligente — 3 agentes sequenciais que coexistem com o pipeline
normal sem alterá-lo.

  Agente 1 (Explorador de Datas)  → Kayak em 9 datas (data ±4)
  Agente 2 (Mapeador de Programas) → cruza carriers ↔ PROGRAM_PARTNERS
  Agente 3 (Cotador de Milhas)    → BuscaMilhas só nos programas relevantes,
                                    apenas na data âncora.

Toda a UI consome `SmartQuoteResult`. Falhas individuais não derrubam o
fluxo: Kayak indisponível devolve calendar vazio, Buscamilhas falho devolve
miles_offers vazio.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional

# Reusa os clientes existentes — não os modifica.
from kayak_client import search_flights as kayak_search
from offer_parser import extract_offers as kayak_extract
from miles_app.buscamilhas_client import search_flights_buscamilhas


# ──────────────────────────────────────────────────────────────────
# Mapeamento de programas brasileiros → companhias parceiras (IATA)
# ──────────────────────────────────────────────────────────────────
PROGRAM_PARTNERS: dict[str, dict] = {
    "SMILES": {  # GOL Smiles — ~55 parceiras
        "airlines": [
            "G3",  # GOL (própria)
            "AA", "DL", "UA", "AC", "LA", "AD",
            "CM", "AV", "AM", "AR", "H2",
            "LH", "LX", "OS", "SK", "AY", "KL", "AF",
            "BA", "IB", "TP", "FR", "VY", "EI",
            "TK", "EK", "EY", "QR", "SV", "MS",
            "ET", "SA", "WB",
            "NH", "JL", "CX", "SQ", "TG", "KE",
            "MH", "GA", "VN", "OZ", "CI", "BR",
            "NZ", "QF",
        ],
        "rate_brl_per_mile": 0.0200,
        "label": "Smiles (GOL)",
        "own_carrier": "G3",
        "buscamilhas_key": "GOL",
    },
    "LATAM_PASS": {  # LATAM Pass — 16 parceiras (tabela fixa via call center)
        "airlines": [
            "LA",  # LATAM (própria)
            "QR", "DL", "JL", "QF", "VS",
            "AS", "BA", "IB", "CX", "AY",
            "RJ", "LH", "LX", "OS", "AM", "AR",
        ],
        "rate_brl_per_mile": 0.0285,
        "label": "LATAM Pass",
        "own_carrier": "LA",
        "buscamilhas_key": "LATAM",
    },
    "AZUL_FIDELIDADE": {  # Azul Fidelidade — 31 parceiras, award real em 6
        "airlines": [
            "AD",  # Azul (própria)
            "AC", "CM", "EK", "TP", "TK", "UA",
            "EY", "LH", "LX", "OS", "SK", "AY", "KL", "AF",
            "BA", "IB", "QR", "SQ", "NH", "JL", "CX",
            "AM", "AR", "AV", "LA",
        ],
        "award_partners": ["AC", "CM", "EK", "TP", "TK", "UA"],
        "rate_brl_per_mile": 0.0200,
        "label": "Azul Fidelidade",
        "own_carrier": "AD",
        "buscamilhas_key": "AZUL",
    },
}


# ──────────────────────────────────────────────────────────────────
# Resultado
# ──────────────────────────────────────────────────────────────────
@dataclass
class SmartQuoteResult:
    price_calendar: dict[str, float] = field(default_factory=dict)   # ISO date → BRL
    calendar_carriers: dict[str, list[str]] = field(default_factory=dict)  # ISO date → [IATA]
    anchor_date: Optional[str] = None
    anchor_carriers: list[str] = field(default_factory=list)
    savings_vs_requested: float = 0.0
    relevant_programs: dict = field(default_factory=dict)
    miles_offers: list = field(default_factory=list)
    date_requested: str = ""
    date_is_already_best: bool = False
    notes: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────
def _carriers_iata_from_kayak_raw(raw: dict) -> list[str]:
    """Extrai códigos IATA únicos das companhias presentes nos segments do
    raw response do Kayak. Necessário porque `extract_offers` devolve nomes
    e o Agente 2 trabalha com IATA."""
    if not isinstance(raw, dict):
        return []
    segments = (raw.get("data") or {}).get("segments") or raw.get("segments") or {}
    if not isinstance(segments, dict):
        return []
    seen: list[str] = []
    for seg in segments.values():
        if not isinstance(seg, dict):
            continue
        code = seg.get("airline")
        if isinstance(code, str) and 2 <= len(code) <= 3:
            up = code.upper()
            if up not in seen:
                seen.append(up)
    return seen


def _min_brl_price(parsed_offers: list[dict]) -> Optional[float]:
    """Retorna o menor preço em BRL entre as ofertas. USD/EUR convertidos
    grosseiramente via fx_rates apenas se disponível (reusa o módulo
    existente sem alterá-lo)."""
    if not parsed_offers:
        return None
    best: Optional[float] = None
    for o in parsed_offers:
        amt = o.get("price")
        ccy = (o.get("currency") or "BRL").upper()
        if amt is None:
            continue
        try:
            amt_f = float(amt)
        except (TypeError, ValueError):
            continue
        if ccy == "BRL":
            brl = amt_f
        else:
            try:
                import fx_rates  # tipo USD/EUR → BRL
                brl = fx_rates.convert(amt_f, ccy, "BRL")
            except Exception:
                # Fallback grosseiro: ~5.0 USD→BRL para não descartar a oferta
                brl = amt_f * 5.0
        if best is None or brl < best:
            best = brl
    return best


# ──────────────────────────────────────────────────────────────────
# Agente
# ──────────────────────────────────────────────────────────────────
class SmartQuoteAgent:
    """Roda os 3 agentes sequenciais. Suporta callback de progresso para a UI."""

    def __init__(self, flex_days_each_side: int = 4, max_workers: int = 9):
        self.flex_days = flex_days_each_side
        self.max_workers = max_workers

    # ── Agente 1 ────────────────────────────────────────────────
    def _explore_dates(
        self,
        origin: str,
        destination: str,
        date_requested: date,
        adults: int,
        return_date: Optional[date],
    ) -> tuple[dict[str, float], dict[str, list[str]]]:
        """Retorna (price_calendar, carriers_calendar) — 9 entradas no
        feliz caso. Datas que falharem são omitidas."""
        spans = list(range(-self.flex_days, self.flex_days + 1))
        target_dates = [date_requested + timedelta(days=d) for d in spans]

        price_calendar: dict[str, float] = {}
        carriers_calendar: dict[str, list[str]] = {}

        def _one(d: date):
            try:
                raw = kayak_search(
                    origin=origin,
                    destination=destination,
                    departure_date=d.isoformat(),
                    return_date=return_date.isoformat() if return_date else None,
                    adults=adults,
                    cabin="e",
                )
                parsed = kayak_extract(raw)
                price = _min_brl_price(parsed)
                carriers = _carriers_iata_from_kayak_raw(raw)
                return d.isoformat(), price, carriers, None
            except Exception as e:
                return d.isoformat(), None, [], str(e)[:200]

        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = [ex.submit(_one, d) for d in target_dates]
            for f in as_completed(futures):
                iso, price, carriers, err = f.result()
                if price is not None:
                    price_calendar[iso] = price
                if carriers:
                    carriers_calendar[iso] = carriers

        return price_calendar, carriers_calendar

    # ── Análise — encontra a data âncora ────────────────────────
    @staticmethod
    def _find_anchor(
        price_calendar: dict[str, float],
        carriers_calendar: dict[str, list[str]],
        date_requested: date,
    ) -> tuple[Optional[str], list[str], float, bool]:
        if not price_calendar:
            return None, [], 0.0, False

        sorted_iso = sorted(price_calendar.items(), key=lambda kv: kv[1])
        anchor_iso, anchor_price = sorted_iso[0]
        requested_iso = date_requested.isoformat()
        requested_price = price_calendar.get(requested_iso)

        already_best = (anchor_iso == requested_iso)

        if requested_price is not None and not already_best:
            savings = max(0.0, requested_price - anchor_price)
        else:
            savings = 0.0

        return (
            anchor_iso,
            carriers_calendar.get(anchor_iso, []),
            savings,
            already_best,
        )

    # ── Agente 2 ────────────────────────────────────────────────
    @staticmethod
    def _map_programs(carriers: list[str]) -> dict:
        """Cruza os carriers (IATA) da data âncora com PROGRAM_PARTNERS.
        Retorna por programa as parceiras presentes na rota e marca quais
        são programas relevantes (têm pelo menos 1 cobertura)."""
        if not carriers:
            return {
                "SMILES": [], "LATAM_PASS": [], "AZUL_FIDELIDADE": [],
                "relevant_programs": [],
                "award_only": {},
                "own_carrier_present": [],
            }

        carriers_up = [c.upper() for c in carriers]
        coverage: dict[str, list[str]] = {}
        award_only: dict[str, list[str]] = {}
        own_present: list[str] = []

        for prog_key, info in PROGRAM_PARTNERS.items():
            covered = [c for c in carriers_up if c in info["airlines"]]
            coverage[prog_key] = covered
            if info.get("own_carrier") and info["own_carrier"] in carriers_up:
                own_present.append(prog_key)
            if "award_partners" in info:
                award_only[prog_key] = [c for c in covered if c in info["award_partners"]]

        # Prioridade: programa próprio > award real > maior cobertura.
        scored: list[tuple[int, int, str]] = []  # (priority, neg_count, key)
        for prog_key, covered in coverage.items():
            if not covered:
                continue
            priority = 0
            if prog_key in own_present:
                priority = 3
            elif award_only.get(prog_key):
                priority = 2
            else:
                priority = 1
            scored.append((priority, -len(covered), prog_key))

        scored.sort(key=lambda t: (-t[0], t[1]))  # priority desc, count desc
        relevant = [k for _, _, k in scored]

        return {
            "SMILES":           coverage["SMILES"],
            "LATAM_PASS":       coverage["LATAM_PASS"],
            "AZUL_FIDELIDADE":  coverage["AZUL_FIDELIDADE"],
            "relevant_programs": relevant,
            "award_only":        award_only,
            "own_carrier_present": own_present,
        }

    # ── Agente 3 ────────────────────────────────────────────────
    def _quote_miles(
        self,
        origin: str,
        destination: str,
        anchor_iso: str,
        relevant_programs: list[str],
        adults: int,
        return_date: Optional[date],
    ) -> list[dict]:
        """Chama BuscaMilhas em paralelo apenas para os programas relevantes.
        Retorna lista de dicts simples (não usa pcd.core.schema para manter
        a Cotação Inteligente independente do pipeline normal)."""
        if not relevant_programs:
            return []

        try:
            anchor_date = date.fromisoformat(anchor_iso)
        except ValueError:
            return []

        data_ida = anchor_date.strftime("%d/%m/%Y")
        data_volta = return_date.strftime("%d/%m/%Y") if return_date else None

        def _one(prog_key: str):
            info = PROGRAM_PARTNERS[prog_key]
            companhia = info["buscamilhas_key"]
            t0 = time.perf_counter()
            try:
                raw = search_flights_buscamilhas(
                    companhia=companhia,
                    origem=origin, destino=destination,
                    data_ida=data_ida, data_volta=data_volta,
                    adultos=adults, classe="economica",
                    somente_milhas=True, somente_pagante=False,
                    internacional=companhia not in ("LATAM", "GOL", "AZUL"),
                )
                elapsed_ms = (time.perf_counter() - t0) * 1000
                return {
                    "program": prog_key,
                    "label": info["label"],
                    "raw": raw,
                    "elapsed_ms": elapsed_ms,
                    "error": None,
                }
            except Exception as e:
                return {
                    "program": prog_key,
                    "label": info["label"],
                    "raw": None,
                    "elapsed_ms": (time.perf_counter() - t0) * 1000,
                    "error": str(e)[:200],
                }

        out = []
        with ThreadPoolExecutor(max_workers=min(3, len(relevant_programs))) as ex:
            futures = [ex.submit(_one, p) for p in relevant_programs]
            for f in as_completed(futures):
                out.append(f.result())
        return out

    # ── Orquestrador ────────────────────────────────────────────
    def run(
        self,
        origin: str,
        destination: str,
        date_requested: date,
        adults: int = 1,
        return_date: Optional[date] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> SmartQuoteResult:
        def _progress(msg: str):
            if progress_cb:
                try:
                    progress_cb(msg)
                except Exception:
                    pass

        result = SmartQuoteResult(date_requested=date_requested.isoformat())

        # Agente 1
        _progress("🔍 Agente 1: Explorando preços em 9 datas via Kayak...")
        try:
            price_calendar, carriers_calendar = self._explore_dates(
                origin, destination, date_requested, adults, return_date,
            )
            result.price_calendar = price_calendar
            result.calendar_carriers = carriers_calendar
        except Exception as e:
            result.notes.append(f"Kayak indisponível: {str(e)[:160]}")
            return result

        if not price_calendar:
            result.notes.append("Kayak não retornou preços para nenhuma das 9 datas.")
            return result

        # Análise do calendário
        anchor_iso, anchor_carriers, savings, already_best = self._find_anchor(
            price_calendar, carriers_calendar, date_requested,
        )
        result.anchor_date = anchor_iso
        result.anchor_carriers = anchor_carriers
        result.savings_vs_requested = savings
        result.date_is_already_best = already_best

        if not anchor_iso:
            result.notes.append("Não foi possível identificar uma data âncora.")
            return result

        # Agente 2
        _progress("🗺️ Agente 2: Mapeando programas de milhas para a rota...")
        relevant_programs = self._map_programs(anchor_carriers)
        result.relevant_programs = relevant_programs

        if not relevant_programs.get("relevant_programs"):
            result.notes.append(
                "Nenhum programa de milhas cadastrado cobre as companhias disponíveis "
                "nessa data. Considere emitir em dinheiro."
            )
            return result

        # Agente 3
        _progress("💎 Agente 3: Cotando milhas na melhor data...")
        result.miles_offers = self._quote_miles(
            origin, destination, anchor_iso,
            relevant_programs["relevant_programs"], adults, return_date,
        )

        return result
