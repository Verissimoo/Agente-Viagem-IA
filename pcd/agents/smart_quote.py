"""
Cotação Inteligente — agentes que coexistem com o pipeline normal sem
alterá-lo. O fluxo é em duas etapas (separadas por uma decisão humana):

  Etapa 1 — automática, em `run()`:
    Agente 1 (Explorador de Datas)  → Kayak em (flex_days*2+1) datas
    Agente 2 (Mapeador de Programas) → cruza carriers ↔ PROGRAM_PARTNERS

  Etapa 2 — sob demanda, em `quote_miles_for_date()`:
    Agente 3 (Cotador de Milhas)    → BuscaMilhas só nos programas relevantes,
                                      na data escolhida pelo vendedor.

Toda a UI consome `SmartQuoteResult`. Falhas individuais não derrubam o
fluxo: Kayak indisponível devolve calendar vazio, Buscamilhas falho devolve
miles_offers vazio.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

# Reusa os clientes existentes — não os modifica.
from kayak_client import search_flights as kayak_search
from offer_parser import extract_offers as kayak_extract
from miles_app.buscamilhas_client import search_flights_buscamilhas


# Markup aplicado ao preço bruto do Kayak para exibição comercial (10%).
KAYAK_MARKUP = 1.10


@dataclass
class FlightOptionLite:
    """Resumo enxuto de uma oferta Kayak — usado pela Cotação Inteligente
    para mostrar opções por data e pelo handler de milhas para reaproveitar
    o preço Kayak exato sem refazer a busca."""
    iso_date: str
    price_brl: float
    carriers_iata: list[str] = field(default_factory=list)
    carriers_names: list[str] = field(default_factory=list)
    departure_time: Optional[str] = None
    arrival_time: Optional[str] = None
    duration_min: Optional[int] = None
    stops: Optional[int] = None
    leg_id: Optional[str] = None
    flight_number: Optional[str] = None
    raw: dict = field(default_factory=dict)

    @property
    def price_with_markup(self) -> float:
        return round(self.price_brl * KAYAK_MARKUP, 2)

    @property
    def main_carrier_iata(self) -> str:
        return self.carriers_iata[0] if self.carriers_iata else ""


# ──────────────────────────────────────────────────────────────────
# Display de companhias — usado pela UI para chips coloridos
# ──────────────────────────────────────────────────────────────────
AIRLINE_DISPLAY: dict[str, dict[str, str]] = {
    "LA": {"name": "LATAM Airlines",    "color": "#E31837", "bg": "#fdf0f2"},
    "G3": {"name": "GOL",               "color": "#FF5B00", "bg": "#fff4f0"},
    "AD": {"name": "Azul",              "color": "#0032A0", "bg": "#f0f3ff"},
    "TP": {"name": "TAP Air Portugal",  "color": "#008A5E", "bg": "#f0faf6"},
    "AF": {"name": "Air France",        "color": "#002157", "bg": "#f0f3ff"},
    "KL": {"name": "KLM",               "color": "#00A1DE", "bg": "#f0faff"},
    "AA": {"name": "American Airlines", "color": "#0078D2", "bg": "#f0f7ff"},
    "DL": {"name": "Delta Air Lines",   "color": "#E01933", "bg": "#fdf0f2"},
    "UA": {"name": "United Airlines",   "color": "#005DAA", "bg": "#f0f6ff"},
    "TK": {"name": "Turkish Airlines",  "color": "#C8102E", "bg": "#fdf0f2"},
    "EK": {"name": "Emirates",          "color": "#D71921", "bg": "#fdf0f2"},
    "QR": {"name": "Qatar Airways",     "color": "#5C0632", "bg": "#f8f0f4"},
    "CM": {"name": "Copa Airlines",     "color": "#003DA5", "bg": "#f0f3ff"},
    "AC": {"name": "Air Canada",        "color": "#CC0000", "bg": "#fdf0f0"},
    "EY": {"name": "Etihad Airways",    "color": "#B8A06A", "bg": "#fdfbf5"},
    "IB": {"name": "Iberia",            "color": "#C8102E", "bg": "#fdf0f2"},
    "BA": {"name": "British Airways",   "color": "#2B5CA6", "bg": "#f0f4fc"},
    "LH": {"name": "Lufthansa",         "color": "#05164D", "bg": "#f0f2f8"},
    "JL": {"name": "Japan Airlines",    "color": "#CC0000", "bg": "#fdf0f0"},
    "CX": {"name": "Cathay Pacific",    "color": "#005A6E", "bg": "#f0f6f8"},
}


def airline_display(iata: str) -> dict[str, str]:
    """Retorna dict {name, color, bg} para o IATA. Fallback para o próprio
    código com cor neutra quando a companhia não está cadastrada."""
    return AIRLINE_DISPLAY.get(
        (iata or "").upper(),
        {"name": iata, "color": "#1a56a0", "bg": "#e8f0fb"},
    )


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
    price_calendar: dict[str, float] = field(default_factory=dict)   # ISO date → BRL (melhor oferta da data)
    calendar_carriers: dict[str, list[str]] = field(default_factory=dict)  # ISO date → [IATA] (todos da data)
    # Novos campos: lista completa de ofertas por data + melhor oferta detalhada.
    daily_offers: dict[str, list[FlightOptionLite]] = field(default_factory=dict)
    best_offer_per_date: dict[str, FlightOptionLite] = field(default_factory=dict)
    airline_per_date: dict[str, str] = field(default_factory=dict)  # ISO date → IATA principal da melhor oferta
    anchor_date: Optional[str] = None
    anchor_carriers: list[str] = field(default_factory=list)
    savings_vs_requested: float = 0.0
    relevant_programs: dict = field(default_factory=dict)
    miles_offers: list = field(default_factory=list)
    date_requested: str = ""
    date_is_already_best: bool = False
    notes: list[str] = field(default_factory=list)
    flex_days_used: int = 4

    def get_full_options_for_date(self, date_iso: str) -> list[dict]:
        """Lista de ofertas Kayak da data com os programas que emitem cada uma.
        Cada item: {"option": FlightOptionLite, "programs": [prog_key, ...]}.
        Ordenado por preço (mais barato primeiro)."""
        offers = self.daily_offers.get(date_iso) or []
        out: list[dict] = []
        for opt in offers:
            programs = SmartQuoteAgent.map_programs_for_carrier(opt.main_carrier_iata)
            out.append({"option": opt, "programs": programs})
        return out


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


def _to_brl(amt, ccy: Optional[str]) -> Optional[float]:
    """Converte um par (valor, moeda) para BRL. USD/EUR convertidos via
    fx_rates quando disponível; senão fallback grosseiro 5.0×."""
    if amt is None:
        return None
    try:
        amt_f = float(amt)
    except (TypeError, ValueError):
        return None
    cu = (ccy or "BRL").upper()
    if cu == "BRL":
        return amt_f
    try:
        import fx_rates
        return fx_rates.convert(amt_f, cu, "BRL")
    except Exception:
        return amt_f * 5.0


def _leg_iata_codes(raw: dict, leg_id: Optional[str]) -> tuple[list[str], Optional[str]]:
    """Extrai códigos IATA das companhias e o primeiro flight_number do leg.
    Retorna (codes, flight_number) — codes deduplicado preservando ordem."""
    if not isinstance(leg_id, str) or not isinstance(raw, dict):
        return [], None
    data = raw.get("data") or {}
    legs = data.get("legs") or {}
    segments = data.get("segments") or {}
    leg = legs.get(leg_id)
    if not isinstance(leg, dict):
        return [], None
    seg_refs = leg.get("segments") or []
    codes: list[str] = []
    first_flight: Optional[str] = None
    for sr in seg_refs:
        sid = sr.get("id") if isinstance(sr, dict) else (sr if isinstance(sr, str) else None)
        if not isinstance(sid, str) or sid not in segments:
            continue
        seg = segments[sid]
        code = seg.get("airline")
        if isinstance(code, str) and 2 <= len(code) <= 3:
            up = code.upper()
            if up not in codes:
                codes.append(up)
        if first_flight is None:
            fnum = seg.get("flightNumber") or seg.get("flight_number")
            if fnum is not None:
                first_flight = f"{codes[0] if codes else ''}{fnum}".strip()
    return codes, first_flight


def _parsed_offer_to_lite(
    parsed: dict, raw: dict, iso_date: str,
) -> Optional[FlightOptionLite]:
    price_brl = _to_brl(parsed.get("price"), parsed.get("currency"))
    if price_brl is None:
        return None
    leg_id = parsed.get("leg_id") or parsed.get("out_leg_id")
    codes, flight_num = _leg_iata_codes(raw, leg_id if isinstance(leg_id, str) else None)
    return FlightOptionLite(
        iso_date=iso_date,
        price_brl=float(price_brl),
        carriers_iata=codes,
        carriers_names=list(parsed.get("airlines") or []),
        departure_time=parsed.get("departure_time") or parsed.get("out_departure_time"),
        arrival_time=parsed.get("arrival_time") or parsed.get("out_arrival_time"),
        duration_min=parsed.get("duration_min") or parsed.get("out_duration_min"),
        stops=parsed.get("stops") if parsed.get("stops") is not None else parsed.get("out_stops"),
        leg_id=leg_id if isinstance(leg_id, str) else None,
        flight_number=flight_num,
        raw=dict(parsed),
    )


def _min_brl_price(parsed_offers: list[dict]) -> Optional[float]:
    """Compat: retorna o menor preço BRL — preservada para chamadas antigas."""
    if not parsed_offers:
        return None
    best: Optional[float] = None
    for o in parsed_offers:
        brl = _to_brl(o.get("price"), o.get("currency"))
        if brl is None:
            continue
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
    ) -> tuple[
        dict[str, float], dict[str, list[str]],
        dict[str, list[FlightOptionLite]], dict[str, FlightOptionLite],
        dict[str, str],
    ]:
        """Para cada data no range, faz uma chamada Kayak e captura TODAS as
        ofertas (ordenadas por preço) — não só o preço mínimo.

        Retorna 5 dicts indexados por ISO date:
          price_calendar      — preço da melhor oferta
          carriers_calendar   — todas as companhias retornadas naquela data
          daily_offers        — lista completa de FlightOptionLite por data
          best_offer_per_date — melhor FlightOptionLite por data (com leg_id)
          airline_per_date    — IATA principal da melhor oferta por data
        """
        spans = list(range(-self.flex_days, self.flex_days + 1))
        target_dates = [date_requested + timedelta(days=d) for d in spans]

        price_calendar: dict[str, float] = {}
        carriers_calendar: dict[str, list[str]] = {}
        daily_offers: dict[str, list[FlightOptionLite]] = {}
        best_offer_per_date: dict[str, FlightOptionLite] = {}
        airline_per_date: dict[str, str] = {}

        # MAX_OFFERS_PER_DATE limita o que armazenamos por data para não
        # inflar st.session_state — 10 cobre o "top 5 mais baratos + 5 alternativas"
        # que a UI mostra.
        MAX_OFFERS_PER_DATE = 10

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
                parsed = kayak_extract(raw) or []
                lites: list[FlightOptionLite] = []
                iso = d.isoformat()
                for p in parsed:
                    lite = _parsed_offer_to_lite(p, raw, iso)
                    if lite is not None:
                        lites.append(lite)
                lites.sort(key=lambda x: x.price_brl)
                lites = lites[:MAX_OFFERS_PER_DATE]
                carriers = _carriers_iata_from_kayak_raw(raw)
                return iso, lites, carriers, None
            except Exception as e:
                return d.isoformat(), [], [], str(e)[:200]

        _t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = [ex.submit(_one, d) for d in target_dates]
            for f in as_completed(futures):
                iso, lites, carriers, err = f.result()
                if lites:
                    daily_offers[iso] = lites
                    best = lites[0]
                    best_offer_per_date[iso] = best
                    price_calendar[iso] = best.price_brl
                    airline_per_date[iso] = best.main_carrier_iata
                if carriers:
                    carriers_calendar[iso] = carriers
        _t_elapsed = time.perf_counter() - _t0
        # TEMP_PERF — remover após validar
        print(
            f"⏱ TEMP_PERF smart_quote _explore_dates: "
            f"{len(target_dates)} datas Kayak em paralelo (max={self.max_workers}) → "
            f"{_t_elapsed:.1f}s, {len(price_calendar)} datas com preço"
        )

        return (
            price_calendar, carriers_calendar,
            daily_offers, best_offer_per_date, airline_per_date,
        )

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
    def map_programs_for_carrier(carrier: str) -> list[dict]:
        """Para um IATA específico, devolve os programas que emitem essa
        companhia, com flags 'own_carrier' e 'award_partner'.

        Uso típico: o vendedor seleciona o voo mais barato de uma data
        (operado por X) — chamamos isso e mostramos só os programas que
        cobrem X (não a interseção genérica da rota inteira)."""
        if not carrier:
            return []
        carrier_up = carrier.upper()
        out: list[dict] = []
        for prog_key, info in PROGRAM_PARTNERS.items():
            if carrier_up not in info["airlines"]:
                continue
            is_own = info.get("own_carrier") == carrier_up
            is_award = carrier_up in (info.get("award_partners") or [])
            out.append({
                "program": prog_key,
                "label": info["label"],
                "rate_brl_per_mile": info.get("rate_brl_per_mile"),
                "own_carrier": is_own,
                "award_partner": is_award,
                "buscamilhas_key": info.get("buscamilhas_key"),
            })
        # Prioridade: programa próprio > award partner > parceiro interline.
        out.sort(key=lambda p: (
            -3 if p["own_carrier"] else (-2 if p["award_partner"] else -1)
        ))
        return out

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
        _t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=min(3, len(relevant_programs))) as ex:
            futures = [ex.submit(_one, p) for p in relevant_programs]
            for f in as_completed(futures):
                out.append(f.result())
        _t_elapsed = time.perf_counter() - _t0
        # TEMP_PERF — remover após validar
        print(
            f"⏱ TEMP_PERF smart_quote _quote_miles: "
            f"{len(relevant_programs)} programas BuscaMilhas em paralelo → "
            f"{_t_elapsed:.1f}s"
        )
        return out

    # ── Orquestrador ────────────────────────────────────────────
    def run(
        self,
        origin: str,
        destination: str,
        date_requested: date,
        adults: int = 1,
        return_date: Optional[date] = None,
        flex_days: int = 4,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> SmartQuoteResult:
        """Etapa 1 da Cotação Inteligente: roda Agente 1 (Kayak) + Agente 2
        (mapeamento). NÃO chama BuscaMilhas — Agente 3 é executado sob
        demanda via `quote_miles_for_date()` quando o vendedor escolhe
        explicitamente uma data para a cotação completa."""
        self.flex_days = max(1, int(flex_days))

        def _progress(msg: str):
            if progress_cb:
                try:
                    progress_cb(msg)
                except Exception:
                    pass

        result = SmartQuoteResult(
            date_requested=date_requested.isoformat(),
            flex_days_used=self.flex_days,
        )

        # Agente 1
        total_dates = self.flex_days * 2 + 1
        _progress(f"🔍 Agente 1: Explorando preços em {total_dates} datas via Kayak...")
        try:
            (
                price_calendar, carriers_calendar,
                daily_offers, best_offer_per_date, airline_per_date,
            ) = self._explore_dates(
                origin, destination, date_requested, adults, return_date,
            )
            result.price_calendar = price_calendar
            result.calendar_carriers = carriers_calendar
            result.daily_offers = daily_offers
            result.best_offer_per_date = best_offer_per_date
            result.airline_per_date = airline_per_date
            # TEMP_LOG_SMART_QUOTE — diagnóstico para a investigação do AttributeError.
            # Remover após validar end-to-end no Streamlit Cloud.
            print(
                f"[smart_quote] price_calendar={len(price_calendar)} "
                f"daily_offers={len(daily_offers)} "
                f"best_offer_per_date={len(best_offer_per_date)} "
                f"airline_per_date={len(airline_per_date)} "
                f"sample_best={ {k: (v.main_carrier_iata, v.price_brl) for k, v in list(best_offer_per_date.items())[:2]} }"
            )
        except Exception as e:
            result.notes.append(f"Kayak indisponível: {str(e)[:160]}")
            return result

        if not price_calendar:
            result.notes.append(
                f"Kayak não retornou preços para nenhuma das {total_dates} datas."
            )
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
        # TEMP_LOG_SMART_QUOTE
        print(
            f"[smart_quote] anchor_date={anchor_iso} anchor_carriers={anchor_carriers} "
            f"savings={savings:.2f} already_best={already_best} "
            f"relevant_programs={relevant_programs.get('relevant_programs')}"
        )

        if not relevant_programs.get("relevant_programs"):
            result.notes.append(
                "Nenhum programa de milhas cadastrado cobre as companhias disponíveis "
                "nessa data. Considere emitir em dinheiro."
            )

        return result

    # ── Agente 3 sob demanda ────────────────────────────────────
    def quote_miles_for_date(
        self,
        origin: str,
        destination: str,
        date_iso: str,
        relevant_programs: list[str],
        adults: int = 1,
        return_date: Optional[date] = None,
    ) -> list[dict]:
        """Etapa 2 — Agente 3: cotação BuscaMilhas para uma única data
        escolhida pelo vendedor, restrita aos programas relevantes."""
        return self._quote_miles(
            origin, destination, date_iso, relevant_programs,
            adults, return_date,
        )
