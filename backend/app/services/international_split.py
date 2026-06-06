"""Cotação INTERNACIONAL com quebra de trecho (dois tipos).

Para uma rota internacional saindo do Brasil (ex.: GYN→LIS), além do voo direto,
montamos duas formas de "quebra de trecho":

  TIPO 1 — HUB SPLIT (IA): comprar GYN→SAO (doméstico) + SAO→LIS (internacional)
           separados, encaixando a perna doméstica na janela de conexão do voo
           internacional. Cada perna validada em milhas; cash Kayak leva +10%.

  TIPO 2 — SKIP SPLIT: o Skiplagged já devolve combinações tipo
           GOL GYN→SSA + LATAM SSA→LIS (bilhetes separados). Validamos cada
           perna em milhas (igual hidden city).

Estratégia de custo (decisões validadas):
  • Radar Kayak (barato) varre o intervalo e escolhe o MELHOR dia por rota.
  • A validação cara (milhas/skip) roda só nesse dia.
  • Hub fixo = GRU.

Reusa: date_radar (radar), run_search (milhas multi-programa + skiplagged),
KAYAK_MARKUP, validate_split_with_supplementary, sanitize_offers.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

HUB = "GRU"           # hub principal (compat)
HUBS = ["GRU", "VCP"]  # hubs pra quebra de trecho. VCP = Azul (cash direto + milhas)
KAYAK_MARKUP = 1.10
MIN_CONN_MIN = 150   # janela mínima doméstico→internacional (sem bagagem)
MAX_CONN_MIN = 720   # 12h


def _center_and_flex(dates: List[date]):
    """Centro do RANGE + flex_days (±N, cap 7) pra a matriz do Kayak cobrir todo
    o intervalo pedido. Centro = ponto médio do range (não a mediana das datas)."""
    ds = sorted(dates)
    span = (ds[-1] - ds[0]).days
    center = ds[0] + timedelta(days=span // 2)
    flex = max(1, min(7, (span + 1) // 2))
    return center, flex


def _scan_dates_fallback(origin: str, dest: str, iso_dates: List[str],
                         adults: int, cabin) -> Dict[str, float]:
    """Fallback do radar quando a MATRIZ do Kayak falha (ex.: Madrid não renderiza
    a matriz flex). Usa o scraping POR DATA (scan_dates) — mais lento, porém
    confiável pras rotas que a matriz não cobre. Amostra poucas datas."""
    from backend.app.services.date_radar import scan_dates
    pairs = []
    for s in iso_dates:
        try:
            pairs.append((date.fromisoformat(s), None))
        except ValueError:
            pass
    if not pairs:
        return {}
    try:
        r = scan_dates(pairs, origin=origin, destination=dest, adults=adults, cabin=cabin)
    except Exception as e:
        logger.warning("fallback per-date %s→%s falhou: %s", origin, dest, e)
        return {}
    return {k: float(v) for k, v in (r.price_by_pair or {}).items() if v}


def _kayak_matrix(origin: str, dest: str, center: date, flex: int,
                  requested: set, attempts: int = 1) -> Dict[str, float]:
    """Preços por data via a MATRIZ de datas flexíveis do Kayak (1 call) — bate
    com o calendário que o vendedor vê no site. Filtra só as datas pedidas.
    Faz retry: a raspagem é flaky e uma rota voltar vazia quebra o fluxo."""
    from backend.app.providers.kayak.scraper import fetch_kayak_matrix
    for i in range(attempts):
        try:
            r = fetch_kayak_matrix(origin, dest, center.isoformat(), flex_days=flex)
            by = (r or {}).get("prices_by_date") or {}
            filtered = {k: float(v) for k, v in by.items() if k in requested and v}
            if filtered:
                return filtered
            logger.warning("matrix %s→%s vazia (tent %d/%d)", origin, dest, i + 1, attempts)
        except Exception as e:
            logger.warning("matrix %s→%s falhou (tent %d/%d): %s", origin, dest, i + 1, attempts, e)
    return {}


def _dt(s: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _segs(o: Dict[str, Any], leg: str = "outbound") -> List[Dict[str, Any]]:
    return (o.get(leg) or {}).get("segments") or []


def _is_kayak(o: Dict[str, Any]) -> bool:
    src = str(o.get("source") or "").lower()
    return "kayak" in src


def _best_route(money: List[Dict[str, Any]], miles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Melhor opção VALIDADA de uma rota/dia: milhas (equivalente) OU cash Kayak
    com +10% de markup. Devolve dict com brl/kind/miles/taxes/airline/segments."""
    cands: List[Dict[str, Any]] = []
    for o in miles:
        eq = o.get("equivalent_brl")
        if eq:
            cands.append({
                "kind": "miles", "brl": float(eq), "miles": o.get("miles"),
                "taxes_brl": o.get("taxes_brl"), "airline": o.get("airline"),
                "segments": _segs(o), "program": o.get("miles_program"),
            })
    for o in money:
        if _is_kayak(o) and o.get("price_brl"):
            cands.append({
                "kind": "cash", "brl": round(float(o["price_brl"]) * KAYAK_MARKUP, 2),
                "cash_brl": float(o["price_brl"]), "airline": o.get("airline"),
                "segments": _segs(o),
            })
    return min(cands, key=lambda c: c["brl"]) if cands else None


def _leg_options(money: List[Dict[str, Any]], miles: List[Dict[str, Any]]):
    """Melhor MILHAS e melhor CASH (×1.10) de uma perna, SEPARADOS — pra mostrar
    cada um e comparar. Retorna (best_miles|None, best_cash|None)."""
    from backend.app.ai.agents.airlines import carrier_to_program
    best_miles = None
    for o in miles:
        eq = o.get("equivalent_brl")
        if eq and (best_miles is None or float(eq) < best_miles["brl"]):
            best_miles = {
                "kind": "miles", "brl": float(eq), "miles": o.get("miles"),
                "taxes_brl": o.get("taxes_brl"), "airline": o.get("airline"),
                "program": o.get("miles_program") or carrier_to_program(o.get("airline")),
                "segments": _segs(o),
            }
    best_cash = None
    for o in money:
        if _is_kayak(o) and o.get("price_brl"):
            c = round(float(o["price_brl"]) * KAYAK_MARKUP, 2)
            if best_cash is None or c < best_cash["brl"]:
                best_cash = {
                    "kind": "cash", "brl": c, "cash_brl": float(o["price_brl"]),
                    "airline": o.get("airline"), "segments": _segs(o),
                }
    return best_miles, best_cash


def _hub_leg(money: List[Dict[str, Any]], miles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Uma perna do hub-split: PRIMÁRIO em milhas (foco do produto) + nota de
    `cash_cheaper` quando o mesmo trecho sai mais barato em dinheiro (sinal pro
    vendedor procurar uma emissão melhor). Sem milhas → cai pro cash."""
    bm, bc = _leg_options(money, miles)
    if bm is None:
        if bc is None:
            return None
        return {**bc, "no_miles": True}    # só cash (cia sem programa plugado)
    leg: Dict[str, Any] = dict(bm)
    if bc is not None and bc["cash_brl"] < bm["brl"]:
        leg["cash_cheaper"] = {
            "cash_brl": bc["cash_brl"],                       # preço de mercado
            "with_markup_brl": bc["brl"],                     # com +10%
            "savings_brl": round(bm["brl"] - bc["cash_brl"], 2),  # quanto < que milhas
            "airline": bc["airline"],
        }
    return leg


def _direct_miles_per_carrier(miles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Uma opção de voo direto por CIA que retornou em milhas (a mais barata de
    cada). Prioridade do vendedor: ver milhas em TODAS as companhias com
    resultado, não só a mais barata global."""
    from backend.app.ai.agents.airlines import carrier_to_program
    best_by: Dict[str, Dict[str, Any]] = {}
    for o in miles:
        eq = o.get("equivalent_brl")
        if not eq:
            continue
        air = (o.get("airline") or "?")
        cur = best_by.get(air.upper())
        if cur is None or float(eq) < cur["total_brl"]:
            best_by[air.upper()] = {
                "type": "direct_miles", "airline": air,
                "program": o.get("miles_program") or carrier_to_program(air),
                "miles": o.get("miles"), "taxes_brl": o.get("taxes_brl"),
                "total_brl": float(eq), "segments": _segs(o),
            }
    return list(best_by.values())


def _direct_cash_unplugged(money: List[Dict[str, Any]],
                           miles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Cash Kayak mais barato cuja CIA não retornou em milhas na nossa busca →
    não temos programa plugado pra ela. Mostra com +10% e aviso de que é a mais
    barata, mas fora dos nossos programas de milhas."""
    miles_carriers = {(o.get("airline") or "").upper() for o in miles}
    cash = sorted(
        (o for o in money if _is_kayak(o) and o.get("price_brl")),
        key=lambda o: float(o["price_brl"]),
    )
    for o in cash:
        air = o.get("airline") or "—"
        if air.upper() in miles_carriers:
            continue  # já temos essa cia em milhas — não precisa do cash
        return {
            "type": "direct_cash", "airline": air,
            "total_brl": round(float(o["price_brl"]) * KAYAK_MARKUP, 2),
            "cash_brl": float(o["price_brl"]), "segments": _segs(o),
            "no_miles_program": True,
        }
    return None


def _run(origin: str, dest: str, day: date, adults: int, cabin: str) -> Dict[str, Any]:
    from backend.app.ai.agents.sanitizer import sanitize_offers
    from backend.app.ai.agents.tools import run_search
    try:
        r = run_search(origin=origin, destination=dest, date_start=day,
                       adults=adults, cabin=cabin, top_n=10)
    except Exception as e:
        logger.warning("intl-split run_search %s→%s %s falhou: %s", origin, dest, day, e)
        return {"ok": False}
    if not r.get("ok"):
        return {"ok": False}
    return {
        "ok": True,
        "money": sanitize_offers(r.get("money_offers") or []),
        "miles": sanitize_offers(r.get("miles_offers") or []),
    }


def radar_international(*, origin: str, destination: str, dates: List[date],
                       adults: int = 1, cabin: str = "economy") -> Dict[str, Any]:
    """FASE 1 da confirmação: radar barato via MATRIZ flex do Kayak (1 call por
    rota, preços batendo com o site) — direto (origin→dest) + cada HUB (→dest),
    em paralelo. Descobre a melhor data de cada rota (podem diferir)."""
    center, flex = _center_and_flex(dates)
    # A matriz traz TODOS os dias do range numa call — usa o range COMPLETO (não
    # só uma amostra), senão dias baratos no meio do intervalo escapam.
    ds = sorted(dates)
    full = [ds[0] + timedelta(days=i) for i in range((ds[-1] - ds[0]).days + 1)]
    requested = {d.isoformat() for d in full}

    # Amostra de datas pro fallback por-data (poucas, pra não explodir o tempo).
    sample_iso = sorted(requested)
    if len(sample_iso) > 4:
        step = (len(sample_iso) - 1) / 3
        sample_iso = [sample_iso[round(i * step)] for i in range(4)]

    def _route(o: str, d: str):
        by = _kayak_matrix(o, d, center, flex, requested)
        if not by:
            # Matriz vazia (ex.: Madrid não renderiza a matriz flex) → fallback
            # por-data, que é confiável pras rotas que a matriz não cobre.
            logger.info("intl radar: matriz vazia %s→%s — fallback por-data", o, d)
            by = _scan_dates_fallback(o, d, sample_iso, adults, cabin)
        ranked = [date.fromisoformat(k) for k in sorted(by, key=lambda k: by[k])]
        return ranked, by

    # DIRETO primeiro e SOZINHO (navegador Playwright limpo = mais confiável; é a
    # rota prioritária). Rodar 3 Chromium em paralelo fazia o direto voltar vazio.
    dir_days, dir_by = _route(origin, destination)
    # Hubs depois, no máximo 2 em paralelo (menos contenção que 3).
    hubs: Dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=min(2, len(HUBS))) as ex:
        f_hubs = {h: ex.submit(_route, h, destination) for h in HUBS}
        for h, f in f_hubs.items():
            days, by = f.result()
            if days:
                hubs[h] = {"days": days, "by_date": by}

    return {
        "direct": {"days": dir_days, "by_date": dir_by},
        "hubs": hubs,   # {hub: {days, by_date}} — só hubs com resultado
    }


def _best_domestic_fit(dom_res: Dict[str, Any], intl_dep: Optional[datetime]) -> Optional[Dict[str, Any]]:
    """Da busca doméstica origin→hub (já feita), escolhe a melhor perna que
    CHEGA a tempo de conectar no voo internacional (janela MIN_CONN..MAX_CONN)."""
    money, miles = dom_res.get("money") or [], dom_res.get("miles") or []
    if intl_dep is None:
        return _hub_leg(money, miles)

    def _fits(o: Dict[str, Any]) -> bool:
        segs = _segs(o)
        arr = _dt(segs[-1].get("arrival_dt")) if segs else None
        if not arr:
            return False
        gap = (intl_dep - arr).total_seconds() / 60
        return MIN_CONN_MIN <= gap <= MAX_CONN_MIN

    return _hub_leg([o for o in money if _fits(o)], [o for o in miles if _fits(o)])


def _skip_split_options(money: List[Dict[str, Any]], adults: int, cabin: str) -> List[Dict[str, Any]]:
    """Tipo 2: valida em milhas os splits que o Skiplagged devolveu (GOL GYN→SSA
    + LATAM SSA→LIS, etc.) — igual hidden city."""
    from backend.app.ai.agents.hidden_city_validator import validate_split_with_supplementary
    splits = [o for o in money if "split" in str(o.get("category") or "").lower()]
    if not splits:
        return []
    validated = validate_split_with_supplementary(splits, adults=adults, cabin=cabin, max_validations=1)
    out = []
    for o in validated:
        alt = o.get("miles_alternative") or {}
        if alt.get("is_split") and alt.get("equivalent_brl"):
            out.append({
                "type": "skip_split", "total_brl": float(alt["equivalent_brl"]),
                "miles": alt.get("miles"), "taxes_brl": alt.get("taxes_brl"),
                "breakdown": alt.get("split_breakdown"), "segments": _segs(o),
            })
    return out


def quote_international(*, origin: str, destination: str,
                       dates: Optional[List[date]] = None,
                       direct_days: Optional[List[date]] = None,
                       hubs: Optional[Dict[str, date]] = None,
                       reference: Optional[Dict[str, Any]] = None,
                       adults: int = 1, cabin: str = "economy",
                       on_progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Orquestra direto + hub-split (Tipo 1) + skip-split (Tipo 2) e devolve as
    opções validadas, ordenadas pela mais barata.

    `on_progress(msg)` (opcional) recebe mensagens de progresso pra streamar ao
    vendedor — esta função É o job de background; o wrapper async passa o callback.

    IMPORTANTE: as buscas de milhas rodam SEQUENCIALMENTE de propósito. Rodá-las
    em paralelo multiplica a disputa pelos semáforos (BuscaMilhas=3, Kayak=5) e
    pelo rate-limit do RapidAPI → cada pipeline estoura o orçamento e volta
    INCOMPLETA (sem a quebra de trecho). Sequencial, cada busca pega o semáforo
    inteiro e completa as 8 cias internacionais."""
    def _emit(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    # Datas: ou vêm explícitas (Fase 2 — melhor dia de cada rota, já achado no
    # radar da Fase 1) ou rodamos o radar aqui (chamada direta / flex ≤ 3 dias).
    if direct_days is None or hubs is None:
        if not dates:
            return {"options": []}
        _emit("Analisando datas no mercado (Kayak)…")
        rad = radar_international(origin=origin, destination=destination,
                                 dates=dates, adults=adults, cabin=cabin)
        direct_days = direct_days or rad["direct"]["days"][:2]
        if hubs is None:
            hubs = {h: info["days"][0] for h, info in rad["hubs"].items() if info["days"]}
        if reference is None:
            reference = {"direct_by_date": rad["direct"]["by_date"],
                         "hubs_by_date": {h: i["by_date"] for h, i in rad["hubs"].items()}}
    direct_days = direct_days or []
    hubs = hubs or {}

    # ROBUSTEZ: se a matriz Kayak falhou/veio vazia, NÃO desiste do split (era o
    # bug: caía no fluxo normal sem confirmação nem quebra de trecho). Usa as
    # próprias datas do range — sem o ranking do radar, mas ainda valida em milhas.
    if dates and not direct_days:
        logger.warning("intl: radar do direto vazio — fallback pras datas do range")
        direct_days = sorted(dates)[:2]
    if dates and not hubs:
        logger.warning("intl: radar dos hubs vazio — fallback pras datas do range")
        hubs = {h: sorted(dates)[0] for h in HUBS}

    options: List[Dict[str, Any]] = []

    # 2. Buscas de rota/dia SEQUENCIAIS (ver docstring) — cada uma emite progresso.
    #   direct: origin→dest · hub_intl: HUB→dest · hub_dom: origin→HUB (por hub)
    # specs: cada um é (kind, hub, origin, dest, day)
    specs: List[Tuple[str, str, str, str, date]] = [
        ("direct", "", origin, destination, d) for d in direct_days
    ]
    for hub, hday in hubs.items():
        specs.append(("hub_intl", hub, hub, destination, hday))
        specs.append(("hub_dom", hub, origin, hub, hday))

    results: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for kind, hub, o, dd, dy in specs:
        if kind == "direct":
            label = f"voo direto {origin}→{destination}"
        elif kind == "hub_intl":
            label = f"trecho internacional {hub}→{destination}"
        else:
            label = f"trecho nacional {origin}→{hub}"
        _emit(f"Buscando {label} em {dy.strftime('%d/%m')}…")
        results[(kind, hub, dy.isoformat())] = _run(o, dd, dy, adults, cabin)

    # 3. DIRETO: milhas em TODAS as cias com resultado + cash Kayak sem programa.
    # O skip-split (Tipo 2) é COLETADO aqui, mas só entra no fim se for o mais
    # barato (prioridade do vendedor: milhas + quebra nacional, skip por último).
    skip_candidates: List[Dict[str, Any]] = []
    for day in direct_days:
        res = results.get(("direct", "", day.isoformat())) or {}
        if not res.get("ok"):
            continue
        diso = day.isoformat()
        for opt in _direct_miles_per_carrier(res["miles"]):
            opt["date"] = diso
            options.append(opt)
        unplugged = _direct_cash_unplugged(res["money"], res["miles"])
        if unplugged:
            unplugged["date"] = diso
            options.append(unplugged)
        for sk in _skip_split_options(res["money"], adults, cabin):
            sk["date"] = diso
            skip_candidates.append(sk)

    # 4. QUEBRA DE TRECHO NACIONAL via hub (Tipo 1) — PRIORIDADE: pra CADA hub
    # (GRU, VCP), HUB→dest validado + encaixe origin→HUB validado. VCP traz Azul
    # (cash direto + milhas), mesmo sendo conexão nacional mais difícil.
    _emit("Montando a quebra de trecho nacional…")
    for hub, hday in hubs.items():
        diso = hday.isoformat()
        intl_res = results.get(("hub_intl", hub, diso)) or {}
        dom_res = results.get(("hub_dom", hub, diso)) or {}
        if not (intl_res.get("ok") and dom_res.get("ok")):
            continue
        intl = _hub_leg(intl_res["money"], intl_res["miles"])
        if not intl:
            continue
        intl_dep = _dt((intl.get("segments") or [{}])[0].get("departure_dt"))
        dom = _best_domestic_fit(dom_res, intl_dep)
        if not dom:
            continue
        options.append({
            "type": "hub_split", "date": diso,
            "total_brl": round(intl["brl"] + dom["brl"], 2),
            "intl_leg": intl, "domestic_leg": dom, "hub": hub,
        })

    # 5. SKIP-SPLIT (Tipo 2 / Skiplagged): só entra se for MAIS BARATO que a
    # melhor opção segura (milhas/cash/quebra nacional). Mesmo quando entra,
    # vai marcado como arriscado — o presenter nunca o recomenda.
    cheapest_safe = min((o["total_brl"] for o in options), default=float("inf"))
    for sk in skip_candidates:
        if sk["total_brl"] < cheapest_safe:
            sk["cheaper_but_risky"] = True
            options.append(sk)

    options.sort(key=lambda o: o["total_brl"])

    # Sinal "mercado mais barato que milhas": se a referência Kayak do direto for
    # bem mais barata que a melhor opção validada em milhas, o presenter avisa
    # (só no texto) que o mercado tem um preço que NÃO achamos em milhas.
    ref = reference or {}
    direct_by_date = ref.get("direct_by_date") or {}
    market_signal = None
    if direct_by_date:
        ref_iso = min(direct_by_date, key=lambda k: direct_by_date[k])
        ref_price = float(direct_by_date[ref_iso])
        best_validated = min((o["total_brl"] for o in options), default=float("inf"))
        # +10% de markup sobre a referência de mercado (nossa margem de venda).
        ref_marked = round(ref_price * KAYAK_MARKUP, 2)
        if ref_marked < best_validated:
            market_signal = {"date": ref_iso, "price_brl": ref_price,
                             "price_markup_brl": ref_marked}

    _emit(f"Pronto — {len(options)} opção(ões) validada(s).")
    return {
        "options": options,
        "direct_days": [d.isoformat() for d in direct_days],
        "hubs": {h: d.isoformat() for h, d in hubs.items()},
        "reference": {"direct_by_date": direct_by_date,
                      "hubs_by_date": ref.get("hubs_by_date") or {}},
        "market_signal": market_signal,
    }
