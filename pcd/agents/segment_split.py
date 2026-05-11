"""
Quebra de Trecho — versão simplificada (Fase 1).

Regra única: sempre quebrar em GRU (São Paulo).
Faz apenas uma chamada ao Kayak por perna que envolve São Paulo
(mais uma de referência para o preço direto, em paralelo).

  - Brasil → exterior   → busca GRU → destino
  - exterior → Brasil   → busca origem → GRU
  - Doméstico nacional  → busca duas pernas (origem→GRU e GRU→destino)
  - origem ou destino já é GRU → não aplicável
  - rota toda fora do Brasil → não aplicável

Fase 2 (não implementada aqui) trará a busca automática da segunda
perna doméstica para rotas internacionais.

Mantém a classe pública `SegmentSplitAgent`. Reusa
`kayak_client.search_flights` e `offer_parser.extract_offers` como
caixa-preta — não modifica nenhum cliente existente.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from kayak_client import search_flights as kayak_search
from offer_parser import extract_offers as kayak_extract


HUB_FIXO = "GRU"  # mantido como default; vendedor pode escolher outro via UI

# Códigos metropolitanos (cobrem múltiplos aeroportos da mesma região)
METRO_CODES: dict[str, str] = {
    "SAO": "GRU",  # São Paulo → GRU (cobre GRU/CGH/VCP)
    "RIO": "GIG",  # Rio de Janeiro → GIG (cobre GIG/SDU)
    "BHZ": "CNF",  # Belo Horizonte → CNF (cobre CNF/PLU)
}

BR_AIRPORTS: set[str] = {
    # Aeroportos individuais
    "GRU", "CGH", "VCP", "GIG", "SDU", "BSB", "CNF", "PLU", "CWB",
    "POA", "FLN", "NVT", "JOI", "FOR", "REC", "SSA", "MCZ", "AJU",
    "JPA", "NAT", "THE", "SLZ", "BEL", "MAO", "PVH", "BVB", "RBR",
    "MCP", "PMW", "GYN", "CGR", "CGB", "VIX", "MGF", "LDB", "UDI",
    "IGU", "RAO", "CXJ", "GVR", "JDO", "PNZ", "VDC", "IOS", "BPS",
    "TBT", "STM", "CKS", "MAB", "CAY", "BVH", "FEN", "JTC",
    # Códigos metropolitanos (Kayak/IATA city codes)
    "SAO", "RIO", "BHZ",
}


def resolve_metro_to_airport(code: str) -> str:
    """Converte código metropolitano para aeroporto principal (RIO→GIG, SAO→GRU, BHZ→CNF).
    Se já for um aeroporto individual, devolve sem alterar."""
    return METRO_CODES.get((code or "").upper(), (code or "").upper())


# ──────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────
@dataclass
class KayakOffer:
    origin: str
    destination: str
    airlines: list[str] = field(default_factory=list)
    airlines_iata: list[str] = field(default_factory=list)
    departure_dt: Optional[datetime] = None
    arrival_dt: Optional[datetime] = None
    duration_min: int = 0
    stops: int = 0
    price_brl: float = 0.0
    raw: dict = field(default_factory=dict)
    # Preenchido apenas quando a oferta é resultado de fit_domestic_leg(),
    # representando o tempo de conexão real com o voo internacional pareado.
    layover_minutes: int = 0


@dataclass
class SimpleSegmentResult:
    origin: str
    destination: str
    date: str
    # "br_to_intl" | "intl_to_br" | "br_domestic" | "not_applicable"
    route_type: str
    leg_to_gru: Optional[list[KayakOffer]] = None
    leg_from_gru: Optional[list[KayakOffer]] = None
    direct_offer: Optional[KayakOffer] = None
    not_applicable_reason: Optional[str] = None
    notes: list[str] = field(default_factory=list)
    # Hub efetivamente usado nesta busca (GRU, GIG, CNF, ...).
    # Permite que a UI exiba a estratégia correta nos cards das pernas.
    hub: str = HUB_FIXO


@dataclass
class DomesticFitResult:
    intl_offer: KayakOffer
    search_date: str
    # "same_day" | "day_before" | "day_after"
    search_date_offset: str
    target_window_start: Optional[datetime]
    target_window_end: Optional[datetime]
    compatible_offers: list[KayakOffer] = field(default_factory=list)
    incompatible_offers: list[KayakOffer] = field(default_factory=list)
    no_results: bool = False
    with_baggage: bool = False
    notes: list[str] = field(default_factory=list)
    # Campos auxiliares para re-bucket client-side quando o vendedor troca
    # o checkbox "bagagem despachada" — evitam re-chamar o Kayak.
    intl_direction: str = ""        # "from_gru" | "to_gru"
    all_offers: list[KayakOffer] = field(default_factory=list)


def rebucket_fit(fit: DomesticFitResult, with_baggage: bool) -> DomesticFitResult:
    """Re-bucketiza compatible/incompatible com novo `with_baggage` reusando
    `fit.all_offers` — sem novas chamadas ao Kayak.

    Útil quando o vendedor alterna o checkbox "Considerar bagagem despachada":
    a janela de conexão muda (150min ↔ 240min), mas o conjunto de voos
    domésticos disponíveis não muda. Recalcula em milissegundos.

    Se `fit.no_results` ou se mudar o `with_baggage` faria com que a janela
    sequer caiba na data buscada, devolve um resultado coerente (possivelmente
    vazio) sem refazer a pesquisa.
    """
    if fit.with_baggage == with_baggage:
        return fit
    if not fit.all_offers or fit.intl_direction not in {"from_gru", "to_gru"}:
        return DomesticFitResult(
            intl_offer=fit.intl_offer,
            search_date=fit.search_date,
            search_date_offset=fit.search_date_offset,
            target_window_start=fit.target_window_start,
            target_window_end=fit.target_window_end,
            compatible_offers=[], incompatible_offers=[],
            no_results=fit.no_results, with_baggage=with_baggage,
            notes=list(fit.notes),
            intl_direction=fit.intl_direction,
            all_offers=list(fit.all_offers),
        )

    agent = SegmentSplitAgent()
    min_conn = agent.MIN_CONN_BAG_MIN if with_baggage else agent.MIN_CONN_NO_BAG_MIN
    max_conn = agent.MAX_CONN_MIN

    intl = fit.intl_offer
    notes: list[str] = []

    if fit.intl_direction == "from_gru":
        if intl.departure_dt is None:
            return fit
        target_max = intl.departure_dt - timedelta(minutes=min_conn)
        target_min = intl.departure_dt - timedelta(minutes=max_conn)
        compat, incompat = agent._bucket_by_arrival_window(
            list(fit.all_offers), target_min, target_max, intl.departure_dt
        )
    else:  # to_gru
        if intl.arrival_dt is None:
            return fit
        target_min = intl.arrival_dt + timedelta(minutes=min_conn)
        target_max = intl.arrival_dt + timedelta(minutes=max_conn)
        compat, incompat = agent._bucket_by_departure_window(
            list(fit.all_offers), target_min, target_max, intl.arrival_dt
        )

    compat.sort(key=lambda o: o.price_brl)
    incompat.sort(key=lambda o: o.price_brl)
    compat = compat[: agent.MAX_FIT_OFFERS]
    incompat = incompat[: agent.MAX_FIT_INCOMPAT_OFFERS]

    if not compat and not fit.no_results:
        notes.append(
            "Bagagem despachada exige mais tempo de conexão — algumas opções "
            "saíram da janela. Refaça o encaixe para ampliar a busca, se necessário."
            if with_baggage else
            "Sem bagagem despachada permite conexões mais curtas — o filtro foi recalculado."
        )

    return DomesticFitResult(
        intl_offer=fit.intl_offer,
        search_date=fit.search_date,
        search_date_offset=fit.search_date_offset,
        target_window_start=target_min, target_window_end=target_max,
        compatible_offers=compat, incompatible_offers=incompat,
        no_results=(not compat and not incompat),
        with_baggage=with_baggage,
        notes=notes,
        intl_direction=fit.intl_direction,
        all_offers=list(fit.all_offers),
    )


def offer_id(off: KayakOffer) -> str:
    """Gera um id estável para uma oferta — usado como chave em st.session_state.

    Prefere o leg_id do Kayak (estável dentro de uma resposta); cai num hash
    composto por rota+horário+preço+companhias quando o leg_id não está
    disponível.
    """
    raw = off.raw or {}
    leg = raw.get("leg_id") or raw.get("out_leg_id")
    if isinstance(leg, str) and leg:
        return f"{off.origin}-{off.destination}-{leg}"
    dep = off.departure_dt.isoformat() if off.departure_dt else "no-dep"
    cias = ",".join(off.airlines_iata) if off.airlines_iata else (
        ",".join(off.airlines[:2]) if off.airlines else ""
    )
    return f"{off.origin}-{off.destination}-{dep}-{off.price_brl:.0f}-{cias}"


# ──────────────────────────────────────────────────────────────────
# Helpers privados
# ──────────────────────────────────────────────────────────────────
def _to_brl(amt, ccy: str | None) -> Optional[float]:
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
        import fx_rates  # type: ignore
        return fx_rates.convert(amt_f, cu, "BRL")
    except Exception:
        return amt_f * 5.0


def _parse_dt(s) -> Optional[datetime]:
    if not isinstance(s, str) or not s:
        return None
    s2 = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        return datetime.fromisoformat(s2)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _leg_iata_codes(raw: dict, leg_id: str) -> list[str]:
    data = raw.get("data") or {}
    legs = data.get("legs") or {}
    segments = data.get("segments") or {}
    leg = legs.get(leg_id)
    if not isinstance(leg, dict):
        return []
    seg_refs = leg.get("segments") or []
    out: list[str] = []
    for sr in seg_refs:
        sid = sr.get("id") if isinstance(sr, dict) else (sr if isinstance(sr, str) else None)
        if not isinstance(sid, str) or sid not in segments:
            continue
        seg = segments[sid]
        code = seg.get("airline")
        if isinstance(code, str) and 2 <= len(code) <= 3:
            up = code.upper()
            if up not in out:
                out.append(up)
    return out


def _to_offer(origin: str, destination: str, offer: dict, raw: dict) -> Optional[KayakOffer]:
    price_brl = _to_brl(offer.get("price"), offer.get("currency"))
    if price_brl is None:
        return None
    leg_id = offer.get("leg_id") or offer.get("out_leg_id")
    return KayakOffer(
        origin=origin.upper(),
        destination=destination.upper(),
        airlines=offer.get("airlines") or [],
        airlines_iata=_leg_iata_codes(raw, leg_id) if isinstance(leg_id, str) else [],
        departure_dt=_parse_dt(offer.get("departure_time") or offer.get("out_departure_time")),
        arrival_dt=_parse_dt(offer.get("arrival_time") or offer.get("out_arrival_time")),
        duration_min=int(offer.get("duration_min") or offer.get("out_duration_min") or 0),
        stops=int(offer.get("stops") or offer.get("out_stops") or 0),
        price_brl=float(price_brl),
        raw=offer,
    )


# ──────────────────────────────────────────────────────────────────
# Agente
# ──────────────────────────────────────────────────────────────────
class SegmentSplitAgent:
    TOP_K_PER_LEG = 10

    # Conexão para o encaixe doméstico (fit_domestic_leg)
    MIN_CONN_BAG_MIN = 240        # 4h com bagagem despachada
    MIN_CONN_NO_BAG_MIN = 150     # 2h30m sem bagagem
    MAX_CONN_MIN = 720            # 12h — acima disso seria pernoite
    MAX_FIT_OFFERS = 10
    MAX_FIT_INCOMPAT_OFFERS = 5

    def run(
        self,
        origin: str,
        destination: str,
        date: str,
        adults: int = 1,
        return_date: Optional[str] = None,
        hub: str = HUB_FIXO,
    ) -> SimpleSegmentResult:
        # Normaliza origem/destino: metrópole (SAO/RIO/BHZ) → aeroporto principal.
        ori_raw = (origin or "").upper()
        dst_raw = (destination or "").upper()
        ori = resolve_metro_to_airport(ori_raw)
        dst = resolve_metro_to_airport(dst_raw)

        # Valida hub: 3 letras maiúsculas; cai no padrão se inválido.
        hub_up = (hub or HUB_FIXO).upper().strip()
        if not (len(hub_up) == 3 and hub_up.isalpha()):
            hub_up = HUB_FIXO
        hub_up = resolve_metro_to_airport(hub_up)

        # 1. Casos de não aplicabilidade
        if ori == dst:
            return SimpleSegmentResult(
                origin=ori, destination=dst, date=date,
                route_type="not_applicable",
                not_applicable_reason="Origem e destino são iguais.",
                hub=hub_up,
            )
        if ori == hub_up or dst == hub_up:
            return SimpleSegmentResult(
                origin=ori, destination=dst, date=date,
                route_type="not_applicable",
                not_applicable_reason=f"Origem ou destino já é o hub selecionado ({hub_up}). Escolha outro hub.",
                hub=hub_up,
            )

        ori_br = ori in BR_AIRPORTS
        dst_br = dst in BR_AIRPORTS

        if not ori_br and not dst_br:
            return SimpleSegmentResult(
                origin=ori, destination=dst, date=date,
                route_type="not_applicable",
                not_applicable_reason=f"Rota não passa pelo Brasil — quebra em {hub_up} não se aplica.",
                hub=hub_up,
            )

        # 2. Determinar tipo de rota e tarefas a executar
        if ori_br and not dst_br:
            route_type = "br_to_intl"
            tasks = [
                ("leg_from_gru", hub_up, dst),
                ("direct", ori, dst),
            ]
        elif not ori_br and dst_br:
            route_type = "intl_to_br"
            tasks = [
                ("leg_to_gru", ori, hub_up),
                ("direct", ori, dst),
            ]
        else:  # both BR, neither is hub
            route_type = "br_domestic"
            tasks = [
                ("leg_to_gru", ori, hub_up),
                ("leg_from_gru", hub_up, dst),
                ("direct", ori, dst),
            ]

        # 3. Chamadas Kayak em paralelo
        results: dict[str, list[KayakOffer]] = {}

        def _one(kind: str, _ori: str, _dst: str) -> tuple[str, list[KayakOffer]]:
            try:
                raw = kayak_search(
                    origin=_ori, destination=_dst,
                    departure_date=date, return_date=return_date,
                    adults=adults, cabin="e",
                )
                offers = kayak_extract(raw) or []
                priced: list[tuple[float, dict]] = []
                for o in offers:
                    brl = _to_brl(o.get("price"), o.get("currency"))
                    if brl is None:
                        continue
                    priced.append((brl, o))
                priced.sort(key=lambda t: t[0])
                top = priced[: self.TOP_K_PER_LEG]
                legs: list[KayakOffer] = []
                for _, o in top:
                    seg = _to_offer(_ori, _dst, o, raw)
                    if seg is not None:
                        legs.append(seg)
                return kind, legs
            except Exception:
                return kind, []

        with ThreadPoolExecutor(max_workers=min(4, len(tasks))) as ex:
            futs = [ex.submit(_one, k, o, d) for (k, o, d) in tasks]
            for f in futs:
                kind, legs = f.result()
                results[kind] = legs

        # 4. Construir resultado
        leg_to_gru = results.get("leg_to_gru")
        leg_from_gru = results.get("leg_from_gru")
        direct_top = results.get("direct", [])
        direct_offer = direct_top[0] if direct_top else None

        notes: list[str] = []
        if route_type == "br_to_intl" and not leg_from_gru:
            notes.append(
                f"Não foi possível buscar a perna {hub_up} → {dst} no momento."
            )
        if route_type == "intl_to_br" and not leg_to_gru:
            notes.append(
                f"Não foi possível buscar a perna {ori} → {hub_up} no momento."
            )
        if route_type == "br_domestic":
            if not leg_to_gru:
                notes.append(
                    f"Não foi possível buscar a perna 1 ({ori} → {hub_up}) no momento."
                )
            if not leg_from_gru:
                notes.append(
                    f"Não foi possível buscar a perna 2 ({hub_up} → {dst}) no momento."
                )

        return SimpleSegmentResult(
            origin=ori, destination=dst, date=date,
            route_type=route_type,
            leg_to_gru=leg_to_gru,
            leg_from_gru=leg_from_gru,
            direct_offer=direct_offer,
            notes=notes,
            hub=hub_up,
        )

    # ── Fase 2: encaixe do voo doméstico ────────────────────────
    def fit_domestic_leg(
        self,
        intl_offer: KayakOffer,
        other_endpoint: str,
        intl_direction: str,        # "from_gru" | "to_gru"
        adults: int = 1,
        with_baggage: bool = False,
    ) -> DomesticFitResult:
        """Pesquisa voos domésticos compatíveis com um voo internacional já
        escolhido pelo vendedor.

        - intl_direction="from_gru" → voo internacional sai de GRU; busca o
          doméstico `other_endpoint → GRU` chegando antes da partida intl.
        - intl_direction="to_gru"  → voo internacional chega em GRU; busca o
          doméstico `GRU → other_endpoint` decolando depois da chegada intl.

        Decide automaticamente o(s) dia(s) de pesquisa conforme o horário do
        voo internacional, e divide o retorno em compatíveis (dentro da janela)
        e incompatíveis (fora). Cada oferta retorna com `layover_minutes`
        preenchido.
        """
        min_conn = self.MIN_CONN_BAG_MIN if with_baggage else self.MIN_CONN_NO_BAG_MIN
        max_conn = self.MAX_CONN_MIN
        other = (other_endpoint or "").upper()

        if intl_direction == "from_gru":
            return self._fit_from_gru(intl_offer, other, adults, with_baggage, min_conn, max_conn)
        if intl_direction == "to_gru":
            return self._fit_to_gru(intl_offer, other, adults, with_baggage, min_conn, max_conn)
        raise ValueError(
            f"intl_direction inválido: {intl_direction!r} (esperado 'from_gru' ou 'to_gru')"
        )

    # ── Internos do fit ─────────────────────────────────────────
    def _fit_from_gru(
        self,
        intl: KayakOffer,
        other: str,
        adults: int,
        with_baggage: bool,
        min_conn: int,
        max_conn: int,
    ) -> DomesticFitResult:
        if intl.departure_dt is None:
            return DomesticFitResult(
                intl_offer=intl, search_date="", search_date_offset="same_day",
                target_window_start=None, target_window_end=None,
                no_results=True, with_baggage=with_baggage,
                notes=["Voo internacional sem horário de partida — janela não pôde ser calculada."],
            )

        target_max = intl.departure_dt - timedelta(minutes=min_conn)
        target_min = intl.departure_dt - timedelta(minutes=max_conn)

        # A data de busca é a data calendárica em que cai o `target_max` —
        # i.e., onde realisticamente chegariam os voos no fim da janela.
        # Se for anterior à do voo intl, classificamos como "day_before".
        # Cobre todos os casos de partida intl de madrugada (00:00-~06:00),
        # quando target_max recua para o dia anterior.
        search_date = target_max.date()
        if search_date < intl.departure_dt.date():
            offset = "day_before"
        else:
            offset = "same_day"

        date_str = search_date.isoformat()
        all_offers = self._search_kayak_for_fit(other, "GRU", date_str, adults)
        compatible, incompatible = self._bucket_by_arrival_window(
            all_offers, target_min, target_max, intl.departure_dt
        )

        notes: list[str] = []
        # Janela apertada: só day_before sem resultados → tenta capturar
        # voos chegando de madrugada no mesmo dia da partida intl.
        if not compatible and offset == "day_before":
            same_day = intl.departure_dt.date().isoformat()
            extra_offers = self._search_kayak_for_fit(other, "GRU", same_day, adults)
            extra_window_start = datetime.combine(
                intl.departure_dt.date(), datetime.min.time()
            )
            extra_window_end = target_max
            extra_compat, _ = self._bucket_by_arrival_window(
                extra_offers, extra_window_start, extra_window_end, intl.departure_dt
            )
            if extra_compat:
                compatible = extra_compat
                notes.append(
                    "Janela apertada — considere ir um dia antes para mais opções."
                )

        if not compatible and not incompatible:
            return DomesticFitResult(
                intl_offer=intl, search_date=date_str, search_date_offset=offset,
                target_window_start=target_min, target_window_end=target_max,
                no_results=True, with_baggage=with_baggage,
                notes=[
                    f"Nenhum voo {other} → GRU encontrado na janela necessária. "
                    f"Considere viajar um dia antes ou aceitar conexão noturna em GRU."
                ] + notes,
                intl_direction="from_gru",
                all_offers=[],
            )

        all_offers_full = list(all_offers)
        compatible.sort(key=lambda o: o.price_brl)
        incompatible.sort(key=lambda o: o.price_brl)
        compatible_capped = compatible[: self.MAX_FIT_OFFERS]
        incompatible_capped = incompatible[: self.MAX_FIT_INCOMPAT_OFFERS]

        return DomesticFitResult(
            intl_offer=intl, search_date=date_str, search_date_offset=offset,
            target_window_start=target_min, target_window_end=target_max,
            compatible_offers=compatible_capped,
            incompatible_offers=incompatible_capped,
            no_results=False, with_baggage=with_baggage,
            notes=notes,
            intl_direction="from_gru",
            all_offers=all_offers_full,
        )

    def _fit_to_gru(
        self,
        intl: KayakOffer,
        other: str,
        adults: int,
        with_baggage: bool,
        min_conn: int,
        max_conn: int,
    ) -> DomesticFitResult:
        if intl.arrival_dt is None:
            return DomesticFitResult(
                intl_offer=intl, search_date="", search_date_offset="same_day",
                target_window_start=None, target_window_end=None,
                no_results=True, with_baggage=with_baggage,
                notes=["Voo internacional sem horário de chegada — janela não pôde ser calculada."],
            )

        target_min = intl.arrival_dt + timedelta(minutes=min_conn)
        target_max = intl.arrival_dt + timedelta(minutes=max_conn)

        # Sempre busca o dia da chegada; se chegar à noite tarde (>=21h),
        # busca também o dia seguinte para capturar voos da manhã.
        dates_to_search: list[str] = [intl.arrival_dt.date().isoformat()]
        if intl.arrival_dt.hour >= 21:
            next_day = (intl.arrival_dt.date() + timedelta(days=1)).isoformat()
            if next_day not in dates_to_search:
                dates_to_search.append(next_day)

        # Offset reportado: "day_after" se a janela mínima já cair no dia
        # seguinte (ex: chegou 23:30 → voo doméstico só decola amanhã).
        if target_min.date() != intl.arrival_dt.date():
            offset = "day_after"
            primary_date = target_min.date().isoformat()
        else:
            offset = "same_day"
            primary_date = intl.arrival_dt.date().isoformat()

        all_offers: list[KayakOffer] = []
        for d in dates_to_search:
            all_offers.extend(self._search_kayak_for_fit("GRU", other, d, adults))

        # Dedup por leg_id (evita repetir o mesmo voo capturado nas duas
        # buscas, caso um id estável apareça em ambas as datas)
        seen: set[str] = set()
        deduped: list[KayakOffer] = []
        for o in all_offers:
            raw = o.raw or {}
            leg = raw.get("leg_id") or raw.get("out_leg_id")
            key = (
                f"leg:{leg}" if isinstance(leg, str) and leg
                else f"sig:{o.departure_dt}-{o.arrival_dt}-{o.price_brl:.2f}"
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(o)

        compatible, incompatible = self._bucket_by_departure_window(
            deduped, target_min, target_max, intl.arrival_dt
        )

        if not compatible and not incompatible:
            return DomesticFitResult(
                intl_offer=intl, search_date=primary_date, search_date_offset=offset,
                target_window_start=target_min, target_window_end=target_max,
                no_results=True, with_baggage=with_baggage,
                notes=[
                    f"Nenhum voo GRU → {other} encontrado na janela necessária. "
                    f"Considere uma noite em São Paulo ou outra data."
                ],
                intl_direction="to_gru",
                all_offers=[],
            )

        all_offers_full = list(deduped)
        compatible.sort(key=lambda o: o.price_brl)
        incompatible.sort(key=lambda o: o.price_brl)
        compatible_capped = compatible[: self.MAX_FIT_OFFERS]
        incompatible_capped = incompatible[: self.MAX_FIT_INCOMPAT_OFFERS]

        return DomesticFitResult(
            intl_offer=intl, search_date=primary_date, search_date_offset=offset,
            target_window_start=target_min, target_window_end=target_max,
            compatible_offers=compatible_capped,
            incompatible_offers=incompatible_capped,
            no_results=False, with_baggage=with_baggage,
            intl_direction="to_gru",
            all_offers=all_offers_full,
        )

    def _search_kayak_for_fit(
        self, ori: str, dst: str, date_iso: str, adults: int,
    ) -> list[KayakOffer]:
        """Versão sem ranking: devolve todas as ofertas como KayakOffer
        (sem TOP_K_PER_LEG aplicado). Filtragem por janela vem depois."""
        try:
            raw = kayak_search(
                origin=ori, destination=dst,
                departure_date=date_iso, return_date=None,
                adults=adults, cabin="e",
            )
            offers = kayak_extract(raw) or []
            out: list[KayakOffer] = []
            for o in offers:
                seg = _to_offer(ori, dst, o, raw)
                if seg is not None:
                    out.append(seg)
            return out
        except Exception:
            return []

    def _bucket_by_arrival_window(
        self,
        offers: list[KayakOffer],
        win_start: datetime,
        win_end: datetime,
        intl_dep: datetime,
    ) -> tuple[list[KayakOffer], list[KayakOffer]]:
        compat: list[KayakOffer] = []
        incompat: list[KayakOffer] = []
        for off in offers:
            if off.arrival_dt is None:
                incompat.append(off)
                continue
            off.layover_minutes = int((intl_dep - off.arrival_dt).total_seconds() / 60)
            if win_start <= off.arrival_dt <= win_end:
                compat.append(off)
            else:
                incompat.append(off)
        return compat, incompat

    def _bucket_by_departure_window(
        self,
        offers: list[KayakOffer],
        win_start: datetime,
        win_end: datetime,
        intl_arr: datetime,
    ) -> tuple[list[KayakOffer], list[KayakOffer]]:
        compat: list[KayakOffer] = []
        incompat: list[KayakOffer] = []
        for off in offers:
            if off.departure_dt is None:
                incompat.append(off)
                continue
            off.layover_minutes = int((off.departure_dt - intl_arr).total_seconds() / 60)
            if win_start <= off.departure_dt <= win_end:
                compat.append(off)
            else:
                incompat.append(off)
        return compat, incompat
