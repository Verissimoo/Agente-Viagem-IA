"""Orchestrator — converte slots em parâmetros de busca e chama a tool.

Nó determinístico (não chama LLM). Recebe `slots` do state, monta os
argumentos certos, invoca `run_search`, e guarda o resultado no state.

Aplica o `RateLimiter` do usuário antes de buscar — busca é cara e
explorável. Se passar do limite, devolve mensagem amigável e não busca.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date as _date, timedelta
from typing import Any, List, Optional

from langchain_core.messages import AIMessage, HumanMessage

from backend.app.ai.agents.routes import classify_route
from backend.app.ai.agents.state import ChatState, IntakeSlots
from backend.app.ai.agents.tools import run_search
from backend.app.chat.security.audit import get_audit_logger
from backend.app.chat.security.rate_limit import RateLimitExceeded, get_rate_limiter

logger = logging.getLogger(__name__)

# Confirmação (flex internacional > 3 dias): reconhece um "sim" do vendedor.
_AFFIRM_RE = re.compile(
    r"\b(sim|pode|confirm\w*|isso|aham|claro|ok|okay|beleza|blz|fechad\w*|"
    r"aprov\w*|positivo|prossegu\w*|segue|seguir|vai|manda|bora|quero|"
    r"faz|faça|busca\w*|pesquis\w*|valida\w*)\b",
    re.IGNORECASE,
)


def _latest_human_text(state: ChatState) -> str:
    for m in reversed(state.get("messages") or []):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


def _is_affirmative(text: str) -> bool:
    return bool(_AFFIRM_RE.search(text or ""))


def _pick_listed_date(text: str, candidate_isos: List[str]) -> Optional[_date]:
    """Se o vendedor citar o DIA de uma das datas oferecidas (ex.: '22' ou
    '22/10'), devolve essa data — pra ele escolher outra da lista, não a 1ª."""
    for iso in candidate_isos:
        d = _parse_iso_date(iso)
        if not d:
            continue
        if re.search(rf"\b{d.day}\b", text) or d.strftime("%d/%m") in text:
            return d
    return None


def _intl_confirmation_message(
    state: ChatState, slots: IntakeSlots, *, origin: str, destination: str,
    date_start: _date, date_end: Optional[_date], adults: int, cabin: str,
) -> Optional[ChatState]:
    """FASE 1 da confirmação internacional: radar Kayak das DUAS rotas (direto
    origin→dest e hub HUB→dest) acha a melhor data de cada uma (podem diferir) +
    referência das outras datas, e PEDE confirmação pra rodar as DUAS buscas
    aprofundadas. Devolve o state (next_node=end) ou None se o radar vier vazio."""
    from backend.app.services.international_split import radar_international
    dates = _sample_dates(date_start, date_end, cap=6)
    try:
        rad = radar_international(origin=origin, destination=destination,
                                 dates=dates, adults=adults, cabin=cabin)
    except Exception as e:
        logger.warning("orchestrator: radar de confirmação falhou: %s", e)
        return None

    dir_days = rad["direct"]["days"]
    dir_by = rad["direct"]["by_date"]
    rad_hubs = rad.get("hubs") or {}
    if not dir_days and not rad_hubs:
        return None

    def _fmt(iso: str) -> str:
        d = _parse_iso_date(iso)
        return d.strftime("%d/%m") if d else iso

    dir_day = dir_days[0].isoformat() if dir_days else None
    # Melhor dia de cada hub (GRU, VCP).
    hubs_best = {h: info["days"][0].isoformat() for h, info in rad_hubs.items() if info.get("days")}

    parts = [f"Comparei o mercado para **{origin} → {destination}** no período:\n"]
    if dir_day:
        p = dir_by.get(dir_day)
        parts.append(f"• **Voo direto** {origin}→{destination}: melhor em **{_fmt(dir_day)}**"
                     + (f" (~R$ {float(p):.0f})" if p else ""))
    for hub, hday in hubs_best.items():
        ph = (rad_hubs[hub]["by_date"] or {}).get(hday)
        parts.append(f"• **Via {hub}** {hub}→{destination} (quebra de trecho): "
                     f"melhor em **{_fmt(hday)}**" + (f" (~R$ {float(ph):.0f})" if ph else ""))
    # Referência das outras datas do direto (regra: sempre mostrar).
    others = sorted((iso for iso in dir_by if iso != dir_day), key=lambda k: dir_by[k])[:4]
    if others:
        ref_txt = " · ".join(f"{_fmt(iso)} ~R$ {float(dir_by[iso]):.0f}" for iso in others)
        parts.append(f"\nReferência de outras datas (direto): {ref_txt}")
    parts.append(
        "\nQuer que eu rode as **buscas aprofundadas** (voo direto + quebra de "
        "trecho via hub)? Responda **sim** — ou me diga outra data."
    )

    new_slots = {
        **slots,
        "intl_awaiting_confirmation": True,
        "intl_confirmation": {
            "direct_day": dir_day, "hubs": hubs_best,
            "direct_by_date": dir_by,
            "hubs_by_date": {h: info["by_date"] for h, info in rad_hubs.items()},
        },
        "intl_radar_dates": [d.isoformat() for d in dir_days[:3]],
    }
    return {
        **state, "slots": new_slots, "next_node": "end",
        "messages": [AIMessage(content="\n".join(parts))],
    }


def _parse_iso_date(value: Optional[str]) -> Optional[_date]:
    if not value:
        return None
    try:
        return _date.fromisoformat(value)
    except ValueError:
        return None


# Aeroportos com CIA EXCLUSIVA: VCP é hub só da Azul (nenhuma outra cia tem
# itinerário internacional saindo de lá). Restringir a busca a Azul (milhas +
# cash/Azul Oficial) corta o tempo — sem varrer as 8 cias, Skiplagged, etc.
_AIRPORT_RESTRICT = {
    "VCP": {"companhias": ["AZUL"], "always_include": ["AZUL_CASH"]},
}


def _merge_origin_searches(origins: List[str], kw: dict) -> dict:
    """Cidade multi-aeroporto (São Paulo=GRU/VCP): roda run_search por aeroporto
    e JUNTA as ofertas, pra não perder voo barato de aeroporto secundário (ex.:
    Azul VCP→LIS). Sequencial de propósito — pipelines paralelos disputam os
    semáforos e voltam incompletos. O presenter re-ranqueia; aqui só concatena."""
    results = []
    for o in origins:
        try:
            r = run_search(origin=o, **{**kw, **_AIRPORT_RESTRICT.get(o, {})})
        except Exception as e:
            logger.warning("orchestrator: run_search origem %s falhou: %s", o, e)
            continue
        if r.get("ok"):
            results.append(r)
    if not results:
        return {"ok": False, "error": "todas as origens falharam"}
    merged = dict(results[0])
    for key in ("ranked_offers", "money_offers", "miles_offers"):
        seen: set = set()
        combined: list = []
        for r in results:
            for off in (r.get(key) or []):
                oid = off.get("offer_id")
                if oid and oid in seen:
                    continue
                if oid:
                    seen.add(oid)
                combined.append(off)
        merged[key] = combined
    return merged


def _sample_dates(start: _date, end: Optional[_date], cap: int = 8) -> List[_date]:
    """Amostra uniforme de datas no intervalo [start, end] (no máx `cap`).
    O radar Kayak varre essas datas pra escolher a melhor — varrer todas as 16
    de um range seria caro demais; amostrar mantém a latência sob controle."""
    if not end or end <= start:
        return [start]
    span = (end - start).days
    n = min(cap, span + 1)
    if n <= 1:
        return [start]
    step = span / (n - 1)
    days = sorted({start + timedelta(days=round(i * step)) for i in range(n)})
    return days


def orchestrator_node(state: ChatState) -> ChatState:
    slots: IntakeSlots = state.get("slots") or {}  # type: ignore[assignment]
    user_id = state.get("user_id", "")
    thread_id = state.get("thread_id")
    audit = get_audit_logger()

    origin = slots.get("origin_iata")
    destination = slots.get("destination_iata")
    date_start = _parse_iso_date(slots.get("date_start"))

    if not origin or not destination or not date_start:
        # Não deveria acontecer (Intake garante), mas defesa em profundidade.
        return {
            **state,
            "next_node": "intake",
            "messages": [AIMessage(content=(
                "Não consegui montar a busca com as informações que temos. "
                "Pode confirmar a origem, destino e data?"
            ))],
            "errors": [*state.get("errors", []), "missing_required_after_intake"],
        }

    try:
        get_rate_limiter().check_search(user_id)
    except RateLimitExceeded as e:
        audit.log(
            "rate_limit.exceeded",
            user_id=user_id,
            thread_id=thread_id,
            severity="warn",
            detail={"kind": e.kind, "retry_in_s": e.retry_in_s},
        )
        return {
            **state,
            "next_node": "end",
            "messages": [AIMessage(content=(
                f"Você atingiu o limite de buscas por agora. Tente novamente em "
                f"{int(e.retry_in_s)} segundos."
            ))],
        }

    audit.log(
        "search.run",
        user_id=user_id,
        thread_id=thread_id,
        detail={
            "origin": origin,
            "destination": destination,
            "date_start": date_start.isoformat(),
            "trip_type": slots.get("trip_type", "oneway"),
        },
    )

    # Normaliza flex_mode: "plus", "minus", "plusminus" → o pipeline interno
    # aceita "plusminus" (busca simétrica). Mantemos o slot original pra
    # exibir ao vendedor "antes"/"depois" quando ele perguntar.
    flex_mode_raw = (slots.get("flex_mode") or "none").lower()
    flex_mode_for_pipeline = (
        "plusminus" if flex_mode_raw in ("plus", "minus", "plusminus") else flex_mode_raw
    )

    # Cap real do pipeline é 7 dias (15 datas). Se vendedor pediu mais,
    # registra warning pro presenter mencionar na resposta.
    flex_days_requested = int(slots.get("flex_days", 0) or 0)
    PIPELINE_FLEX_CAP = 7
    flex_clamped = flex_days_requested > PIPELINE_FLEX_CAP
    if flex_clamped:
        logger.info(
            "orchestrator: flex_days=%d > cap=%d → limitando busca",
            flex_days_requested, PIPELINE_FLEX_CAP,
        )

    # ─── Flex de ROUNDTRIP em duas fases ─────────────────────────────
    # O radar Kayak (barato) varre as combinações (ida, volta) candidatas e
    # escolhe as melhores datas; só nas TOP 2-3 rodamos a cotação completa
    # (milhas + hidden city). Cobre: janela-ida × janela-volta, duração+range,
    # ida-flex + volta fixa, e ±N dias com volta.
    trip_duration = int(slots.get("trip_duration_days") or 0)
    date_end_parsed = _parse_iso_date(slots.get("date_end"))
    return_from = _parse_iso_date(slots.get("return_from"))
    return_to = _parse_iso_date(slots.get("return_to"))
    single_return = _parse_iso_date(slots.get("date_return"))
    trip_type = slots.get("trip_type") or "oneway"

    # ─── INTERNACIONAL SÓ-IDA: quebra de trecho (2 tipos) ─────────────
    # Rota internacional só-ida → orquestra direto + hub-split (Tipo 1) +
    # skip-split (Tipo 2), cada perna validada em milhas. O radar Kayak escolhe
    # o melhor dia (amostra ≤4 datas do intervalo) e a validação cara roda só
    # nele. Round-trip internacional ainda cai no fluxo normal (escopo futuro).
    #
    # FEATURE FLAG (default OFF): esta orquestração faz ~6-8 scrapes pesados e
    # leva ~2min (piso de scraping externo, não removível por trim). Por isso
    # fica desligada por padrão — o internacional segue no run_search normal
    # (~40-50s). Ligue com INTERNATIONAL_SPLIT_ENABLED=1 pra testar/validar.
    _intl_split_on = os.getenv("INTERNATIONAL_SPLIT_ENABLED", "0") == "1"
    if (_intl_split_on and trip_type != "roundtrip"
            and classify_route(origin, destination) == "international"):
        from backend.app.services.international_split import quote_international

        # Progresso ao vivo: emite via stream writer do LangGraph → o SSE do chat
        # mostra "buscando trecho X…" em tempo real. Fora de um stream vira no-op.
        try:
            from langgraph.config import get_stream_writer
            _writer = get_stream_writer()
        except Exception:
            _writer = None

        def _progress(msg: str) -> None:
            if _writer:
                try:
                    _writer({"progress": msg})
                except Exception:
                    pass

        _adults = int(slots.get("adults", 1))
        _cabin = slots.get("cabin", "economy")

        def _deep_search(use_slots: IntakeSlots, *,
                         direct_days: Optional[List[_date]] = None,
                         hubs: Optional[dict] = None,
                         reference: Optional[dict] = None,
                         dates: Optional[List[_date]] = None) -> Optional[ChatState]:
            """Busca cara e validada → state pro presenter, ou None se vier vazio.
            Datas: explícitas (melhor dia de cada rota, da Fase 1) ou um range."""
            try:
                q = quote_international(
                    origin=origin, destination=destination,
                    dates=dates, direct_days=direct_days, hubs=hubs, reference=reference,
                    adults=_adults, cabin=_cabin, on_progress=_progress,
                )
            except Exception as e:
                logger.warning("orchestrator: quote_international falhou: %s", e)
                return None
            if q and q.get("options"):
                audit.log(
                    "search.run.international_split",
                    user_id=user_id, thread_id=thread_id,
                    detail={"origin": origin, "destination": destination,
                            "options": len(q["options"]),
                            "direct_days": q.get("direct_days"), "hubs": q.get("hubs")},
                )
                return {
                    **state, "slots": use_slots, "next_node": "presenter",
                    "search_results": {
                        "ok": True, "international_options": q["options"],
                        "intl_reference": q.get("reference"),
                        "intl_market_signal": q.get("market_signal"),
                    },
                }
            return None

        awaiting = bool(slots.get("intl_awaiting_confirmation"))
        flex_span = (date_end_parsed - date_start).days if (date_end_parsed and date_end_parsed > date_start) else 0

        # FASE 2: vendedor confirmou → roda as buscas, cada rota na SUA melhor
        # data (direto na melhor de origin→dest; cada hub na melhor dele). Se ele
        # citou outra data, ela vira o dia do DIRETO.
        if awaiting:
            conf = slots.get("intl_confirmation") or {}
            text = _latest_human_text(state)
            hubs_conf = conf.get("hubs") or {}
            hubs_days = {h: _parse_iso_date(v) for h, v in hubs_conf.items() if _parse_iso_date(v)}
            dir_day = _parse_iso_date(conf.get("direct_day"))
            if dir_day is None:
                # Radar do direto falhou na Fase 1 → NUNCA usa date_start (era o
                # bug: buscava 10/09 ignorando a data mapeada). Usa a melhor data
                # de hub conhecida (data barata real do intervalo).
                hub_dates = sorted(d for d in hubs_days.values() if d)
                dir_day = hub_dates[0] if hub_dates else date_start
                logger.info("orchestrator: direct_day ausente (radar flaky) → usando %s", dir_day)
            picked = _pick_listed_date(text, list(slots.get("intl_radar_dates") or []))
            if picked:
                dir_day = picked
            elif not _is_affirmative(text):
                logger.info("orchestrator: confirmação ambígua (%r) — seguindo nas melhores datas", text[:60])
            cleared = {**slots, "intl_awaiting_confirmation": False,
                       "intl_confirmation": None, "intl_radar_dates": None}
            res = _deep_search(cleared, direct_days=[dir_day],
                               hubs=hubs_days or None, reference=conf or None)
            if res is not None:
                return res
            slots = cleared  # vazio → segue no fluxo normal com slots limpos

        # FASE 1: flex > 3 dias e ainda não confirmou → radar + pergunta.
        elif flex_span > 3:
            phase1 = _intl_confirmation_message(
                state, slots, origin=origin, destination=destination,
                date_start=date_start, date_end=date_end_parsed, adults=_adults, cabin=_cabin,
            )
            if phase1 is not None:
                return phase1
            res = _deep_search(slots, dates=_sample_dates(date_start, date_end_parsed, cap=6))
            if res is not None:
                return res

        # Flex ≤ 3 dias (ou data única) → busca direta, sem confirmação.
        else:
            res = _deep_search(slots, dates=_sample_dates(date_start, date_end_parsed, cap=6))
            if res is not None:
                return res

        logger.info("orchestrator: international_split vazio; fallback busca normal")

    is_rt_flex = trip_type == "roundtrip" and (
        (return_from and return_to)
        or (trip_duration > 0 and date_end_parsed and date_end_parsed > date_start)
        or (flex_mode_for_pipeline == "range" and date_end_parsed and single_return)
        or (flex_mode_for_pipeline == "plusminus" and flex_days_requested > 0 and single_return)
    )

    if is_rt_flex:
        from backend.app.services.flex_planner import build_candidate_pairs
        from backend.app.services.date_radar import scan_dates
        from backend.app.ai.agents.multi_date import run_multi_date_search

        pairs = build_candidate_pairs(
            depart_start=date_start, depart_end=date_end_parsed,
            return_start=return_from, return_end=return_to,
            single_return=single_return, duration_days=trip_duration,
            flex_mode=flex_mode_for_pipeline, flex_days=flex_days_requested, cap=16,
        )
        if pairs:
            radar = scan_dates(
                pairs, origin=origin, destination=destination,
                adults=int(slots.get("adults", 1)), cabin=slots.get("cabin", "economy"),
            )
            top = (radar.ranked_pairs or pairs)[:3]
            logger.info(
                "orchestrator: radar=%s, %d candidatos → top %d: %s",
                radar.source, len(pairs), len(top),
                [(str(i), str(v)) for i, v in top],
            )
            audit.log(
                "search.run.flex_radar",
                user_id=user_id, thread_id=thread_id,
                detail={
                    "origin": origin, "destination": destination,
                    "candidates": len(pairs), "radar_source": radar.source,
                    "top": [{"ida": str(i), "volta": str(v)} for i, v in top],
                },
            )
            common = dict(
                origin=origin, destination=destination,
                adults=int(slots.get("adults", 1)),
                cabin=slots.get("cabin", "economy"),
                direct_only=bool(slots.get("direct_only", False)),
                baggage_checked=bool(slots.get("baggage_checked") or False),
                flex_mode="none", flex_days=0,
            )
            result = run_multi_date_search(date_pairs=top, common_args=common)
            if result.get("ok"):
                info = result.setdefault("multi_date_info", {})
                info["radar_source"] = radar.source
                info["candidates_scanned"] = len(pairs)
                info["radar_prices"] = radar.price_by_pair

                # COMPARATIVO via SKIP nas TOP-3 datas do Kayak (sem validação de
                # milhas) — hidden city/split ida+volta como referência extra.
                try:
                    from backend.app.services.date_radar import scan_skip_pairs
                    info["skip_prices"] = scan_skip_pairs(
                        top, origin=origin, destination=destination,
                        adults=int(slots.get("adults", 1)),
                        cabin=slots.get("cabin", "economy"),
                    )
                except Exception as e:
                    logger.warning("orchestrator: skip sweep falhou: %s", e)

                # HIDDEN CITY ida-e-volta = DOIS bilhetes só-ida somados (hidden
                # city é one-way). Cota a melhor data do radar como ida (O→D) +
                # volta (D→O) separadas e soma. O presenter compara com o RT
                # normal e recomenda o mais barato validado.
                try:
                    from backend.app.services.roundtrip_hidden_city import quote_roundtrip_two_oneways
                    best_ida, best_volta = top[0]
                    hc_rt = quote_roundtrip_two_oneways(
                        origin=origin, destination=destination,
                        ida_date=best_ida, volta_date=best_volta,
                        adults=int(slots.get("adults", 1)),
                        cabin=slots.get("cabin", "economy"),
                    )
                    if hc_rt:
                        result["roundtrip_two_oneways"] = hc_rt
                except Exception as e:
                    logger.warning("orchestrator: two-oneways falhou: %s", e)

                return {**state, "next_node": "validator", "search_results": result}

            # Cotação completa falhou nas top datas → tenta uma busca fixa na
            # melhor combinação do radar (nunca cai no watchdog genérico).
            logger.warning("orchestrator: flex radar/multi vazio (%s); fallback fixo", result.get("error"))
            best_ida, best_volta = top[0]
            fb = run_search(
                origin=origin, destination=destination,
                date_start=best_ida, date_return=best_volta,
                adults=int(slots.get("adults", 1)), cabin=slots.get("cabin", "economy"),
                direct_only=bool(slots.get("direct_only", False)),
                flex_mode="none", flex_days=0,
                baggage_checked=bool(slots.get("baggage_checked") or False),
            )
            if fb.get("ok") and (fb.get("ranked_offers") or fb.get("money_offers") or fb.get("miles_offers")):
                fb.setdefault("justification", []).insert(
                    0, f"Melhor combinação encontrada: ida {best_ida.strftime('%d/%m')}, volta {best_volta.strftime('%d/%m')}.",
                )
                return {**state, "next_node": "validator", "search_results": fb}
            return {
                **state, "next_node": "end",
                "search_failed_notice": True,
                "messages": [AIMessage(content=(
                    f"Comparei as datas entre {date_start.strftime('%d/%m')} e "
                    f"{(date_end_parsed or return_to or single_return or date_start).strftime('%d/%m')}, "
                    "mas as fontes não retornaram tarifas pra essas combinações agora. "
                    "Quer tentar com datas fixas ou outro período?"
                ))],
            }

    # SÓ-IDA com flexibilidade: o pipeline normal (build_date_plan) já busca
    # todas as datas num único pool paralelo e o ranking escolhe a mais barata —
    # eficiente. (O radar Kayak-first só compensa no ROUNDTRIP, onde o
    # cross-product ida×volta é grande; em ida-só ele só adiciona overhead.)
    # Cidade multi-aeroporto (São Paulo=GRU/VCP, Rio=GIG/SDU): busca os TOP-2
    # aeroportos e junta — senão perde voo barato de aeroporto secundário (ex.:
    # Azul VCP→LIS, que sumia quando a origem virava só CGH/GRU).
    origins = [o for o in (slots.get("origin_iatas") or [origin]) if o][:2]
    _search_kw = dict(
        destination=destination,
        date_start=date_start,
        date_return=_parse_iso_date(slots.get("date_return")),
        adults=int(slots.get("adults", 1)),
        cabin=slots.get("cabin", "economy"),
        direct_only=bool(slots.get("direct_only", False)),
        flex_mode=flex_mode_for_pipeline,
        flex_days=int(slots.get("flex_days", 0)),
        date_end=_parse_iso_date(slots.get("date_end")),
        flex_return=bool(slots.get("flex_return", False)),
        baggage_checked=bool(slots.get("baggage_checked") or False),
    )
    if len(origins) > 1:
        result = _merge_origin_searches(origins, _search_kw)
    else:
        result = run_search(origin=origins[0], **_search_kw)

    if not result.get("ok"):
        audit.log(
            "search.failed",
            user_id=user_id, thread_id=thread_id,
            severity="error",
            detail={"error": result.get("error")},
        )
        return {
            **state,
            "next_node": "end",
            "messages": [AIMessage(content=(
                "Tive um problema ao consultar agora. Pode tentar de novo em alguns segundos?"
            ))],
            "search_results": None,
        }

    # Anexa flag de flex truncado pra o presenter avisar o vendedor.
    if flex_clamped:
        result["flex_clamped_info"] = {
            "requested_days": flex_days_requested,
            "actual_cap_days": PIPELINE_FLEX_CAP,
        }

    # ROUNDTRIP FIXO (sem flex): hidden city é one-way, então o ida-e-volta real
    # também é a soma de 2 bilhetes só-ida (ida O→D + volta D→O), cada um
    # validado. O presenter compara com o RT normal e recomenda o mais barato.
    if trip_type == "roundtrip" and single_return:
        try:
            from backend.app.services.roundtrip_hidden_city import quote_roundtrip_two_oneways
            hc_rt = quote_roundtrip_two_oneways(
                origin=origin, destination=destination,
                ida_date=date_start, volta_date=single_return,
                adults=int(slots.get("adults", 1)),
                cabin=slots.get("cabin", "economy"),
            )
            if hc_rt:
                result["roundtrip_two_oneways"] = hc_rt
        except Exception as e:
            logger.warning("orchestrator: two-oneways (RT fixo) falhou: %s", e)

    return {
        **state,
        "next_node": "validator",
        "search_results": result,
    }
