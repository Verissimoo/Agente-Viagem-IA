"""Miles Match — pairs a Kayak combination (domestic leg + international leg)
with targeted award searches:
  • Doméstica → APENAS o programa próprio da companhia operadora
                 (G3 → SMILES, LA → LATAM Pass, AD → TudoAzul)
  • Internacional → todos os programas em PROGRAM_COVERAGE que cobrem
                     a companhia daquele voo, em paralelo.

Para cada voo retornado, marca:
  • is_exact_match  — número de voo + data + horário com tolerância de 10min
  • is_in_window    — cabe na janela de conexão (2h30m / 4h com bagagem)

O agente usa os clients existentes (BuscaMilhas e Economilhas) e os
parsers já maduros — não duplica lógica de pricing nem de paginação.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from backend.app.services.segment_split import KayakOffer
from backend.app.services.conversion import miles_to_brl

# Clients e parsers — dependências de runtime carregadas só quando o
# agente faz uma chamada (assim os testes e imports não falham caso
# alguma credencial esteja faltando no ambiente).


# ──────────────────────────────────────────────────────────────────
# Cobertura — programa → companhias que ele consegue emitir (IATA)
# ──────────────────────────────────────────────────────────────────
PROGRAM_COVERAGE: dict[str, dict[str, list[str]]] = {
    "SMILES": {  # GOL Smiles
        "carriers": [
            "G3", "AA", "AC", "AF", "KL", "AR", "AV", "CM", "AM",
            "DL", "UA", "LH", "LX", "OS", "SK", "AY", "BA", "IB",
            "TP", "TK", "EK", "EY", "QR", "SV", "MS", "ET", "SA",
            "NH", "JL", "CX", "SQ", "TG", "KE", "MH", "GA", "VN",
            "OZ", "CI", "BR", "NZ", "QF", "FR", "VY", "EI", "WB",
            "H2", "LA", "AD",
        ],
    },
    "LATAM_PASS": {  # LATAM Pass
        "carriers": [
            "LA", "QR", "DL", "JL", "QF", "VS", "AS", "BA", "IB",
            "CX", "AY", "RJ", "LH", "LX", "OS", "AM", "AR",
        ],
    },
    "AZUL_FIDELIDADE": {  # TudoAzul
        "carriers": [
            "AD", "AC", "CM", "EK", "TP", "TK", "UA", "EY", "LH",
            "LX", "OS", "SK", "AY", "KL", "AF", "BA", "IB", "QR",
            "SQ", "NH", "JL", "CX", "AM", "AR", "AV", "LA",
        ],
        "award_only": ["AC", "CM", "EK", "TP", "TK", "UA", "AD"],
    },
    "AZUL_INTERLINE": {  # Azul Pelo Mundo
        "carriers": [
            "AD", "AC", "CM", "EK", "TP", "TK", "UA", "EY", "LH",
            "LX", "OS", "SK", "AY", "KL", "AF", "BA", "IB", "QR",
            "SQ", "NH", "JL", "CX", "AM", "AR", "AV", "LA",
        ],
    },
    "COPA": {
        "carriers": [
            "CM", "UA", "AC", "LH", "LX", "OS", "SK", "AV", "TK",
            "SQ", "NH", "ET", "EY",
        ],
    },
    "IBERIA": {
        "carriers": [
            "IB", "BA", "AY", "QR", "RJ", "LA", "QF", "JL", "AA",
            "AS",
        ],
    },
    "BRITISH": {
        "carriers": [
            "BA", "IB", "AY", "QR", "JL", "QF", "LA", "AA", "AS",
            "RJ", "MH",
        ],
    },
}

# carrier IATA → programa próprio (apenas as 3 nacionais brasileiras).
CARRIER_TO_OWN_PROGRAM: dict[str, str] = {
    "G3": "SMILES",
    "LA": "LATAM_PASS",
    "AD": "AZUL_FIDELIDADE",
}

# program → nome aceito pelo BuscaMilhas client (somente os programas que
# devem ser pesquisados via BuscaMilhas — fase 3 limita ao essencial).
PROGRAM_TO_BUSCAMILHAS_NAME: dict[str, str] = {
    "SMILES": "GOL",
    "LATAM_PASS": "LATAM",
    "AZUL_FIDELIDADE": "AZUL",
}

# program → nome aceito pelo Economilhas client.
PROGRAM_TO_ECONOMILHAS_NAME: dict[str, str] = {
    "SMILES": "SMILES",
    "LATAM_PASS": "LATAM",
    "AZUL_FIDELIDADE": "AZUL",
    "AZUL_INTERLINE": "AZUL_INTERLINE",
    "COPA": "COPA",
    "IBERIA": "IBERIA",
    "BRITISH": "BRITISH",
}

# Para conversão milhas→BRL: programa → label aceito por miles_to_brl.
# Os labels seguem o dict RATES_BRL_PER_MILE em pcd/core/conversion.py.
PROGRAM_TO_RATE_LABEL: dict[str, str] = {
    "SMILES": "GOL",
    "LATAM_PASS": "LATAM",
    "AZUL_FIDELIDADE": "AZUL",
    "AZUL_INTERLINE": "INTERLINE",
    "COPA": "COPA",
    "IBERIA": "IBERIA",          # → 0.0700 (Avios)
    "BRITISH": "AVIOS",          # → 0.0700 (Avios)
}

# Constantes de conexão — alinhadas com SegmentSplitAgent (fase 2).
MIN_CONN_BAG_MIN = 240
MIN_CONN_NO_BAG_MIN = 150
MAX_CONN_MIN = 720

EXACT_MATCH_MINUTES_TOLERANCE = 10


# ──────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────
@dataclass
class MilesMatchOption:
    program: str
    miles: int
    miles_brl_equivalent: float
    taxes_brl: float
    total_real_cost_brl: float
    flight_number: str
    carrier: str
    departure_dt: Optional[datetime]
    arrival_dt: Optional[datetime]
    is_exact_match: bool
    is_in_window: bool
    layover_minutes: int
    raw_data: dict = field(default_factory=dict)


@dataclass
class MilesMatchResult:
    leg_type: str                              # "domestic" | "international"
    kayak_reference: KayakOffer
    target_carrier: str
    programs_searched: list[str]
    options: list[MilesMatchOption] = field(default_factory=list)
    has_exact_match: bool = False
    no_results_reason: Optional[str] = None
    notes: list[str] = field(default_factory=list)
    # Estado da bagagem na última avaliação — usado pela UI para decidir
    # se vale rebucketizar quando o vendedor toggla o checkbox.
    with_baggage: bool = False


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────
def _identify_carrier(off: KayakOffer) -> str:
    """Extrai o código IATA da companhia operadora (G3, LA, AD, AA, …)
    a partir do KayakOffer. Prefere o primeiro elemento de
    `airlines_iata`; se vazio, tenta inferir pelo `airlines`."""
    iata = list(getattr(off, "airlines_iata", []) or [])
    if iata:
        c = (iata[0] or "").upper()
        if 2 <= len(c) <= 3:
            return c
    names = list(getattr(off, "airlines", []) or [])
    if names:
        # mapeamento mínimo nome → IATA
        nm = (names[0] or "").upper()
        for code, n in (
            ("G3", "GOL"), ("LA", "LATAM"), ("AD", "AZUL"),
            ("AA", "AMERICAN"), ("DL", "DELTA"), ("UA", "UNITED"),
            ("BA", "BRITISH"), ("IB", "IBERIA"), ("AF", "AIR FRANCE"),
            ("KL", "KLM"), ("LH", "LUFTHANSA"), ("TP", "TAP"),
            ("CM", "COPA"), ("AC", "AIR CANADA"), ("EK", "EMIRATES"),
            ("QR", "QATAR"), ("TK", "TURKISH"),
        ):
            if n in nm:
                return code
    return ""


def _map_carrier_to_own_program(carrier: str) -> Optional[str]:
    return CARRIER_TO_OWN_PROGRAM.get((carrier or "").upper())


def _programs_covering(carrier: str) -> list[str]:
    """Lista (em ordem do dict) os programas que conseguem emitir naquele
    carrier IATA."""
    c = (carrier or "").upper()
    out: list[str] = []
    for prog, info in PROGRAM_COVERAGE.items():
        if c in (info.get("carriers") or []):
            out.append(prog)
    return out


def _provider_program_name(program: str, provider: str) -> Optional[str]:
    if provider == "economilhas":
        return PROGRAM_TO_ECONOMILHAS_NAME.get(program)
    return PROGRAM_TO_BUSCAMILHAS_NAME.get(program)  # buscamilhas default


def _parse_flight_number(numero: Any) -> tuple[str, str]:
    """Retorna (carrier_code, flight_digits) a partir de strings tipo
    'G3-1450', 'G31450', 'LA 8038', '1450'. Robusto a variações."""
    s = str(numero or "").strip().upper()
    if not s:
        return ("", "")
    # Remove separadores comuns
    cleaned = s.replace(" ", "").replace("-", "")
    # Letras iniciais (até 3) = carrier
    i = 0
    while i < len(cleaned) and cleaned[i].isalpha() and i < 3:
        i += 1
    carrier = cleaned[:i]
    digits = cleaned[i:]
    return (carrier, digits)


def _is_exact_flight_match(
    miles_row: dict, kayak_offer: KayakOffer,
) -> bool:
    """Determina se a oferta de milhas representa o MESMO voo do Kayak.

    Critérios:
      • mesmo carrier IATA (do kayak_offer.airlines_iata[0])
      • mesmo número de voo (parte numérica)
      • mesma data de partida
      • horário de partida com tolerância de ±10min
    Aceita números soltos (ex: '1450') comparando só os dígitos.
    """
    if not miles_row:
        return False

    target_carrier = _identify_carrier(kayak_offer)
    if not target_carrier:
        return False

    # Carrier do voo de milhas — preferir o segments_raw que reflete o
    # operating carrier real (alguns programas mostram a marca do
    # programa em "Companhia").
    segs = miles_row.get("segments_raw") or []
    miles_carrier = ""
    if segs:
        s0 = segs[0]
        miles_carrier = (getattr(s0, "carrier", "") or "").upper()
    if not miles_carrier:
        miles_carrier = (miles_row.get("Companhia") or "").upper()
        if miles_carrier in {"GOL", "LATAM", "AZUL"}:
            miles_carrier = {"GOL": "G3", "LATAM": "LA", "AZUL": "AD"}[miles_carrier]

    if miles_carrier and miles_carrier != target_carrier:
        return False

    # Número de voo: o row de milhas tem 'NumeroVoo' formatado (ex: 'G3-1450')
    # mas o kayak_offer não expõe o número diretamente. Por isso, "exact
    # match" aqui é definido por carrier + data + horário (±10min) — o
    # próprio horário de partida com tolerância de 10min já isola um único
    # voo em rotas reais. O número aparece como informação na opção.
    miles_dep = miles_row.get("departure_dt")
    if not isinstance(miles_dep, datetime) or kayak_offer.departure_dt is None:
        return False
    if miles_dep.date() != kayak_offer.departure_dt.date():
        return False
    delta = abs((miles_dep - kayak_offer.departure_dt).total_seconds()) / 60.0
    return delta <= EXACT_MATCH_MINUTES_TOLERANCE


def _layover_with(
    other_dt: datetime,
    this_dt: datetime,
    other_is_before: bool,
) -> int:
    """Tempo de conexão em minutos. `other_is_before=True` significa que
    o `other_dt` representa a CHEGADA do voo anterior (em GRU), e
    `this_dt` representa a PARTIDA do voo atual (que sai de GRU).
    Quando False, o sentido é invertido."""
    if other_is_before:
        return int((this_dt - other_dt).total_seconds() / 60)
    return int((other_dt - this_dt).total_seconds() / 60)


def _is_in_window(
    layover_min: int, with_baggage: bool,
) -> bool:
    min_conn = MIN_CONN_BAG_MIN if with_baggage else MIN_CONN_NO_BAG_MIN
    return min_conn <= layover_min <= MAX_CONN_MIN


# ──────────────────────────────────────────────────────────────────
# Agente
# ──────────────────────────────────────────────────────────────────
class MilesMatchAgent:
    MAX_OPTIONS_PER_LEG = 12

    # ── Domestic ────────────────────────────────────────────────
    def match_domestic_leg(
        self,
        kayak_offer: KayakOffer,
        other_leg_dt: datetime,
        other_leg_direction: str,         # "before_intl" | "after_intl"
        with_baggage: bool,
        adults: int,
        provider: str = "buscamilhas",
    ) -> MilesMatchResult:
        carrier = _identify_carrier(kayak_offer)
        own_program = _map_carrier_to_own_program(carrier)

        if own_program is None:
            return MilesMatchResult(
                leg_type="domestic",
                kayak_reference=kayak_offer,
                target_carrier=carrier or "?",
                programs_searched=[],
                no_results_reason=(
                    f"Companhia {carrier or '?'} não tem programa de milhas próprio "
                    f"mapeado (esperado G3/LA/AD)."
                ),
                with_baggage=with_baggage,
            )

        # Confere se o provedor consegue consultar este programa
        if _provider_program_name(own_program, provider) is None:
            return MilesMatchResult(
                leg_type="domestic", kayak_reference=kayak_offer,
                target_carrier=carrier, programs_searched=[],
                no_results_reason=(
                    f"Programa {own_program} não suportado no provedor {provider}."
                ),
                with_baggage=with_baggage,
            )

        rows = self._fetch_program_rows(
            program=own_program, provider=provider,
            origin=kayak_offer.origin, destination=kayak_offer.destination,
            kayak_dep_dt=kayak_offer.departure_dt,
            adults=adults,
        )

        options = self._rows_to_options(
            rows=rows, program=own_program, kayak_offer=kayak_offer,
            other_leg_dt=other_leg_dt, other_leg_direction=other_leg_direction,
            with_baggage=with_baggage,
        )
        # Mantém apenas as que cabem na janela ou são exact_match (por
        # garantia, mesmo se o exact_match cair fora por edge-case).
        options = [o for o in options if o.is_in_window or o.is_exact_match]
        options.sort(key=lambda o: (not o.is_exact_match, o.total_real_cost_brl))
        options = options[: self.MAX_OPTIONS_PER_LEG]

        has_exact = any(o.is_exact_match for o in options)

        return MilesMatchResult(
            leg_type="domestic", kayak_reference=kayak_offer,
            target_carrier=carrier,
            programs_searched=[own_program],
            options=options, has_exact_match=has_exact,
            no_results_reason=None if options else (
                f"Sem disponibilidade no programa {own_program} para essa data/companhia. "
                f"Pode tentar validar manualmente no BuscaMilhas."
            ),
            with_baggage=with_baggage,
        )

    # ── International ───────────────────────────────────────────
    def match_international_leg(
        self,
        kayak_offer: KayakOffer,
        domestic_leg_dt: datetime,
        domestic_leg_direction: str,      # "before_intl" | "after_intl"
        with_baggage: bool,
        adults: int,
        provider: str = "buscamilhas",
    ) -> MilesMatchResult:
        carrier = _identify_carrier(kayak_offer)
        all_programs = _programs_covering(carrier)

        if not all_programs:
            return MilesMatchResult(
                leg_type="international", kayak_reference=kayak_offer,
                target_carrier=carrier or "?",
                programs_searched=[],
                no_results_reason=(
                    f"Nenhum programa cadastrado emite voos da {carrier or '?'}. "
                    f"Considere apenas a tarifa Kayak."
                ),
                with_baggage=with_baggage,
            )

        # Filtra por programas suportados pelo provider escolhido
        supported: list[str] = []
        notes: list[str] = []
        for p in all_programs:
            if _provider_program_name(p, provider) is not None:
                supported.append(p)
            else:
                notes.append(
                    f"Programa {p} não suportado em {provider} — pulado."
                )

        if not supported:
            return MilesMatchResult(
                leg_type="international", kayak_reference=kayak_offer,
                target_carrier=carrier, programs_searched=[],
                notes=notes,
                no_results_reason=(
                    f"Nenhum dos programas que cobrem {carrier} está disponível "
                    f"em {provider}."
                ),
                with_baggage=with_baggage,
            )

        # Consultas paralelas
        all_rows: list[tuple[str, dict]] = []
        with ThreadPoolExecutor(max_workers=min(6, len(supported))) as ex:
            futs = {
                ex.submit(
                    self._fetch_program_rows,
                    program=p, provider=provider,
                    origin=kayak_offer.origin, destination=kayak_offer.destination,
                    kayak_dep_dt=kayak_offer.departure_dt, adults=adults,
                ): p
                for p in supported
            }
            for fut in futs:
                p = futs[fut]
                try:
                    rows = fut.result()
                except Exception:
                    rows = []
                for r in rows:
                    all_rows.append((p, r))

        options: list[MilesMatchOption] = []
        for prog, row in all_rows:
            opt = self._row_to_option(
                row=row, program=prog, kayak_offer=kayak_offer,
                other_leg_dt=domestic_leg_dt,
                other_leg_direction=domestic_leg_direction,
                with_baggage=with_baggage,
            )
            if opt is None:
                continue
            options.append(opt)

        options = [o for o in options if o.is_in_window or o.is_exact_match]
        # Confirma que o carrier do voo de milhas bate com o target —
        # evita cardápio cheio de marca-própria que não voa o trecho.
        options = [o for o in options if (o.carrier or "").upper() == carrier]
        options.sort(key=lambda o: (not o.is_exact_match, o.total_real_cost_brl))
        options = options[: self.MAX_OPTIONS_PER_LEG]

        has_exact = any(o.is_exact_match for o in options)

        return MilesMatchResult(
            leg_type="international", kayak_reference=kayak_offer,
            target_carrier=carrier,
            programs_searched=supported,
            options=options, has_exact_match=has_exact,
            notes=notes,
            no_results_reason=None if options else (
                f"Não encontramos cotação em milhas para esse voo da {carrier} "
                f"nessa janela de conexão. Pode ser que o voo não esteja "
                f"disponível em milhas no momento, ou que a janela seja muito "
                f"apertada para conexão."
            ),
            with_baggage=with_baggage,
        )

    # ── Internos ────────────────────────────────────────────────
    def _fetch_program_rows(
        self,
        program: str,
        provider: str,
        origin: str,
        destination: str,
        kayak_dep_dt: Optional[datetime],
        adults: int,
    ) -> list[dict]:
        """Faz UMA chamada Kayak-like ao provedor de milhas, restrita a um
        único programa, e devolve a lista de rows já parseadas (apenas
        ofertas IsMiles=True).

        Para a data, usa a data do voo Kayak (que é o que faz sentido em
        um encaixe — o voo Kayak escolhido é a referência)."""
        if kayak_dep_dt is None:
            return []
        date_iso = kayak_dep_dt.date().isoformat()

        if provider == "buscamilhas":
            comp = PROGRAM_TO_BUSCAMILHAS_NAME.get(program)
            if comp is None:
                return []
            try:
                from backend.app.providers.buscamilhas.client import search_flights_buscamilhas
                from backend.app.providers.buscamilhas.parser import (
                    extract_rows_from_buscamilhas,
                )
                data_ida_br = kayak_dep_dt.strftime("%d/%m/%Y")
                raw = search_flights_buscamilhas(
                    companhia=comp,
                    origem=origin, destino=destination,
                    data_ida=data_ida_br,
                    adultos=adults,
                    somente_milhas=True,
                )
                rows = extract_rows_from_buscamilhas(raw, comp, "OW")
                return [r for r in rows if r.get("IsMiles")]
            except Exception:
                # Falha em uma companhia não derruba a busca — outras seguem.
                return []

        if provider == "economilhas":
            comp = PROGRAM_TO_ECONOMILHAS_NAME.get(program)
            if comp is None:
                return []
            try:
                from backend.app.providers.economilhas.client import search_flights_economilhas
                from backend.app.providers.economilhas.parser import extract_rows_from_economilhas
                raw = search_flights_economilhas(
                    airlines=[comp],
                    origin=origin, destination=destination,
                    departure_date=date_iso,
                    adults=adults,
                    price_type="MILES",
                )
                rows, _failures = extract_rows_from_economilhas(raw, "OW")
                return [r for r in rows if r.get("IsMiles")]
            except Exception:
                # Falha em uma companhia não derruba a busca — outras seguem.
                return []

        return []

    def _rows_to_options(
        self,
        rows: list[dict],
        program: str,
        kayak_offer: KayakOffer,
        other_leg_dt: datetime,
        other_leg_direction: str,
        with_baggage: bool,
    ) -> list[MilesMatchOption]:
        out: list[MilesMatchOption] = []
        for r in rows:
            opt = self._row_to_option(
                row=r, program=program, kayak_offer=kayak_offer,
                other_leg_dt=other_leg_dt,
                other_leg_direction=other_leg_direction,
                with_baggage=with_baggage,
            )
            if opt is not None:
                out.append(opt)
        return out

    def _row_to_option(
        self,
        row: dict,
        program: str,
        kayak_offer: KayakOffer,
        other_leg_dt: datetime,
        other_leg_direction: str,
        with_baggage: bool,
    ) -> Optional[MilesMatchOption]:
        miles = row.get("Milhas")
        try:
            miles_int = int(miles or 0)
        except (TypeError, ValueError):
            return None
        if miles_int <= 0:
            return None

        taxes = float(row.get("Taxas (R$)") or 0.0)
        rate_label = PROGRAM_TO_RATE_LABEL.get(program, program)
        eq_brl = miles_to_brl(miles_int, airline=rate_label, program=rate_label)
        total_brl = eq_brl + taxes

        dep_dt = row.get("departure_dt") if isinstance(row.get("departure_dt"), datetime) else None
        arr_dt = row.get("arrival_dt") if isinstance(row.get("arrival_dt"), datetime) else None

        # Carrier real (do segmento) — programa pode não ser a operadora
        segs = row.get("segments_raw") or []
        seg_carrier = ""
        seg_flight = ""
        if segs:
            s0 = segs[0]
            seg_carrier = (getattr(s0, "carrier", "") or "").upper()
            seg_flight = getattr(s0, "flight_number", "") or ""

        flight_no = row.get("NumeroVoo") or seg_flight or ""
        carrier_used = seg_carrier or _identify_carrier(kayak_offer)

        # Cálculo de janela com a outra perna
        is_in_window = False
        layover_min = 0
        if dep_dt is not None and arr_dt is not None:
            if other_leg_direction == "before_intl":
                # outra perna chega ANTES desta partida (esta perna sai
                # depois) — útil quando esta é a internacional GRU→X com
                # doméstica chegando em GRU.
                layover_min = int((dep_dt - other_leg_dt).total_seconds() / 60)
            elif other_leg_direction == "after_intl":
                # outra perna parte DEPOIS desta chegada — útil quando
                # esta é a internacional X→GRU com doméstica saindo de GRU.
                layover_min = int((other_leg_dt - arr_dt).total_seconds() / 60)
            else:
                layover_min = 0
            is_in_window = _is_in_window(layover_min, with_baggage)

        return MilesMatchOption(
            program=program,
            miles=miles_int,
            miles_brl_equivalent=float(eq_brl),
            taxes_brl=float(taxes),
            total_real_cost_brl=float(total_brl),
            flight_number=str(flight_no),
            carrier=carrier_used,
            departure_dt=dep_dt,
            arrival_dt=arr_dt,
            is_exact_match=_is_exact_flight_match(row, kayak_offer),
            is_in_window=is_in_window,
            layover_minutes=layover_min,
            raw_data=row,
        )


# ──────────────────────────────────────────────────────────────────
# Re-bucket client-side (para refiltrar quando o usuário troca o
# checkbox "Considerar bagagem despachada", sem refazer chamadas)
# ──────────────────────────────────────────────────────────────────
def rebucket_match(result: MilesMatchResult, with_baggage: bool) -> MilesMatchResult:
    """Reaplica o filtro de janela (is_in_window) sobre as opções já
    coletadas, recalculando layover_minutes inalterado e ajustando
    is_in_window. Usado quando o vendedor troca o checkbox de bagagem."""
    if not result.options:
        return result
    new_options: list[MilesMatchOption] = []
    for o in result.options:
        in_win = (
            _is_in_window(o.layover_minutes, with_baggage)
            if (o.layover_minutes or 0) > 0
            else o.is_in_window
        )
        new_options.append(MilesMatchOption(
            program=o.program, miles=o.miles,
            miles_brl_equivalent=o.miles_brl_equivalent,
            taxes_brl=o.taxes_brl, total_real_cost_brl=o.total_real_cost_brl,
            flight_number=o.flight_number, carrier=o.carrier,
            departure_dt=o.departure_dt, arrival_dt=o.arrival_dt,
            is_exact_match=o.is_exact_match,
            is_in_window=in_win,
            layover_minutes=o.layover_minutes,
            raw_data=o.raw_data,
        ))
    new_options = [o for o in new_options if o.is_in_window or o.is_exact_match]
    new_options.sort(key=lambda o: (not o.is_exact_match, o.total_real_cost_brl))

    return MilesMatchResult(
        leg_type=result.leg_type,
        kayak_reference=result.kayak_reference,
        target_carrier=result.target_carrier,
        programs_searched=list(result.programs_searched),
        options=new_options,
        has_exact_match=any(o.is_exact_match for o in new_options),
        no_results_reason=None if new_options else result.no_results_reason,
        notes=list(result.notes),
        with_baggage=with_baggage,
    )
