"""Smart Quote endpoints — the original 2-phase intelligent quoting workflow.

Phase 1: `POST /api/v1/smart-quote/explore`
  Runs Kayak across ±N days, returns a calendar with the cheapest price per
  day and the carriers that show up. The seller sees the calendar and picks
  a date.

Phase 2: `POST /api/v1/smart-quote/quote-for-date`
  Given a specific date and the carriers seen on it, dispatches Economilhas
  + BuscaMilhas + Skiplagged in parallel — only for that date. Returns offers
  in the unified format.

Also kept here:
- `POST /api/v1/smart-quote` (legacy "program recommender")
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.domain.models import (
    CabinClass,
    Scenario,
    SearchRequest,
    SourceType,
    TripType,
    UnifiedOffer,
)
from backend.app.providers.buscamilhas.adapter import (
    BuscaMilhasAmericanAdapter,
    BuscaMilhasAzulAdapter,
    BuscaMilhasCopaAdapter,
    BuscaMilhasGolAdapter,
    BuscaMilhasIberiaAdapter,
    BuscaMilhasInterlineAdapter,
    BuscaMilhasLatamAdapter,
    BuscaMilhasTapAdapter,
)
from backend.app.providers.economilhas.adapter import EconomilhasAdapter
from backend.app.providers.kayak.adapter import KayakAdapter
from backend.app.providers.skiplagged.adapter import SkiplaggedAdapter
from backend.app.services.conversion import (
    cost_per_mile,
    skiplagged_estimation_program,
)
from backend.app.services.miles_match import (
    CARRIER_TO_OWN_PROGRAM,
    PROGRAM_COVERAGE,
)


# Markup aplicado ao preço bruto do Kayak para exibição comercial (10%).
KAYAK_MARKUP = 1.10

# Limiar de estabilidade do calendário: se (max-min)/min <= 5%, sinaliza
# "Preços estáveis no período" para o vendedor não perder tempo trocando data.
CALENDAR_STABILITY_THRESHOLD = 0.05

router = APIRouter(tags=["smart-quote"])


# ────────────────────────────────────────────────────────────────────
# Legacy: program recommender (kept for /smart-quote)
# ────────────────────────────────────────────────────────────────────
class SmartQuoteRequest(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    date: date
    adults: int = Field(1, ge=1, le=9)
    estimated_price_brl: float | None = None
    carriers_seen: list[str] = Field(default_factory=list)


class ProgramRecommendation(BaseModel):
    program: str
    label: str
    cost_per_mile_brl: float
    miles_equivalent: int | None = None
    covers_carriers: list[str] = []


class SmartQuoteResponse(BaseModel):
    origin: str
    destination: str
    date: str
    skiplagged_reference_program: str
    programs: list[ProgramRecommendation]


PROGRAM_DISPLAY: dict[str, str] = {
    "SMILES":            "GOL Smiles",
    "LATAM_PASS":        "LATAM Pass",
    "AZUL_FIDELIDADE":   "Azul TudoAzul",
    "AZUL_INTERLINE":    "Azul Pelo Mundo",
    "COPA":              "Copa ConnectMiles",
    "IBERIA":            "Iberia Plus",
    "BRITISH":           "British Avios",
}


# Nome legível por código IATA — usado no card de melhor oferta.
AIRLINE_NAME: dict[str, str] = {
    "G3": "GOL", "LA": "LATAM", "AD": "Azul",
    "AA": "American Airlines", "DL": "Delta", "UA": "United",
    "AC": "Air Canada", "TP": "TAP Air Portugal", "AF": "Air France",
    "KL": "KLM", "LH": "Lufthansa", "BA": "British Airways",
    "IB": "Iberia", "EK": "Emirates", "QR": "Qatar Airways",
    "TK": "Turkish Airlines", "CM": "Copa Airlines", "AV": "Avianca",
    "AM": "Aeroméxico", "AR": "Aerolíneas Argentinas", "AS": "Alaska",
    "JL": "Japan Airlines", "NH": "ANA", "CX": "Cathay Pacific",
    "SQ": "Singapore Airlines", "EY": "Etihad",
}


def _programs_emitting_carrier(carrier_iata: str) -> list[ProgramOnCarrier]:
    """Devolve a lista de programas que emitem para o IATA dado, com flags
    `own_carrier` (programa próprio da cia) e `award_partner` (parceiro
    award real, não só interline). Ordenado por prioridade comercial."""
    c = (carrier_iata or "").upper()
    if not c:
        return []

    out: list[ProgramOnCarrier] = []
    for program, info in PROGRAM_COVERAGE.items():
        if c not in (info.get("carriers") or []):
            continue
        is_own = CARRIER_TO_OWN_PROGRAM.get(c) == program
        is_award = c in (info.get("award_only") or [])
        rate_key = PROGRAM_TO_RATES_KEY.get(program, "DEFAULT")
        rate = cost_per_mile(program=rate_key, miles=50000)
        out.append(ProgramOnCarrier(
            program=program,
            label=PROGRAM_DISPLAY.get(program, program),
            cost_per_mile_brl=rate,
            own_carrier=is_own,
            award_partner=is_award,
        ))

    # Programa próprio primeiro, depois award_partner, depois o resto.
    out.sort(key=lambda p: (
        0 if p.own_carrier else (1 if p.award_partner else 2),
    ))
    return out


def _build_best_offer_on_date(cash_offers: list[UnifiedOffer]) -> BestOfferOnDate | None:
    """Pega o cash mais barato do dia (que não seja hidden-city, para não
    distorcer o preço de venda padrão) e monta o destaque com markup +
    programas que emitem essa companhia."""
    if not cash_offers:
        return None

    eligible = [
        o for o in cash_offers
        if o.price_brl is not None and o.scenario != "hidden_city"
    ]
    if not eligible:
        return None

    best = min(eligible, key=lambda o: o.price_brl or float("inf"))
    out_seg = best.outbound.segments[0] if best.outbound and best.outbound.segments else None
    carrier_iata = (out_seg.carrier if out_seg and out_seg.carrier else "").upper()[:2]

    market = float(best.price_brl or 0.0)
    with_markup = round(market * KAYAK_MARKUP, 2)

    # Card mostra HH:MM (não datetime completo) — extraído aqui.
    dep_str = out_seg.departure_dt.strftime("%H:%M") if out_seg and out_seg.departure_dt else None
    arr_str = out_seg.arrival_dt.strftime("%H:%M") if out_seg and out_seg.arrival_dt else None

    return BestOfferOnDate(
        carrier_iata=carrier_iata,
        carrier_name=AIRLINE_NAME.get(carrier_iata, best.airline or carrier_iata),
        flight_number=(out_seg.flight_number if out_seg else None),
        departure_time=dep_str,
        arrival_time=arr_str,
        stops=best.stops_out,
        duration_min=best.outbound.duration_min if best.outbound else None,
        price_market_brl=round(market, 2),
        price_with_markup_brl=with_markup,
        markup_pct=10.0,
        programs_emitting=_programs_emitting_carrier(carrier_iata),
    )

PROGRAM_TO_RATES_KEY: dict[str, str] = {
    "SMILES":            "GOL",
    "LATAM_PASS":        "LATAM",
    "AZUL_FIDELIDADE":   "AZUL",
    "AZUL_INTERLINE":    "INTERLINE",
    "COPA":              "COPA",
    "IBERIA":            "IBERIA",
    "BRITISH":           "AVIOS",
}


def _relevant_programs(carriers: list[str]) -> list[tuple[str, list[str]]]:
    if not carriers:
        return [(p, []) for p in ("SMILES", "LATAM_PASS", "AZUL_FIDELIDADE")]
    carriers_upper = [c.upper() for c in carriers]
    out: list[tuple[str, list[str]]] = []
    for program, info in PROGRAM_COVERAGE.items():
        covers = [c for c in carriers_upper if c in info.get("carriers", [])]
        if covers:
            out.append((program, covers))
    out.sort(key=lambda t: -len(t[1]))
    return out


@router.post("/smart-quote", response_model=SmartQuoteResponse)
def smart_quote(payload: SmartQuoteRequest) -> SmartQuoteResponse:
    try:
        relevant = _relevant_programs(payload.carriers_seen)
        recs: list[ProgramRecommendation] = []
        for program, covers in relevant:
            rate_key = PROGRAM_TO_RATES_KEY.get(program, "DEFAULT")
            rate = cost_per_mile(program=rate_key, miles=50000)
            miles_equiv = None
            if payload.estimated_price_brl and rate > 0:
                miles_equiv = int(round(payload.estimated_price_brl / rate))
            recs.append(
                ProgramRecommendation(
                    program=program,
                    label=PROGRAM_DISPLAY.get(program, program),
                    cost_per_mile_brl=rate,
                    miles_equivalent=miles_equiv,
                    covers_carriers=covers,
                )
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha no smart-quote: {e}") from e

    return SmartQuoteResponse(
        origin=payload.origin.upper(),
        destination=payload.destination.upper(),
        date=payload.date.isoformat(),
        skiplagged_reference_program=skiplagged_estimation_program(),
        programs=recs,
    )


# ────────────────────────────────────────────────────────────────────
# Phase 1 — Kayak calendar exploration
# ────────────────────────────────────────────────────────────────────
class ExploreRequest(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    date_start: date
    adults: int = Field(1, ge=1, le=9)
    cabin: CabinClass = CabinClass.ECONOMY
    flex_days: int = Field(4, ge=1, le=7, description="±N days around date_start")


class CarrierStat(BaseModel):
    iata: str
    name: str
    min_price_brl: float
    offer_count: int


class DayQuote(BaseModel):
    date: str
    min_price_brl: float | None = None
    offer_count: int = 0
    carriers: list[CarrierStat] = []


class ExploreResponse(BaseModel):
    origin: str
    destination: str
    central_date: str
    days: list[DayQuote]
    best_date: str | None = None
    best_price_brl: float | None = None
    best_carrier_iata: str | None = None
    # Campos para o trio de cards (Melhor dia / Sua data / Economia)
    requested_date: str
    requested_date_price_brl: float | None = None
    savings_brl: float = 0.0
    is_already_best: bool = False
    stability: str = "unknown"  # "stable" | "savings" | "unknown"
    stability_message: str | None = None


def _kayak_for_date(req_template: SearchRequest, target_date: date) -> tuple[date, list[UnifiedOffer]]:
    """Runs the Kayak adapter for one specific date. Absorbs any error."""
    try:
        req = SearchRequest(
            origin=req_template.origin,
            destination=req_template.destination,
            date_start=target_date,
            date_end=target_date,
            adults=req_template.adults,
            cabin=req_template.cabin,
            trip_type=TripType.ONEWAY,
        )
        offers = KayakAdapter().search(req, use_fixtures=False, debug_dump=False)
        return target_date, offers
    except Exception as e:
        print(f"[smart-quote/explore] Kayak failed for {target_date}: {e}")
        return target_date, []


@router.post("/smart-quote/explore", response_model=ExploreResponse)
def explore(payload: ExploreRequest) -> ExploreResponse:
    """Phase 1: explore cash prices via Kayak across ±N days.

    Estratégia: scraper de matriz `kayak.com.br/.../{date}-flexible-Ndays` numa
    única call retorna os preços por data EXATAMENTE como aparecem no site —
    bate com o que o usuário vê. Em paralelo, faz N buscas individuais pra
    ter as ofertas detalhadas por carrier (necessário pra render do card
    "melhor oferta na data"). Quando a matriz devolve um preço, ele
    sobrescreve o min calculado da busca individual.
    """
    central = payload.date_start
    template = SearchRequest(
        origin=[payload.origin.upper()],
        destination=[payload.destination.upper()],
        date_start=central,
        date_end=central,
        adults=payload.adults,
        cabin=payload.cabin,
        trip_type=TripType.ONEWAY,
    )

    today = date.today()
    target_dates = sorted({
        central + timedelta(days=i)
        for i in range(-payload.flex_days, payload.flex_days + 1)
        if central + timedelta(days=i) >= today
    })

    # Matriz Kayak: raspa as 7 (ou menos) datas numa só call.
    # Falhou? Não bloqueia — caímos no comportamento antigo de buscas por data.
    matrix_prices: dict[str, float] = {}
    try:
        from backend.app.providers.kayak.scraper import fetch_kayak_matrix
        matrix_data = fetch_kayak_matrix(
            origin=payload.origin.upper(),
            destination=payload.destination.upper(),
            center_date=central.isoformat(),
            flex_days=payload.flex_days,
        )
        if matrix_data and matrix_data.get("prices_by_date"):
            matrix_prices = matrix_data["prices_by_date"]
    except Exception as e:
        print(f"[smart-quote/explore] Matrix scrape falhou: {e}")

    results: dict[date, list[UnifiedOffer]] = {}
    with ThreadPoolExecutor(max_workers=min(len(target_dates), 8) or 1) as ex:
        futures = [ex.submit(_kayak_for_date, template, d) for d in target_dates]
        for f in as_completed(futures):
            d, offs = f.result()
            results[d] = offs

    days: list[DayQuote] = []
    for d in target_dates:
        offers = results.get(d, [])
        if not offers:
            days.append(DayQuote(date=d.isoformat(), min_price_brl=None, offer_count=0, carriers=[]))
            continue

        # Defesa contra outliers absurdamente baixos do Kayak (mesmo após o
        # filtro do adapter, pode passar caso de borda). Calcula mediana e
        # descarta tudo abaixo de 40% dela — preserva voos com promoção real
        # (geralmente 60-90% da mediana) mas mata buckets de taxa/segmento.
        valid_offers = [o for o in offers if o.airline and o.price_brl is not None]
        if valid_offers:
            prices_sorted = sorted(float(o.price_brl) for o in valid_offers)
            median = prices_sorted[len(prices_sorted) // 2]
            threshold = median * 0.40
            valid_offers = [o for o in valid_offers if float(o.price_brl or 0) >= threshold]

        # Per-carrier stats
        by_carrier: dict[str, list[float]] = {}
        for o in valid_offers:
            iata = (o.outbound.segments[0].carrier if o.outbound and o.outbound.segments else "")[:2].upper()
            key = iata or (o.airline or "?").upper()[:2]
            by_carrier.setdefault(key, []).append(float(o.price_brl))

        carriers = [
            CarrierStat(
                iata=k,
                name=k,
                min_price_brl=min(prices),
                offer_count=len(prices),
            )
            for k, prices in by_carrier.items()
        ]
        carriers.sort(key=lambda c: c.min_price_brl)

        valid_prices = [float(o.price_brl) for o in valid_offers if o.price_brl is not None]
        min_price = min(valid_prices) if valid_prices else None

        # Sobrescreve com o preço da matriz Kayak quando disponível — esse é
        # o preço EXATO que o usuário vê na flex-matrix do site, não o min
        # da busca single-date (que pode diferir por sort=price_a vs curado).
        matrix_price = matrix_prices.get(d.isoformat())
        if matrix_price is not None:
            min_price = float(matrix_price)

        days.append(DayQuote(
            date=d.isoformat(),
            min_price_brl=min_price,
            offer_count=len(valid_offers),
            carriers=carriers,
        ))

    best_day = min(
        (d for d in days if d.min_price_brl is not None),
        key=lambda d: d.min_price_brl or float("inf"),
        default=None,
    )

    requested_iso = central.isoformat()
    requested_day = next((d for d in days if d.date == requested_iso), None)
    requested_price = requested_day.min_price_brl if requested_day else None

    if best_day is None or requested_price is None:
        savings = 0.0
        is_already_best = False
    else:
        is_already_best = (best_day.date == requested_iso)
        savings = max(0.0, requested_price - (best_day.min_price_brl or 0.0))

    valid_prices = [d.min_price_brl for d in days if d.min_price_brl is not None]
    if len(valid_prices) >= 2:
        cmin, cmax = min(valid_prices), max(valid_prices)
        spread = (cmax - cmin) / cmin if cmin > 0 else 0.0
        if spread <= CALENDAR_STABILITY_THRESHOLD:
            stability = "stable"
            stability_message = (
                f"Preços estáveis no período — a tarifa mais barata é praticamente "
                f"a mesma nos {len(valid_prices)} dias. Você pode manter "
                f"{central.strftime('%d/%m/%Y')} sem perda financeira."
            )
        else:
            stability = "savings"
            stability_message = (
                f"Variação de até R$ {cmax - cmin:.2f} no período "
                f"({spread * 100:.1f}%). Vale conferir se a data mais barata "
                f"funciona para o cliente."
            )
    else:
        stability = "unknown"
        stability_message = None

    return ExploreResponse(
        origin=payload.origin.upper(),
        destination=payload.destination.upper(),
        central_date=requested_iso,
        days=days,
        best_date=best_day.date if best_day else None,
        best_price_brl=best_day.min_price_brl if best_day else None,
        best_carrier_iata=(best_day.carriers[0].iata if best_day and best_day.carriers else None),
        requested_date=requested_iso,
        requested_date_price_brl=requested_price,
        savings_brl=savings,
        is_already_best=is_already_best,
        stability=stability,
        stability_message=stability_message,
    )


# ────────────────────────────────────────────────────────────────────
# Phase 2 — Quote miles + Skiplagged for a specific date
# ────────────────────────────────────────────────────────────────────
class QuoteForDateRequest(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    date: date
    return_date: date | None = Field(None, description="Data de volta; quando setada habilita roundtrip")
    adults: int = Field(1, ge=1, le=9)
    cabin: CabinClass = CabinClass.ECONOMY
    include_skiplagged: bool = True
    include_buscamilhas: bool = True
    include_economilhas: bool = True
    include_kayak: bool = True


class ProgramOnCarrier(BaseModel):
    program: str
    label: str
    cost_per_mile_brl: float
    own_carrier: bool = False
    award_partner: bool = False


class BestOfferOnDate(BaseModel):
    """Destaque visual para o melhor cash da data — espelha o card
    'MELHOR OFERTA NA DATA SELECIONADA' do legado."""
    carrier_iata: str
    carrier_name: str
    flight_number: str | None = None
    departure_time: str | None = None
    arrival_time: str | None = None
    stops: int | None = None
    duration_min: int | None = None
    price_market_brl: float
    price_with_markup_brl: float
    markup_pct: float = 10.0
    programs_emitting: list[ProgramOnCarrier] = []


class HiddenCityMilesAlternative(BaseModel):
    """Uma cotação em milhas para o ITINERÁRIO OFICIAL de uma oferta hidden city.

    Ex.: Skiplagged sugere BSB->GIG (com escala em FOR) operado pela GOL como
    hidden city. Aqui cotamos quanto custa esse MESMO bilhete BSB->GIG em
    milhas (Smiles), pra mostrar pro vendedor a alternativa em milhas do
    PNR que ele realmente vai emitir.
    """
    source: str           # "buscamilhas_gol" | "economilhas"
    program_label: str    # "Smiles" | "LATAM Pass" | etc
    miles: int
    taxes_brl: float
    real_cost_brl: float
    flight_number: str | None = None
    departure_time: str | None = None  # "HH:MM"
    arrival_time: str | None = None


class DirectFlightCheck(BaseModel):
    """Cash do voo DIRETO pro destino REAL do passageiro (não o destino oficial
    do bilhete hidden city). Usado pra validar se o hidden city realmente
    economiza vs comprar um voo normal pro destino que o cliente quer."""
    origin: str                        # BSB
    passenger_destination: str         # SSA (onde o cliente vai)
    direct_min_price_brl: float | None = None  # menor cash direto (Kayak)
    direct_carrier_iata: str | None = None     # cia operadora do mais barato
    found_any: bool = False            # houve oferta cash pra essa rota?
    savings_vs_hidden_brl: float | None = None  # hidden - direto; positivo = hidden economiza
    is_hidden_worth_it: bool = False   # hidden city realmente economiza vs voo direto?


class HiddenCityMilesQuote(BaseModel):
    """Pacote completo do que mostramos no itinerário detalhado de uma hidden
    city: a rota oficial do bilhete + onde o passageiro desce + as alternativas
    em milhas para esse mesmo bilhete (com cross-validate) + comparação com
    voo direto pro destino real."""
    official_origin: str          # origem do bilhete (BSB)
    official_destination: str     # destino OFICIAL do bilhete (GIG)
    passenger_destination: str    # onde o passageiro desce (FOR)
    carrier_iata: str             # operating carrier (G3)
    carrier_label: str            # "GOL"
    departure_dt: str | None = None
    alternatives: list[HiddenCityMilesAlternative] = []
    has_validated: bool = False   # alguma alternativa veio do Economilhas?

    # Comparação cash vs milhas pro vendedor decidir rápido qual emitir:
    cash_reference_brl: float | None = None        # preço cash do bilhete Skiplagged original
    cheapest_miles_real_cost_brl: float | None = None  # menor alternativa em milhas
    savings_brl: float | None = None               # cash - miles; positivo = milhas economiza
    recommendation: str = "unknown"                # "cash_cheaper" | "miles_cheaper" | "similar" | "unknown" | "direct_better"

    # Comparativo com voo DIRETO pro destino real do passageiro — única forma
    # de validar se hidden city realmente compensa. Se direto for mais barato,
    # hidden city é só dor de cabeça.
    direct_flight: DirectFlightCheck | None = None


class TableRow(BaseModel):
    """Uma linha da tabela-planilha que espelha o legado: cada perna (ida ou
    volta) de uma oferta vira uma linha indexada por ID curto (G1, L2, K3...).
    O ID encontra a oferta inteira em `flat_offers` para o itinerário detalhado."""
    id: str               # "G1", "L1", "AD1", "K1", ...
    offer_index: int      # índice em flat_offers
    leg: str              # "IDA" | "VOLTA"
    carrier_iata: str
    companhia_label: str  # "GOL", "LATAM", "AZUL", nome legível
    source_label: str     # "BuscaMilhas · Smiles", "Kayak", "Skiplagged"
    scenario: str | None = None   # "hidden_city", "split_cash", "cash_direct", "miles_direct"
    risk_notes: str | None = None # aviso textual (vem de UnifiedOffer.risk_notes)
    layover_official: str | None = None  # destino oficial do bilhete (hidden city)
    date: str             # "DD/MM/YYYY"
    miles: int | None
    taxes_brl: float | None
    real_cost_brl: float | None   # milhas em BRL + taxas (ou só price_brl pra cash)
    price_brl: float | None       # preço cash (None se for milhas pura)
    price_with_markup_brl: float | None  # cash com markup 10% (Kayak)
    price_with_baggage_brl: float | None  # estimativa com bagagem (cash + R$80, milhas + 5k mi)
    duration_min: int | None
    duration_str: str     # "1h20m"
    stops: int
    departure_time: str | None  # "HH:MM"
    arrival_time: str | None
    layover_city: str     # "Direto" ou IATA da conexão
    # Cross-validation entre fontes de milhas:
    #   • is_validated=True quando BuscaMilhas E Economilhas devolveram a
    #     mesma oferta (mesmo voo, mesmo dia, milhas próximas ≤10%).
    #   • validation_sources lista as fontes que confirmaram.
    # Cash (Kayak/Skiplagged) sempre is_validated=True (fonte única é definitiva).
    is_validated: bool = True
    validation_sources: list[str] = []
    # Para linhas de Skiplagged hidden city: cotações em milhas para o
    # ITINERÁRIO OFICIAL completo do bilhete (não pro destino do passageiro).
    # Ex.: Skiplagged sugere BSB->GIG (desce em FOR); aqui mostra "quanto
    # custa em Smiles esse BSB->GIG completo" pra o vendedor saber a
    # alternativa em milhas do mesmo PNR.
    hidden_city_miles: HiddenCityMilesQuote | None = None


class CarrierBucket(BaseModel):
    """Tab por companhia (LATAM, GOL, AZUL, KAYAK, INTERNACIONAL, ALL).
    `rows` já vem ordenado por custo real ascendente; `best` é a linha topo."""
    code: str
    label: str
    rows: list[TableRow] = []
    best: TableRow | None = None
    has_results: bool = False


class VerdictCard(BaseModel):
    """Card do Veredito PcD: Melhor Achado / Melhor em Milhas / Melhor em Dinheiro."""
    kind: str             # "overall" | "miles" | "money"
    label: str            # "MELHOR ACHADO GERAL" etc.
    row: TableRow | None = None
    description: str = ""


class QuoteForDateResponse(BaseModel):
    origin: str
    destination: str
    date: str
    return_date: str | None = None

    # Legado: mantém retro-compat para os clients existentes
    miles_offers: list[UnifiedOffer] = []
    cash_offers: list[UnifiedOffer] = []
    best_offer_on_date: BestOfferOnDate | None = None

    # Cotação Completa (espelha legado)
    flat_offers: list[UnifiedOffer] = []          # tudo numa lista, indexado para itinerário
    buckets: dict[str, CarrierBucket] = {}        # ALL / KAYAK / LATAM / GOL / AZUL / INTL
    bucket_order: list[str] = []                  # ordem das abas na UI
    airline_ranking: list[CarrierBucket] = []     # 3 cards: LATAM, GOL, AZUL (sempre nessa ordem)
    verdict: list[VerdictCard] = []               # 3 cards: overall, miles, money
    summary: str = ""
    # Quando best_overall é milhas mas best_money tem preço cru menor,
    # explica pro vendedor que o cash com markup ficaria mais caro e
    # por isso milhas ganhou — alinha calendário cash (sem markup) com
    # veredito (com markup pra comparar com milhas).
    comparison_note: str | None = None


_BUSCAMILHAS_ADAPTERS: list = [
    BuscaMilhasLatamAdapter,
    BuscaMilhasGolAdapter,
    BuscaMilhasAzulAdapter,
    BuscaMilhasTapAdapter,
    BuscaMilhasIberiaAdapter,
    BuscaMilhasAmericanAdapter,
    BuscaMilhasInterlineAdapter,
    BuscaMilhasCopaAdapter,
]

# Prefixo de ID por bucket — espelha o legado "$1, $2 ... G1, G2 ... L1, L2 ...".
# Cada bucket tem identidade própria; Skiplagged ganha aba dedicada (S) para
# o vendedor diferenciar tarifas hidden city das milhas reais.
ID_PREFIX_BY_BUCKET = {
    "KAYAK":      "K",
    "LATAM":      "L",
    "GOL":        "G",
    "AZUL":       "AD",
    "INTL":       "I",
    "SKIPLAGGED": "S",
}


def _run_adapter_safe(adapter_cls, req: SearchRequest) -> list[UnifiedOffer]:
    try:
        offers = adapter_cls().search(req, use_fixtures=False, debug_dump=False) or []
        # Garante equivalent_brl preenchido em TODAS as ofertas (BuscaMilhas e
        # Economilhas devolvem miles+taxas sem stamp do custo real). Sem isso
        # o quote-complete mostra "Custo Real: —" nas linhas de milhas.
        from backend.app.services.conversion import offer_equivalent_brl
        for o in offers:
            if o.equivalent_brl is None or o.equivalent_brl == 0:
                try:
                    v = offer_equivalent_brl(o)
                    if v and v > 0:
                        o.equivalent_brl = float(v)
                except Exception:
                    pass
        return offers
    except Exception as e:
        print(f"[smart-quote/quote-for-date] {adapter_cls.__name__} failed: {e}")
        return []


def _format_duration(minutes: int | None) -> str:
    if not minutes or minutes <= 0:
        return "—"
    h = minutes // 60
    m = minutes % 60
    if h == 0: return f"{m}min"
    if m == 0: return f"{h}h"
    return f"{h}h{m:02d}m"


def _real_cost(offer: UnifiedOffer) -> float | None:
    """Custo real em BRL: milhas × cost_per_mile + taxas, ou price_brl puro.

    Prioriza `equivalent_brl` quando o adapter já preencheu. Caso contrário
    (BuscaMilhas/Economilhas não setam por default), calcula ad-hoc via
    `offer_equivalent_brl` que respeita a faixa de rates.json do programa.
    """
    if offer.equivalent_brl is not None and offer.equivalent_brl > 0:
        return float(offer.equivalent_brl)
    if offer.miles is not None and offer.miles > 0:
        # Calculo a partir das tarifas do rates.json — taxas já incluídas.
        from backend.app.services.conversion import offer_equivalent_brl
        v = offer_equivalent_brl(offer)
        if v and v > 0:
            return float(v)
    if offer.price_brl is not None:
        return float(offer.price_brl)
    return None


def _bucket_for_carrier(carrier_iata: str, source: str, has_miles: bool = True) -> str:
    """Decide qual aba a oferta vai parar:
      • source=kayak       → KAYAK     (cash benchmark de mercado)
      • source=skiplagged  → SKIPLAGGED (hidden city / split cash, aba dedicada)
      • cash de outros provedores (BuscaMilhas "Pagante" / Economilhas cash)
        → KAYAK (mantém abas de milhas só com milhas reais)
      • milhas, agrupadas por carrier IATA:
        LA→LATAM, G3→GOL, AD→AZUL, outros→INTL

    As abas LATAM/GOL/AZUL ficam reservadas para MILHAS reais — nenhum
    cash vaza pra elas, garantindo que a coluna "Milhas" nunca fique vazia.
    """
    src = (source or "").lower()
    if src == "kayak":
        return "KAYAK"
    if src == "skiplagged":
        return "SKIPLAGGED"
    if not has_miles:
        # Cash em provedor de milhas (BuscaMilhas modo Pagante etc) →
        # consolidamos com KAYAK pra não bagunçar as abas de milhas.
        return "KAYAK"
    c = (carrier_iata or "").upper()
    if c == "LA": return "LATAM"
    if c == "G3": return "GOL"
    if c == "AD": return "AZUL"
    return "INTL"


def _build_table_row(
    offer: UnifiedOffer,
    offer_index: int,
    row_id: str,
    leg: str,           # "IDA" | "VOLTA"
    itinerary,           # offer.outbound ou offer.inbound
    is_validated: bool = True,
    validation_sources: list[str] | None = None,
    hidden_city_miles: HiddenCityMilesQuote | None = None,
) -> TableRow:
    seg0 = itinerary.segments[0] if itinerary and itinerary.segments else None
    seg_last = itinerary.segments[-1] if itinerary and itinerary.segments else None
    carrier_iata = (seg0.carrier if seg0 and seg0.carrier else "")[:3].upper()
    stops = max(0, len(itinerary.segments) - 1) if itinerary and itinerary.segments else 0
    layover = "Direto" if stops == 0 else (itinerary.segments[0].destination if stops > 0 and itinerary.segments else "—")

    real_cost = _real_cost(offer)
    miles = offer.miles
    taxes = offer.taxes_brl
    is_kayak_cash = (offer.source.value.lower() if hasattr(offer.source, "value") else str(offer.source).lower()) == "kayak"

    # PREÇO FINAL na planilha: para cash = price_brl, para milhas = real_cost
    # (custo total em R$ se converter milhas a mercado). Nunca fica vazio
    # quando temos dados pra calcular.
    if offer.price_brl is not None:
        price_final = float(offer.price_brl)
    elif miles is not None and real_cost is not None:
        price_final = float(real_cost)
    else:
        price_final = None

    # Markup 10% só faz sentido para cash do Kayak.
    price_with_markup = round(price_final * KAYAK_MARKUP, 2) if (is_kayak_cash and price_final is not None) else None

    # VALOR C/ MALA: cash + R$80 fixo, milhas + 5000 mi adicionais (estimativa).
    if miles is not None and real_cost is not None and miles > 0:
        rate_per_mile = (real_cost - float(taxes or 0)) / miles
        price_with_bag = round(real_cost + 5000 * rate_per_mile, 2)
    elif price_final is not None:
        base = price_with_markup if price_with_markup is not None else price_final
        price_with_bag = round(base + 80.0, 2)
    else:
        price_with_bag = None

    src_label_map = {
        "buscamilhas_latam": "BuscaMilhas · LATAM Pass",
        "buscamilhas_gol": "BuscaMilhas · Smiles",
        "buscamilhas_azul": "BuscaMilhas · TudoAzul",
        "buscamilhas_tap": "BuscaMilhas · Miles&Go",
        "buscamilhas_iberia": "BuscaMilhas · Iberia",
        "buscamilhas_american": "BuscaMilhas · AAdvantage",
        "buscamilhas_interline": "BuscaMilhas · Interline",
        "buscamilhas_copa": "BuscaMilhas · ConnectMiles",
        "economilhas": "Economilhas",
        "mcp_award": "MCP Award",
        "mcp_qatar": "MCP · Qatar",
        "kayak": "Kayak",
        "skiplagged": "Skiplagged",
    }
    src_key = offer.source.value if hasattr(offer.source, "value") else str(offer.source)
    src_label = src_label_map.get(src_key.lower(), src_key)

    scenario_value = offer.scenario.value if (offer.scenario and hasattr(offer.scenario, "value")) else (str(offer.scenario) if offer.scenario else None)

    return TableRow(
        id=row_id,
        offer_index=offer_index,
        leg=leg,
        carrier_iata=carrier_iata,
        companhia_label=AIRLINE_NAME.get(carrier_iata, offer.airline or carrier_iata),
        source_label=src_label,
        scenario=scenario_value,
        risk_notes=offer.risk_notes,
        layover_official=offer.layover_city,
        date=seg0.departure_dt.strftime("%d/%m/%Y") if seg0 and seg0.departure_dt else "—",
        miles=miles,
        taxes_brl=float(taxes) if taxes is not None else None,
        real_cost_brl=real_cost,
        price_brl=price_final,
        price_with_markup_brl=price_with_markup,
        price_with_baggage_brl=price_with_bag,
        duration_min=itinerary.duration_min if itinerary else None,
        duration_str=_format_duration(itinerary.duration_min if itinerary else None),
        stops=stops,
        departure_time=seg0.departure_dt.strftime("%H:%M") if seg0 and seg0.departure_dt else None,
        arrival_time=seg_last.arrival_dt.strftime("%H:%M") if seg_last and seg_last.arrival_dt else None,
        layover_city=layover,
        is_validated=is_validated,
        validation_sources=validation_sources or [],
        hidden_city_miles=hidden_city_miles,
    )


# Mapa carrier IATA → adapter BuscaMilhas que cota milhas naquela cia.
# Cobre programas brasileiros próprios (G3 Smiles, LA LATAM Pass, AD TudoAzul)
# e parceiros internacionais (TP Miles&Go, IB Iberia Plus, etc.).
_CARRIER_TO_BM_ADAPTER: dict[str, type] = {
    "G3": BuscaMilhasGolAdapter,
    "LA": BuscaMilhasLatamAdapter,
    "AD": BuscaMilhasAzulAdapter,
    "TP": BuscaMilhasTapAdapter,
    "IB": BuscaMilhasIberiaAdapter,
    "AA": BuscaMilhasAmericanAdapter,
    "CM": BuscaMilhasCopaAdapter,
}

_CARRIER_TO_PROGRAM_LABEL: dict[str, str] = {
    "G3": "Smiles (GOL)",
    "LA": "LATAM Pass",
    "AD": "TudoAzul",
    "TP": "TAP Miles&Go",
    "IB": "Iberia Plus",
    "AA": "AAdvantage",
    "CM": "Copa ConnectMiles",
}


def _check_direct_flight(
    origin: str,
    passenger_dest: str,
    target_date,
    adults: int,
    cabin,
    hidden_cash_brl: float | None = None,
) -> DirectFlightCheck:
    """Faz uma busca Kayak pelo voo DIRETO entre origin e o destino REAL do
    passageiro (não o oficial do bilhete hidden city). Devolve o menor cash
    válido e compara com o hidden city pra dizer se vale a pena."""
    check = DirectFlightCheck(origin=origin, passenger_destination=passenger_dest)
    try:
        req = SearchRequest(
            origin=[origin],
            destination=[passenger_dest],
            date_start=target_date,
            date_end=target_date,
            adults=adults,
            cabin=cabin,
            trip_type=TripType.ONEWAY,
        )
        offers = _run_adapter_safe(KayakAdapter, req)
    except Exception:
        offers = []

    valid = [o for o in offers if o.price_brl and o.airline and o.price_brl > 30]
    if not valid:
        return check

    # Filtra outliers via mediana (mesmo padrão do /explore)
    prices = sorted(float(o.price_brl) for o in valid)
    median = prices[len(prices) // 2]
    valid = [o for o in valid if float(o.price_brl or 0) >= median * 0.40]
    if not valid:
        return check

    cheapest = min(valid, key=lambda o: float(o.price_brl or 9e9))
    seg = cheapest.outbound.segments[0] if cheapest.outbound and cheapest.outbound.segments else None
    iata = (seg.carrier if seg and seg.carrier else "")[:3].upper()

    check.found_any = True
    check.direct_min_price_brl = float(cheapest.price_brl)
    check.direct_carrier_iata = iata or None

    if hidden_cash_brl is not None:
        check.savings_vs_hidden_brl = hidden_cash_brl - float(cheapest.price_brl)
        # Hidden city só "compensa" se economiza pelo menos 10% vs o direto;
        # senão a complicação (sem bagagem, sem milhagem, risco PNR) não vale.
        check.is_hidden_worth_it = (
            check.savings_vs_hidden_brl is not None
            and check.savings_vs_hidden_brl > hidden_cash_brl * 0.10
        )
    return check


def _classify_recommendation(
    cash_brl: float | None, cheapest_miles_brl: float | None,
) -> tuple[float | None, str]:
    """Compara cash vs milhas e devolve (savings_brl, recommendation).
    `savings_brl` positivo = milhas economiza; negativo = cash economiza.
    Tolerância de 5% considera 'similar'."""
    if cash_brl is None or cheapest_miles_brl is None:
        return None, "unknown"
    diff = cash_brl - cheapest_miles_brl   # >0: cash > miles → milhas economiza
    tol = max(cash_brl * 0.05, 30.0)
    if abs(diff) <= tol:
        return diff, "similar"
    if diff > 0:
        return diff, "miles_cheaper"
    return diff, "cash_cheaper"


def _build_hidden_city_quote(
    carrier_iata: str,
    origin: str,
    official_dest: str,
    passenger_dest: str,
    departure_dt,           # datetime ou None — para filtrar match por horário
    adults: int,
    cabin,
    cash_reference_brl: float | None = None,
    direct_check: DirectFlightCheck | None = None,
) -> HiddenCityMilesQuote | None:
    """Para UM itinerário oficial (BSB→GIG, pela GOL, 15/06), cota milhas em
    paralelo no BuscaMilhas do programa próprio + Economilhas. Retorna None
    quando o carrier não tem programa BR mapeado nem alternativas encontradas."""
    adapter_cls = _CARRIER_TO_BM_ADAPTER.get(carrier_iata)
    req = SearchRequest(
        origin=[origin],
        destination=[official_dest],
        date_start=departure_dt.date() if departure_dt else date.today(),
        date_end=departure_dt.date() if departure_dt else date.today(),
        adults=adults,
        cabin=cabin,
        trip_type=TripType.ONEWAY,
    )
    miles_results: list[UnifiedOffer] = []
    tasks = []
    if adapter_cls is not None:
        tasks.append(adapter_cls)
    tasks.append(EconomilhasAdapter)
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = [ex.submit(_run_adapter_safe, cls, req) for cls in tasks]
        for f in as_completed(futures):
            try:
                miles_results.extend(f.result())
            except Exception:
                pass

    # Filtra ofertas com mesmo carrier; horário não obrigatório (BuscaMilhas
    # pode devolver voos do dia em horários diferentes — tudo serve como
    # "alternativa em milhas para esse mesmo trecho oficial").
    matched = [
        mo for mo in miles_results
        if mo.miles is not None
        and mo.outbound and mo.outbound.segments
        and (mo.outbound.segments[0].carrier or "").upper()[:3] == carrier_iata
    ]
    # Ordena por milhas asc
    matched.sort(key=lambda mo: mo.miles or 0)
    # Dedup por (flight_number, miles, source) — Economilhas e BuscaMilhas
    # podem devolver o mesmo voo duplicado.
    seen: set[tuple] = set()
    alts: list[HiddenCityMilesAlternative] = []
    for mo in matched:
        mseg = mo.outbound.segments[0]
        mseg_last = mo.outbound.segments[-1]
        src = mo.source.value if hasattr(mo.source, "value") else str(mo.source)
        key = (mseg.flight_number or "", mo.miles, src)
        if key in seen:
            continue
        seen.add(key)
        program = _CARRIER_TO_PROGRAM_LABEL.get(carrier_iata, carrier_iata)
        if src.lower() == "economilhas":
            program = f"{program} via Economilhas"
        alts.append(HiddenCityMilesAlternative(
            source=src,
            program_label=program,
            miles=int(mo.miles),
            taxes_brl=float(mo.taxes_brl or 0),
            real_cost_brl=float(mo.equivalent_brl or 0),
            flight_number=mseg.flight_number,
            departure_time=mseg.departure_dt.strftime("%H:%M") if mseg.departure_dt else None,
            arrival_time=mseg_last.arrival_dt.strftime("%H:%M") if mseg_last.arrival_dt else None,
        ))
        if len(alts) >= 8:
            break

    # Se voo direto pro destino real é mais barato e a economia do hidden city
    # é < 10%, sobrescreve recomendação pra "direct_better" — alerta o vendedor.
    def _maybe_override_with_direct(rec: str) -> str:
        if not direct_check or not direct_check.found_any or cash_reference_brl is None:
            return rec
        if direct_check.direct_min_price_brl is None:
            return rec
        # Direto MAIS BARATO que hidden city → hidden city é furada
        if direct_check.direct_min_price_brl < cash_reference_brl * 0.95:
            return "direct_better"
        return rec

    if not alts:
        rec = "cash_cheaper" if cash_reference_brl else "unknown"
        rec = _maybe_override_with_direct(rec)
        return HiddenCityMilesQuote(
            official_origin=origin,
            official_destination=official_dest,
            passenger_destination=passenger_dest,
            carrier_iata=carrier_iata,
            carrier_label=AIRLINE_NAME.get(carrier_iata, carrier_iata),
            departure_dt=departure_dt.isoformat() if departure_dt else None,
            alternatives=[],
            has_validated=False,
            cash_reference_brl=cash_reference_brl,
            recommendation=rec,
            direct_flight=direct_check,
        )

    sources_used = {alt.source.lower() for alt in alts}
    cheapest_miles_real = min(alt.real_cost_brl for alt in alts)
    savings, rec = _classify_recommendation(cash_reference_brl, cheapest_miles_real)
    rec = _maybe_override_with_direct(rec)

    return HiddenCityMilesQuote(
        official_origin=origin,
        official_destination=official_dest,
        passenger_destination=passenger_dest,
        carrier_iata=carrier_iata,
        carrier_label=AIRLINE_NAME.get(carrier_iata, carrier_iata),
        departure_dt=departure_dt.isoformat() if departure_dt else None,
        alternatives=alts,
        has_validated="economilhas" in sources_used,
        cash_reference_brl=cash_reference_brl,
        cheapest_miles_real_cost_brl=cheapest_miles_real,
        savings_brl=savings,
        recommendation=rec,
        direct_flight=direct_check,
    )


def _quote_hidden_city_alternatives(
    hidden_offers: list[UnifiedOffer],
    adults: int,
    cabin,
    max_groups: int = 5,
) -> dict[int, HiddenCityMilesQuote]:
    """Para cada oferta Skiplagged hidden city, cota milhas no itinerário
    OFICIAL completo (BSB→GIG, não pro destino do passageiro FOR).

    Estratégia:
      1. Agrupa hidden offers por (carrier_iata, origin, official_dest, date).
      2. Para cada grupo único, dispara 1 chamada ao BuscaMilhas do programa
         daquela cia (G3→Smiles, LA→LATAM Pass, AD→TudoAzul, etc.) + 1 ao
         Economilhas. Em paralelo, com cache via _run_adapter_safe.
      3. Match das ofertas de milhas com a oferta original por carrier +
         departure_dt ±60min.
      4. Cap em max_groups grupos para não explodir quota.

    Retorna {id(offer): HiddenCityMilesQuote}.
    """
    from collections import defaultdict

    if not hidden_offers:
        return {}

    # Agrupar
    groups: dict[tuple, list[UnifiedOffer]] = defaultdict(list)
    for o in hidden_offers:
        if not o.outbound or not o.outbound.segments:
            continue
        seg0 = o.outbound.segments[0]
        seg_last = o.outbound.segments[-1]
        carrier = (seg0.carrier or "").upper()[:3]
        if not seg0.departure_dt or not carrier:
            continue
        key = (carrier, seg0.origin, seg_last.destination, seg0.departure_dt.date().isoformat())
        groups[key].append(o)

    # Cap pra economizar quota — pega os grupos com offer mais barata
    sorted_groups = sorted(
        groups.items(),
        key=lambda kv: min((float(o.price_brl or 1e9) for o in kv[1])),
    )[:max_groups]

    out: dict[int, HiddenCityMilesQuote] = {}

    def _query_one_group(key: tuple, sample_offers: list[UnifiedOffer]) -> tuple[tuple, list[UnifiedOffer]]:
        carrier, origin, official_dest, date_iso = key
        req = SearchRequest(
            origin=[origin],
            destination=[official_dest],
            date_start=date.fromisoformat(date_iso),
            date_end=date.fromisoformat(date_iso),
            adults=adults,
            cabin=cabin,
            trip_type=TripType.ONEWAY,
        )
        miles_results: list[UnifiedOffer] = []
        adapter_cls = _CARRIER_TO_BM_ADAPTER.get(carrier)
        if adapter_cls is not None:
            miles_results.extend(_run_adapter_safe(adapter_cls, req))
        # Economilhas cobre os principais BR + parceiros
        miles_results.extend(_run_adapter_safe(EconomilhasAdapter, req))
        # Filtra: só milhas, mesmo carrier do hidden city
        matched = [
            mo for mo in miles_results
            if mo.miles is not None
            and mo.outbound and mo.outbound.segments
            and (mo.outbound.segments[0].carrier or "").upper()[:3] == carrier
        ]
        return key, matched

    # Roda os grupos em paralelo
    group_results: dict[tuple, list[UnifiedOffer]] = {}
    with ThreadPoolExecutor(max_workers=min(len(sorted_groups), 6) or 1) as ex:
        futures = [ex.submit(_query_one_group, k, off) for k, off in sorted_groups]
        for f in as_completed(futures):
            try:
                key, matched = f.result()
                group_results[key] = matched
            except Exception as e:
                print(f"[hidden_city_quote] grupo falhou: {e}")

    # Voos diretos pro destino REAL do passageiro: agrupa por (origin,
    # passenger_dest, date) — N hidden city pra mesmo destino real precisam
    # de só 1 busca Kayak. Map de cash mín do grupo para anexar à decisão.
    direct_groups: dict[tuple, list[UnifiedOffer]] = defaultdict(list)
    for o in hidden_offers:
        if not o.outbound or not o.outbound.segments or not o.layover_city:
            continue
        seg0 = o.outbound.segments[0]
        if not seg0.departure_dt:
            continue
        key = (seg0.origin, o.layover_city, seg0.departure_dt.date().isoformat())
        direct_groups[key].append(o)

    direct_results: dict[tuple, DirectFlightCheck] = {}

    def _query_direct(key: tuple, sample_offers: list[UnifiedOffer]) -> tuple[tuple, DirectFlightCheck]:
        ori, dest, date_iso = key
        cash_min = min((float(o.price_brl) for o in sample_offers if o.price_brl), default=None)
        check = _check_direct_flight(
            origin=ori, passenger_dest=dest,
            target_date=date.fromisoformat(date_iso),
            adults=adults, cabin=cabin, hidden_cash_brl=cash_min,
        )
        return key, check

    if direct_groups:
        with ThreadPoolExecutor(max_workers=min(len(direct_groups), 6) or 1) as ex:
            futures = [ex.submit(_query_direct, k, off) for k, off in list(direct_groups.items())[:max_groups]]
            for f in as_completed(futures):
                try:
                    key, check = f.result()
                    direct_results[key] = check
                except Exception as e:
                    print(f"[hidden_city_direct] grupo falhou: {e}")

    # Constrói HiddenCityMilesQuote por offer
    for key, offers_in_group in sorted_groups:
        carrier, origin, official_dest, date_iso = key
        matched = group_results.get(key, [])
        # Cash de referência do grupo: oferta Skiplagged mais barata desse trecho.
        cash_ref = None
        cash_offers = [float(o.price_brl) for o in offers_in_group if o.price_brl]
        if cash_offers:
            cash_ref = min(cash_offers)

        for o in offers_in_group:
            seg0 = o.outbound.segments[0]
            # Filtra matched que tem horário compatível (±60min).
            # Skiplagged devolve datetime tz-aware, BuscaMilhas naive —
            # normalizamos para naive antes de comparar.
            compatible = []
            seg0_dt = seg0.departure_dt.replace(tzinfo=None) if seg0.departure_dt else None
            for mo in matched:
                mseg = mo.outbound.segments[0]
                if not mseg.departure_dt or not seg0_dt:
                    continue
                m_dt = mseg.departure_dt.replace(tzinfo=None)
                delta_min = abs((m_dt - seg0_dt).total_seconds()) / 60
                if delta_min <= 60:
                    compatible.append(mo)
            # Ordena por milhas asc
            compatible.sort(key=lambda mo: mo.miles or 0)
            # Pega top 5 alternativas
            alts = []
            for mo in compatible[:5]:
                mseg = mo.outbound.segments[0]
                mseg_last = mo.outbound.segments[-1]
                src = mo.source.value if hasattr(mo.source, "value") else str(mo.source)
                program = _CARRIER_TO_PROGRAM_LABEL.get(carrier, carrier)
                if src.lower() == "economilhas":
                    program = f"{program} via Economilhas"
                alts.append(HiddenCityMilesAlternative(
                    source=src,
                    program_label=program,
                    miles=int(mo.miles),
                    taxes_brl=float(mo.taxes_brl or 0),
                    real_cost_brl=float(mo.equivalent_brl or 0),
                    flight_number=mseg.flight_number,
                    departure_time=mseg.departure_dt.strftime("%H:%M") if mseg.departure_dt else None,
                    arrival_time=mseg_last.arrival_dt.strftime("%H:%M") if mseg_last.arrival_dt else None,
                ))
            if not alts:
                continue
            sources_used = {alt.source.lower() for alt in alts}
            offer_cash = float(o.price_brl) if o.price_brl else cash_ref
            cheapest_miles_real = min(alt.real_cost_brl for alt in alts)
            savings, rec = _classify_recommendation(offer_cash, cheapest_miles_real)

            # Pega o DirectFlightCheck do grupo (origin, passenger_dest, date) desta offer.
            direct_key = (origin, o.layover_city or "", date_iso)
            direct_check = direct_results.get(direct_key)
            # Se voo direto mais barato, sobrepõe recomendação para alertar.
            if (
                direct_check and direct_check.found_any
                and direct_check.direct_min_price_brl is not None
                and offer_cash is not None
                and direct_check.direct_min_price_brl < offer_cash * 0.95
            ):
                rec = "direct_better"

            out[id(o)] = HiddenCityMilesQuote(
                official_origin=origin,
                official_destination=official_dest,
                passenger_destination=o.layover_city or "",
                carrier_iata=carrier,
                carrier_label=AIRLINE_NAME.get(carrier, carrier),
                departure_dt=seg0.departure_dt.isoformat() if seg0.departure_dt else None,
                alternatives=alts,
                has_validated="economilhas" in sources_used,
                cash_reference_brl=offer_cash,
                cheapest_miles_real_cost_brl=cheapest_miles_real,
                savings_brl=savings,
                recommendation=rec,
                direct_flight=direct_check,
            )

    return out


def _cross_validate_miles(offers: list[UnifiedOffer]) -> dict[int, tuple[bool, list[str]]]:
    """Regra atual (vendedor solicitou Economilhas como fonte de verdade):

    Uma oferta de milhas é considerada **validada** quando vem do Economilhas
    diretamente, OU quando o BuscaMilhas reporta o mesmo voo (carrier +
    flight_number + data + horário ±15min) que o Economilhas também devolveu.

    Em ambos os casos o tag "✓ validado" significa: "esse voo apareceu no
    Economilhas". Ofertas exclusivas do BuscaMilhas (sem espelho no
    Economilhas) ficam como **fonte única** e o vendedor decide se aceita.

    Cash (sem miles) sempre é considerado validado — fonte única é definitiva.
    """
    out: dict[int, tuple[bool, list[str]]] = {}

    miles_offers = [o for o in offers if o.miles is not None]
    cash_offers = [o for o in offers if o.miles is None]
    for o in cash_offers:
        out[id(o)] = (True, [str(o.source.value if hasattr(o.source, "value") else o.source)])

    from collections import defaultdict
    groups: dict[tuple, list[UnifiedOffer]] = defaultdict(list)

    for o in miles_offers:
        seg = o.outbound.segments[0] if o.outbound and o.outbound.segments else None
        if not seg or not seg.departure_dt:
            out[id(o)] = (False, [str(o.source.value if hasattr(o.source, "value") else o.source)])
            continue
        carrier = (seg.carrier or "")[:3].upper()
        flight_raw = (seg.flight_number or "").upper().replace(" ", "").replace("-", "")
        flight_digits = "".join(ch for ch in flight_raw if ch.isdigit())
        date_key = seg.departure_dt.date().isoformat()
        # Bucket de 30 min — tolera diferença de horário até ~15min.
        hour_bucket = (seg.departure_dt.hour * 60 + seg.departure_dt.minute) // 30
        key = (carrier, flight_digits, date_key, hour_bucket)
        groups[key].append(o)

    for key, group_offers in groups.items():
        sources_in_group: list[str] = []
        for o in group_offers:
            src = o.source.value if hasattr(o.source, "value") else str(o.source)
            if src not in sources_in_group:
                sources_in_group.append(src)

        # Regra simples: passou pelo Economilhas → validado.
        has_economilhas = any(s.lower() == "economilhas" for s in sources_in_group)
        for o in group_offers:
            out[id(o)] = (has_economilhas, sources_in_group)

    return out


def _build_buckets_and_verdict(
    offers: list[UnifiedOffer],
    hidden_city_quotes: dict[int, HiddenCityMilesQuote] | None = None,
) -> tuple[
    dict[str, CarrierBucket], list[str], list[CarrierBucket], list[VerdictCard], str, str | None,
]:
    """Constrói buckets por cia (ALL/KAYAK/LATAM/GOL/AZUL/INTL), o ranking
    por companhia (3 cards LATAM/GOL/AZUL), o veredito (3 cards) e o resumo.
    Cada oferta vira 1 ou 2 TableRows (IDA + VOLTA se roundtrip)."""
    if not offers:
        return {}, [], [], [], "Nenhuma oferta encontrada para esta data.", None

    rows: list[TableRow] = []
    bucket_counters: dict[str, int] = {}

    # Ordena ofertas por custo real para que os IDs (G1, G2, L1, L2) saiam por preço
    offers_sorted = sorted(
        offers,
        key=lambda o: _real_cost(o) if _real_cost(o) is not None else float("inf"),
    )

    # Cross-validate antes de criar TableRows para já marcar is_validated.
    validation_map = _cross_validate_miles(offers_sorted)

    # Cada row carrega seu bucket atribuído na criação para evitar
    # double-detect inconsistente (carrier vs source string).
    row_to_bucket: list[tuple[TableRow, str]] = []

    for idx, offer in enumerate(offers_sorted):
        out_seg = offer.outbound.segments[0] if offer.outbound and offer.outbound.segments else None
        carrier_iata = (out_seg.carrier if out_seg and out_seg.carrier else "")[:3].upper()
        source_str = offer.source.value if hasattr(offer.source, "value") else str(offer.source)
        bucket_key = _bucket_for_carrier(carrier_iata, source_str, has_miles=offer.miles is not None)
        prefix = ID_PREFIX_BY_BUCKET.get(bucket_key, "X")
        bucket_counters[bucket_key] = bucket_counters.get(bucket_key, 0) + 1
        base_id = f"{prefix}{bucket_counters[bucket_key]}"

        validated, val_sources = validation_map.get(id(offer), (True, []))
        hidden_quote = (hidden_city_quotes or {}).get(id(offer))

        # IDA sempre existe
        out_row = _build_table_row(offer, idx, base_id, "IDA", offer.outbound, validated, val_sources, hidden_quote)
        rows.append(out_row)
        row_to_bucket.append((out_row, bucket_key))
        # VOLTA quando roundtrip
        if offer.inbound:
            in_row = _build_table_row(offer, idx, base_id, "VOLTA", offer.inbound, validated, val_sources, hidden_quote)
            rows.append(in_row)
            row_to_bucket.append((in_row, bucket_key))

    # Buckets — ordem espelha o legado: Veredito > Ranking > Kayak > LATAM >
    # GOL > AZUL > Internacional (MCP/outros programas) > Skiplagged (último).
    buckets: dict[str, CarrierBucket] = {
        "ALL":        CarrierBucket(code="ALL",        label="Ranking Geral"),
        "KAYAK":      CarrierBucket(code="KAYAK",      label="Dinheiro (Kayak)"),
        "LATAM":      CarrierBucket(code="LATAM",      label="LATAM (milhas)"),
        "GOL":        CarrierBucket(code="GOL",        label="GOL (milhas)"),
        "AZUL":       CarrierBucket(code="AZUL",       label="AZUL (milhas)"),
        "INTL":       CarrierBucket(code="INTL",       label="Internacional (milhas)"),
        "SKIPLAGGED": CarrierBucket(code="SKIPLAGGED", label="Skiplagged (Hidden City)"),
    }

    for r, bucket_key in row_to_bucket:
        if bucket_key not in buckets:
            bucket_key = "INTL"
        buckets[bucket_key].rows.append(r)
        buckets["ALL"].rows.append(r)

    # Critério de comparação justa entre cash e milhas:
    #   • Cash Kayak vai pra cliente COM markup 10% — esse é o valor real
    #     que ele paga. Usamos price_with_markup_brl quando disponível.
    #   • Milhas vão pelo custo real (taxas + milhas × rate), sem markup.
    #   • Skiplagged usa custo real cru.
    # Isso evita o viés histórico do "cash R$ 2.466 vence milhas R$ 2.578"
    # quando na realidade cash com markup vira R$ 2.713 → milhas economiza.
    def _comparable_cost(r: TableRow) -> float:
        if r.price_with_markup_brl is not None:
            return r.price_with_markup_brl
        if r.real_cost_brl is not None:
            return r.real_cost_brl
        return float("inf")

    def _cost_key(r: TableRow) -> float:
        # Para abas individuais, usar custo real puro (sem markup) — dentro
        # da aba todos têm a mesma natureza.
        return r.real_cost_brl if r.real_cost_brl is not None else float("inf")

    def _confidence_priority(r: TableRow) -> int:
        """Menor número = maior prioridade no Ranking Geral.
        0 = milhas validadas pelo Economilhas (dados completos + confirmados)
        1 = cash Kayak (benchmark de mercado, fonte única definitiva)
        2 = milhas fonte única (BuscaMilhas sem espelho no Economilhas)
        3 = Skiplagged (hidden city / split — requer revisão manual)
        """
        src = (r.source_label or "").lower()
        if src.startswith("skiplagged"):
            return 3
        if r.miles is not None:
            return 0 if r.is_validated else 2
        return 1

    for code, bucket in buckets.items():
        if code == "ALL":
            # Ranking Geral: estratificado por confiança E comparado em base
            # justa (cash com markup vs milhas sem markup).
            bucket.rows.sort(key=lambda r: (_confidence_priority(r), _comparable_cost(r)))
        else:
            bucket.rows.sort(key=_cost_key)
        bucket.has_results = len(bucket.rows) > 0
        bucket.best = bucket.rows[0] if bucket.rows else None

    bucket_order = ["ALL", "KAYAK", "LATAM", "GOL", "AZUL", "INTL", "SKIPLAGGED"]

    # Veredito — `buckets["ALL"].rows` já vem estratificado por confiança,
    # então rows[0] É o melhor entre os mais confiáveis. Para MELHOR EM
    # MILHAS, preferimos voo validado pelo Economilhas; só caímos em fonte
    # única quando não há validados.
    miles_validated = [r for r in buckets["ALL"].rows if r.miles is not None and r.is_validated]
    miles_unvalidated = [r for r in buckets["ALL"].rows if r.miles is not None and not r.is_validated]
    money_rows = [r for r in buckets["ALL"].rows if r.miles is None]

    best_overall = buckets["ALL"].rows[0] if buckets["ALL"].rows else None
    # MELHOR EM MILHAS: validado vence; sem validados, mostra o melhor não-validado.
    if miles_validated:
        best_miles = min(miles_validated, key=lambda r: r.real_cost_brl or float("inf"))
    elif miles_unvalidated:
        best_miles = min(miles_unvalidated, key=lambda r: r.real_cost_brl or float("inf"))
    else:
        best_miles = None
    # MELHOR EM DINHEIRO: prefere Kayak puro sobre Skiplagged (hidden city é arriscado).
    # Critério de menor preço aqui é o MERCADO Kayak (sem markup) — é o que o
    # card mostra como destaque visual; o markup aparece na linha de baixo.
    kayak_cash = [r for r in money_rows if not (r.source_label or "").lower().startswith("skiplagged")]
    if kayak_cash:
        best_money = min(kayak_cash, key=lambda r: r.real_cost_brl or float("inf"))
    elif money_rows:
        best_money = min(money_rows, key=lambda r: r.real_cost_brl or float("inf"))
    else:
        best_money = None

    verdict: list[VerdictCard] = [
        VerdictCard(
            kind="overall",
            label="MELHOR ACHADO GERAL",
            row=best_overall,
            description=(
                f"{best_overall.companhia_label} · custo real (milhas + taxas)"
                if best_overall and best_overall.miles is not None
                else f"{best_overall.companhia_label} · em dinheiro" if best_overall else ""
            ),
        ),
        VerdictCard(
            kind="miles",
            label="MELHOR EM MILHAS",
            row=best_miles,
            description=(
                f"{best_miles.companhia_label} · {best_miles.miles:,} milhas (custo real)".replace(",", ".")
                if best_miles else "Sem ofertas em milhas para esta data"
            ),
        ),
        VerdictCard(
            kind="money",
            label="MELHOR EM DINHEIRO",
            row=best_money,
            description=(
                f"{best_money.companhia_label} · {best_money.source_label}"
                if best_money else "Sem ofertas cash para esta data"
            ),
        ),
    ]

    # Ranking por Companhia (3 cards sempre — vazio com "Sem resultado" quando faltam)
    airline_ranking = [
        buckets["LATAM"],
        buckets["GOL"],
        buckets["AZUL"],
    ]

    # Resumo textual
    if best_overall:
        if best_overall.miles is not None:
            summary = (
                f"A melhor opção foi {best_overall.companhia_label} em milhas. "
                f"Custo real: R$ {best_overall.real_cost_brl:,.2f} — composto por "
                f"{best_overall.miles:,} milhas".replace(",", ".") +
                (f" (≈ R$ {(best_overall.real_cost_brl - (best_overall.taxes_brl or 0)):,.2f})".replace(",", ".") if best_overall.taxes_brl else "") +
                (f" + R$ {best_overall.taxes_brl:,.2f} taxas.".replace(",", ".") if best_overall.taxes_brl else ".")
            )
        else:
            summary = (
                f"A melhor opção foi {best_overall.companhia_label} em dinheiro: "
                f"R$ {best_overall.real_cost_brl:,.2f}.".replace(",", ".")
            )
    else:
        summary = "Nenhuma oferta encontrada para esta data."

    # Formatador BRL pt-BR (1.234,56). Aplica só nos valores individuais
    # — substituir caracteres na frase inteira quebraria pontos finais.
    def _brl(v: float) -> str:
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    # Nota de transparência: quando best_overall é milhas mas best_money tem
    # preço cru menor, mostra como cash-com-markup perde para milhas-sem-markup.
    comparison_note = None
    if best_overall and best_overall.miles is not None and best_money:
        cash_cru = best_money.real_cost_brl or 0
        cash_venda = best_money.price_with_markup_brl or cash_cru
        milhas_custo = best_overall.real_cost_brl or 0
        if cash_cru < milhas_custo and cash_venda > milhas_custo:
            econ = cash_venda - milhas_custo
            comparison_note = (
                f"O calendário mostra R$ {_brl(cash_cru)} (cash do mercado Kayak), "
                f"mas com o markup de 10% o cliente pagaria R$ {_brl(cash_venda)}. "
                f"A opção em milhas custa R$ {_brl(milhas_custo)} (sem markup), "
                f"economizando R$ {_brl(econ)} no preço final de venda."
            )

    return buckets, bucket_order, airline_ranking, verdict, summary, comparison_note


# ────────────────────────────────────────────────────────────────────
# Endpoint: VALIDAÇÃO de cada perna de um Skiplagged Split em milhas.
#
# Diferente do encaixe manual da quebra de trecho, aqui o objetivo é
# CONFIRMAR se cada trecho que o Skiplagged identificou existe nos
# programas de milhas da cia operadora. Se existir, mostramos o custo
# em milhas; se não, avisamos o vendedor.
# ────────────────────────────────────────────────────────────────────
class SplitLegInput(BaseModel):
    """Um trecho do split do Skiplagged a validar em milhas."""
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    carrier_iata: str = Field(..., min_length=2, max_length=3)
    departure_dt: str = Field(..., description="ISO datetime do voo a validar")
    arrival_dt: str | None = None
    flight_number: str | None = None


class SplitLegValidation(BaseModel):
    """Resultado da validação em milhas de UM trecho do split."""
    origin: str
    destination: str
    carrier_iata: str
    carrier_label: str
    departure_dt: str
    flight_number: str | None = None

    found_in_miles: bool = False           # houve QUALQUER oferta milhas na rota+data+cia?
    found_exact_flight: bool = False       # match exato do flight_number ou ±30min do horário
    alternatives: list[HiddenCityMilesAlternative] = []
    cheapest_miles_real_cost_brl: float | None = None
    note: str | None = None                # mensagem human-readable do status


class SplitMilesValidationRequest(BaseModel):
    """Validar uma lista de trechos (split do Skiplagged) em milhas."""
    legs: list[SplitLegInput] = Field(..., min_length=1, max_length=6)
    cash_reference_brl: float | None = Field(None, description="Preço cash total do Skiplagged pra comparar com a soma em milhas")
    adults: int = Field(1, ge=1, le=9)
    cabin: CabinClass = CabinClass.ECONOMY


class SplitMilesValidationResponse(BaseModel):
    """Resposta completa: validação por trecho + agregado."""
    legs: list[SplitLegValidation] = []
    all_found_in_miles: bool = False       # cada perna tem ≥1 oferta milhas?
    total_cheapest_miles_brl: float | None = None  # soma dos baratos por perna
    cash_reference_brl: float | None = None
    savings_brl: float | None = None       # cash - miles total (positivo = milhas economizam)
    recommendation: str = "unknown"        # miles_cheaper | cash_cheaper | similar | incomplete | unknown
    summary_note: str | None = None


def _validate_one_split_leg(
    leg: SplitLegInput,
    adults: int,
    cabin: CabinClass,
) -> SplitLegValidation:
    """Cota um trecho específico do split em BM (cia operadora) + Economilhas
    em paralelo. Reusa _build_hidden_city_quote pra evitar duplicação de
    código — mesmo padrão: rota fixa, mesmo dia, filtro por cia, dedup.
    """
    carrier = leg.carrier_iata.upper()
    # Parse departure_dt
    try:
        from datetime import datetime as _dt
        dep_dt = _dt.fromisoformat(leg.departure_dt.replace("Z", "+00:00"))
        dep_dt_naive = dep_dt.replace(tzinfo=None) if dep_dt.tzinfo else dep_dt
    except (ValueError, AttributeError):
        return SplitLegValidation(
            origin=leg.origin.upper(),
            destination=leg.destination.upper(),
            carrier_iata=carrier,
            carrier_label=_CARRIER_TO_PROGRAM_LABEL.get(carrier, carrier),
            departure_dt=leg.departure_dt,
            flight_number=leg.flight_number,
            note="departure_dt inválido — impossível validar este trecho.",
        )

    quote = _build_hidden_city_quote(
        carrier_iata=carrier,
        origin=leg.origin.upper(),
        official_dest=leg.destination.upper(),
        passenger_dest=leg.destination.upper(),  # split não tem hidden — mesmo destino
        departure_dt=dep_dt_naive,
        adults=adults,
        cabin=cabin,
        cash_reference_brl=None,                  # comparação só no agregado
        direct_check=None,                        # split não usa direct_check
    )

    if quote is None or not quote.alternatives:
        return SplitLegValidation(
            origin=leg.origin.upper(),
            destination=leg.destination.upper(),
            carrier_iata=carrier,
            carrier_label=_CARRIER_TO_PROGRAM_LABEL.get(carrier, carrier),
            departure_dt=leg.departure_dt,
            flight_number=leg.flight_number,
            found_in_miles=False,
            note=(
                f"{_CARRIER_TO_PROGRAM_LABEL.get(carrier, carrier)} sem disponibilidade em milhas "
                f"para {leg.origin.upper()}→{leg.destination.upper()} em {dep_dt_naive.date().isoformat()}."
            ),
        )

    # Decide se algum match é "exato" — mesmo flight_number OU ±30min do horário
    target_hhmm = dep_dt_naive.strftime("%H:%M")
    target_minutes = dep_dt_naive.hour * 60 + dep_dt_naive.minute
    found_exact = False
    for alt in quote.alternatives:
        if leg.flight_number and alt.flight_number and \
                alt.flight_number.replace(" ", "").upper() == leg.flight_number.replace(" ", "").upper():
            found_exact = True
            break
        if alt.departure_time:
            try:
                hh, mm = alt.departure_time.split(":")
                alt_minutes = int(hh) * 60 + int(mm)
                if abs(alt_minutes - target_minutes) <= 30:
                    found_exact = True
                    break
            except (ValueError, AttributeError):
                continue

    cheapest = min((a.real_cost_brl for a in quote.alternatives), default=None)

    return SplitLegValidation(
        origin=leg.origin.upper(),
        destination=leg.destination.upper(),
        carrier_iata=carrier,
        carrier_label=_CARRIER_TO_PROGRAM_LABEL.get(carrier, carrier),
        departure_dt=leg.departure_dt,
        flight_number=leg.flight_number,
        found_in_miles=True,
        found_exact_flight=found_exact,
        alternatives=quote.alternatives,
        cheapest_miles_real_cost_brl=cheapest,
        note=(
            f"Voo {leg.flight_number or ''} confirmado em milhas (±30min)."
            if found_exact
            else f"Cia tem voos em milhas na rota/data, mas não achamos o {leg.flight_number or 'horário exato'}. Veja alternativas."
        ),
    )


@router.post("/smart-quote/split-miles-validation", response_model=SplitMilesValidationResponse)
def split_miles_validation(payload: SplitMilesValidationRequest) -> SplitMilesValidationResponse:
    """Valida cada perna de um Skiplagged Split em milhas (cia operadora + Economilhas).
    NÃO faz encaixe nem busca alternativas de hub — só confirma se os voos do split
    existem em milhas pra mostrar o custo real, ou avisa que não existem."""
    # Validações em paralelo — cada perna é independente
    results: list[SplitLegValidation] = [None] * len(payload.legs)  # type: ignore
    with ThreadPoolExecutor(max_workers=min(len(payload.legs), 6) or 1) as ex:
        futures = {
            ex.submit(_validate_one_split_leg, leg, payload.adults, payload.cabin): idx
            for idx, leg in enumerate(payload.legs)
        }
        for f in as_completed(futures):
            idx = futures[f]
            try:
                results[idx] = f.result()
            except Exception as e:
                leg = payload.legs[idx]
                results[idx] = SplitLegValidation(
                    origin=leg.origin.upper(),
                    destination=leg.destination.upper(),
                    carrier_iata=leg.carrier_iata.upper(),
                    carrier_label=_CARRIER_TO_PROGRAM_LABEL.get(leg.carrier_iata.upper(), leg.carrier_iata.upper()),
                    departure_dt=leg.departure_dt,
                    flight_number=leg.flight_number,
                    note=f"Erro ao validar este trecho: {e}",
                )

    all_found = all(r.found_in_miles for r in results)
    cheapest_sum = (
        sum(r.cheapest_miles_real_cost_brl or 0 for r in results)
        if all_found else None
    )

    # Decide recomendação comparando soma em milhas vs cash do Skiplagged
    rec = "unknown"
    savings: float | None = None
    summary: str | None = None
    if cheapest_sum and payload.cash_reference_brl:
        savings = payload.cash_reference_brl - cheapest_sum
        tol = max(payload.cash_reference_brl * 0.05, 30.0)
        if abs(savings) <= tol:
            rec = "similar"
            summary = f"Cash e milhas em valores parecidos (diferença R$ {savings:.2f})."
        elif savings > 0:
            rec = "miles_cheaper"
            summary = f"Em milhas economiza R$ {savings:.2f} vs cash do Skiplagged."
        else:
            rec = "cash_cheaper"
            summary = f"Cash sai R$ {abs(savings):.2f} mais barato — emitir em milhas não compensa neste split."
    elif not all_found:
        rec = "incomplete"
        missing = [
            f"{r.origin}→{r.destination} ({r.carrier_iata})"
            for r in results if not r.found_in_miles
        ]
        summary = (
            f"Não conseguimos validar em milhas: {', '.join(missing)}. "
            f"Confirme com a cia antes de fechar o split."
        )

    return SplitMilesValidationResponse(
        legs=results,
        all_found_in_miles=all_found,
        total_cheapest_miles_brl=cheapest_sum,
        cash_reference_brl=payload.cash_reference_brl,
        savings_brl=savings,
        recommendation=rec,
        summary_note=summary,
    )


# ────────────────────────────────────────────────────────────────────
# Endpoint sob demanda: cotação em milhas para o itinerário OFICIAL
# de uma oferta Skiplagged hidden city.
#
# Útil quando o usuário seleciona uma linha hidden city que não veio
# com `hidden_city_miles` pré-populado (eager-load tem cap por quota).
# ────────────────────────────────────────────────────────────────────
class HiddenCityMilesRequest(BaseModel):
    """Requisição para cotar milhas em um itinerário oficial de hidden city.

    Ex: hidden city BSB->IOS (descer em FOR) operado pela LATAM no dia
    15/06 — manda origin=BSB, destination=IOS, carrier_iata=LA,
    passenger_destination=FOR, date=2026-06-15.
    """
    origin: str = Field(..., min_length=3, max_length=3, description="IATA origem do bilhete oficial")
    destination: str = Field(..., min_length=3, max_length=3, description="IATA destino OFICIAL do bilhete (não onde o passageiro desce)")
    passenger_destination: str = Field(..., min_length=3, max_length=3, description="Onde o passageiro realmente desce (escala)")
    carrier_iata: str = Field(..., min_length=2, max_length=3)
    date: date
    departure_time: str | None = Field(None, description="HH:MM — opcional, usado para preferir voos no horário próximo")
    adults: int = Field(1, ge=1, le=9)
    cabin: CabinClass = CabinClass.ECONOMY
    cash_reference_brl: float | None = Field(None, description="Preço cash do Skiplagged pra esta rota — usado pra comparar com milhas e dar uma recomendação")


@router.post("/smart-quote/hidden-city-miles", response_model=HiddenCityMilesQuote | None)
def hidden_city_miles(payload: HiddenCityMilesRequest) -> HiddenCityMilesQuote | None:
    """Cota em paralelo BuscaMilhas + Economilhas para o itinerário oficial
    completo de uma oferta hidden city. Sempre devolve a estrutura (mesmo
    com `alternatives=[]` quando não há cobertura) — frontend mostra
    'nenhuma cotação encontrada' em vez de erro."""
    dep_dt = None
    if payload.departure_time:
        try:
            hh, mm = payload.departure_time.split(":")
            from datetime import datetime as _dt
            dep_dt = _dt.combine(payload.date, _dt.min.time()).replace(hour=int(hh), minute=int(mm))
        except (ValueError, AttributeError):
            dep_dt = None
    if dep_dt is None:
        from datetime import datetime as _dt
        dep_dt = _dt.combine(payload.date, _dt.min.time())

    # Em paralelo: voo direto pro destino real do passageiro
    direct_check = _check_direct_flight(
        origin=payload.origin.upper(),
        passenger_dest=payload.passenger_destination.upper(),
        target_date=payload.date,
        adults=payload.adults,
        cabin=payload.cabin,
        hidden_cash_brl=payload.cash_reference_brl,
    )

    return _build_hidden_city_quote(
        carrier_iata=payload.carrier_iata.upper(),
        origin=payload.origin.upper(),
        official_dest=payload.destination.upper(),
        passenger_dest=payload.passenger_destination.upper(),
        departure_dt=dep_dt,
        adults=payload.adults,
        cabin=payload.cabin,
        cash_reference_brl=payload.cash_reference_brl,
        direct_check=direct_check,
    )


@router.post("/smart-quote/quote-for-date", response_model=QuoteForDateResponse)
def quote_for_date(payload: QuoteForDateRequest) -> QuoteForDateResponse:
    """Phase 2: cotação completa para a data escolhida. Roda BuscaMilhas +
    Economilhas + Skiplagged + Kayak (cash) em paralelo. Quando `return_date`
    vem preenchido, faz roundtrip e cada oferta carrega ida + volta.
    """
    trip_type = TripType.ROUNDTRIP if payload.return_date else TripType.ONEWAY
    req = SearchRequest(
        origin=[payload.origin.upper()],
        destination=[payload.destination.upper()],
        date_start=payload.date,
        date_end=payload.date,
        return_start=payload.return_date,
        return_end=payload.return_date,
        adults=payload.adults,
        cabin=payload.cabin,
        trip_type=trip_type,
    )

    tasks: list = []
    if payload.include_economilhas:
        tasks.append(EconomilhasAdapter)
    if payload.include_buscamilhas:
        tasks.extend(_BUSCAMILHAS_ADAPTERS)
    if payload.include_kayak:
        tasks.append(KayakAdapter)
    if payload.include_skiplagged:
        tasks.append(SkiplaggedAdapter)

    all_offers: list[UnifiedOffer] = []
    with ThreadPoolExecutor(max_workers=min(len(tasks), 14) or 1) as ex:
        futures = [ex.submit(_run_adapter_safe, cls, req) for cls in tasks]
        for f in as_completed(futures):
            all_offers.extend(f.result())

    miles = [o for o in all_offers if o.miles is not None]
    cash  = [o for o in all_offers if o.miles is None]

    miles.sort(key=lambda o: o.equivalent_brl if o.equivalent_brl is not None else float("inf"))
    cash.sort(key=lambda o: o.price_brl if o.price_brl is not None else float("inf"))

    # Para Skiplagged hidden city: cota em paralelo a alternativa em MILHAS
    # pelo itinerário OFICIAL do bilhete (não pro destino do passageiro).
    # Ex: hidden city BSB->GIG via FOR pela GOL → cota BSB->GIG em Smiles.
    hidden_offers = [
        o for o in all_offers
        if o.source == SourceType.SKIPLAGGED
        and o.scenario == Scenario.HIDDEN_CITY
    ]
    hidden_city_quotes = _quote_hidden_city_alternatives(
        hidden_offers, adults=payload.adults, cabin=payload.cabin,
    ) if hidden_offers else {}

    buckets, bucket_order, airline_ranking, verdict, summary, comparison_note = _build_buckets_and_verdict(
        all_offers, hidden_city_quotes=hidden_city_quotes,
    )

    return QuoteForDateResponse(
        origin=payload.origin.upper(),
        destination=payload.destination.upper(),
        date=payload.date.isoformat(),
        return_date=payload.return_date.isoformat() if payload.return_date else None,
        miles_offers=miles[:30],
        cash_offers=cash[:15],
        best_offer_on_date=_build_best_offer_on_date(cash),
        flat_offers=sorted(
            all_offers,
            key=lambda o: _real_cost(o) if _real_cost(o) is not None else float("inf"),
        ),
        buckets=buckets,
        bucket_order=bucket_order,
        airline_ranking=airline_ranking,
        verdict=verdict,
        summary=summary,
        comparison_note=comparison_note,
    )
