import streamlit as st
import pandas as pd
import json
from datetime import date
from pathlib import Path
from typing import List
from pcd.run import run_pipeline
from pcd.core.schema import TripType, SourceType
from pcd.core.conversion import RATES_BRL_PER_MILE as RATES
from pcd.nlp.intent_parser import parse_intent_ptbr
from miles_app.buscamilhas_client import COMPANHIAS_NACIONAIS, COMPANHIAS_INTERNACIONAIS
from mcp_offer_parser import extract_mcp_offers

from ui.styles import inject_styles, render_topbar
from ui.formatters import (
    CIA_META as _CIA_META,
    INTERNACIONAIS_SEM_BAGAGEM_EXTRA as _INTERNACIONAIS_SEM_BAGAGEM_EXTRA,
    src_name as _src_name,
    tab_key as _tab_key,
    miles_to_brl,
    format_duration,
    safe_int_miles,
    safe_float,
    get_baggage_price,
    source_is,
    id_prefix as _id_prefix,
)
from ui.renderer import (
    build_table_rows as _build_table_rows_core,
    render_itin_card,
)

_MCP_FIXTURE = Path(__file__).parent / "debug_dumps" / "mcp_all_airlines_GRU_JFK_sample.json"

# Helpers, metadados e taxas de conversão estão em ui/ e pcd/core/conversion.py.
# Importados acima como aliases para preservar os nomes usados por todo o app.


def build_table_rows(offers, include_baggage=False, id_prefix=""):
    """Wrapper local que injeta `adults` a partir do session_state."""
    pi = st.session_state.get("parsed_intent")
    adults = getattr(pi, "adults", 1) if pi else 1
    return _build_table_rows_core(offers, include_baggage, id_prefix, adults=adults)


# ──────────────────────────────────────────────────────────────
# Markup Kayak — preço de venda
#
# Convenção em todo o app:
#   - offer.equivalent_brl / offer.price_brl = PREÇO DE MERCADO (sem markup)
#     Valor cru do Kayak, é o que o cliente vê na pesquisa pública.
#   - kayak_sell_price(offer) = PREÇO DE VENDA (com markup aplicado)
#     É o que a agência cobra. Aplica-se SÓ no momento da exibição —
#     nas tabelas operacionais a referência é o mercado.
# ──────────────────────────────────────────────────────────────
def kayak_markup_pct() -> float:
    """Percentual de markup do Kayak (0.10 = 10%) — configurável via session_state."""
    try:
        return float(st.session_state.get("kayak_markup_pct", 0.10))
    except (TypeError, ValueError):
        return 0.10


def kayak_sell_price(offer_or_value) -> float:
    """Preço de venda (mercado × (1+markup)). Aceita um UnifiedOffer ou
    um número de preço de mercado."""
    if isinstance(offer_or_value, (int, float)):
        market = float(offer_or_value)
    else:
        market = safe_float(getattr(offer_or_value, "equivalent_brl", 0))
    return round(market * (1.0 + kayak_markup_pct()), 2)


def _synthesize_kayak_offer_from_cache(
    cached, origin_iata: str, destination_iata: str,
):
    """Constrói um UnifiedOffer mínimo a partir de um FlightOptionLite cacheado
    pela Cotação Inteligente. Usado para injetar no pipeline_result o EXATO
    preço Kayak mostrado no gráfico, evitando divergência com nova chamada Kayak.

    IMPORTANTE: armazena o PREÇO DE MERCADO (sem markup) — markup é aplicado
    só no momento da exibição via kayak_sell_price()."""
    if cached is None:
        return None
    from datetime import datetime as _dt
    from pcd.core.schema import (
        UnifiedOffer, Itinerary, Segment, SourceType, TripType, LayoverCategory,
    )

    def _parse_dt(s):
        if not isinstance(s, str) or not s:
            return None
        s2 = s.replace("Z", "+00:00") if s.endswith("Z") else s
        try:
            return _dt.fromisoformat(s2)
        except Exception:
            return None

    dep_dt = _parse_dt(getattr(cached, "departure_time", None))
    arr_dt = _parse_dt(getattr(cached, "arrival_time", None))
    if dep_dt is None or arr_dt is None:
        return None

    carrier = (getattr(cached, "main_carrier_iata", "") or "").upper() or "XX"
    seg = Segment(
        origin=origin_iata.upper(), destination=destination_iata.upper(),
        departure_dt=dep_dt, arrival_dt=arr_dt, carrier=carrier,
    )
    itin = Itinerary(
        segments=[seg], duration_min=getattr(cached, "duration_min", None) or None,
    )
    market_price = round(float(getattr(cached, "price_brl", 0.0)), 2)
    return UnifiedOffer(
        source=SourceType.KAYAK,
        airline=carrier,
        trip_type=TripType.ONEWAY,
        outbound=itin,
        price_brl=market_price,
        price_amount=market_price,
        price_currency="BRL",
        equivalent_brl=market_price,
        stops_out=int(getattr(cached, "stops", 0) or 0),
        layover_out=LayoverCategory.DIRECT if (getattr(cached, "stops", 0) or 0) == 0 else LayoverCategory.CONNECTION,
        deeplink=None,
    )


# ──────────────────────────────────────────────────────────────
# Click-to-select nas tabelas (Problemas 3 e 4)
#
# Cada tab que renderiza dataframe de ofertas usa o helper abaixo,
# que ativa seleção single-row e propaga o ID escolhido + aba ativa
# para o session_state. O Itinerário Detalhado lê esses valores e
# filtra/pré-seleciona automaticamente.
# ──────────────────────────────────────────────────────────────
def _render_selectable_offers_df(df, cols_visible, tab_key: str, df_key: str):
    """Renderiza um dataframe de ofertas com seleção clicável.

    - `tab_key` identifica a aba (governa o filtro do selectbox no fim da página).
    - `df_key` é a key única do widget Streamlit.

    Side-effects no session_state quando o usuário clica numa linha:
      * selected_flight_id ← ID da oferta selecionada
      * active_tab         ← tab_key
      * _scroll_to_itin    ← True (faz o scroll automático até o itinerário)
    """
    if df is None or df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
        return
    visible = [c for c in cols_visible if c in df.columns]
    event = st.dataframe(
        df[visible],
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=df_key,
    )
    try:
        rows_sel = (event.selection.rows or []) if event is not None else []  # type: ignore[attr-defined]
    except Exception:
        rows_sel = []
    if rows_sel and "ID" in df.columns:
        sel_idx = int(rows_sel[0])
        if 0 <= sel_idx < len(df):
            fid = str(df.iloc[sel_idx]["ID"])
            if st.session_state.get("selected_flight_id") != fid \
               or st.session_state.get("active_tab") != tab_key:
                st.session_state["selected_flight_id"] = fid
                st.session_state["active_tab"] = tab_key
                st.session_state["_scroll_to_itin"] = True


# Mapa: tab_key → lista de prefixos de ID aceitos pelo filtro do selectbox.
# Tabs que listam ofertas heterogêneas (Veredito, Ranking Geral) liberam tudo.
_TAB_PREFIXES: dict[str, list[str] | None] = {
    "verdito":   None,           # libera tudo
    "ranking":   None,           # libera tudo
    "dinheiro":  ["$"],
    "cia_latam": ["L"],
    "cia_gol":   ["G"],
    "cia_azul":  ["A"],
    "cia_tap":   ["TP"],
    "cia_american airlines": ["AA"],
    "cia_interline": ["IN"],
    "cia_copa":  ["CM"],
    "mcp_award": ["W", "IB"],    # MCP inclui Iberia e outros internacionais
    "mcp_qatar": ["QR"],
}

# Título exibido sobre o itinerário detalhado, por aba.
_TAB_ITIN_TITLE: dict[str, str] = {
    "verdito":   "Todos os voos",
    "ranking":   "Todos os voos",
    "dinheiro":  "Dinheiro",
    "cia_latam": "LATAM",
    "cia_gol":   "GOL",
    "cia_azul":  "AZUL",
    "cia_tap":   "TAP",
    "cia_american airlines": "American Airlines",
    "cia_interline": "Interline",
    "cia_copa":  "COPA",
    "mcp_award": "Internacional (MCP)",
    "mcp_qatar": "Qatar",
}


def _id_alpha_prefix(fid: str) -> str:
    """'AA3' → 'AA'; '$12' → '$'; 'L7' → 'L'."""
    out = []
    for ch in (fid or ""):
        if ch.isdigit():
            break
        out.append(ch)
    return "".join(out)


# ═══════════════════════════════════════════════════════════════
# PAGE CONFIG
# ═══════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Agente de Cotação PcD", page_icon="✈️",
    layout="wide", initial_sidebar_state="collapsed",
)

inject_styles()
render_topbar()


# ── Estado: flexibilidade da Cotação Inteligente (sincronizado entre
#    o slider do popover e o numérico inline ao lado do toggle).
if "smart_flex_days" not in st.session_state:
    st.session_state["smart_flex_days"] = 4

def _sync_smart_flex_from_slider():
    st.session_state["smart_flex_days"] = int(st.session_state["smart_flex_slider"])

def _sync_smart_flex_from_inline():
    st.session_state["smart_flex_days"] = int(st.session_state["smart_flex_inline"])


# Defaults dos checkboxes — garantem que cada flag esteja definida mesmo
# quando o submenu correspondente está oculto (provedor diferente).
s_latam = s_gol = s_azul = False
s_tap = s_american = s_interline = s_copa = s_qatar = False
s_money = True
s_mcp = False
e_smiles = e_latam_p = e_azul = e_azul_int = e_copa_e = e_iberia = e_british = False
e_money = True
e_debug = False
provider = st.session_state.get("miles_provider", "BuscaMilhas")

# ── Engrenagem / Configurações ──
col_gear, _ = st.columns([1, 14])
with col_gear:
    with st.popover("⚙️"):
        # ── Seletor de provedor (no topo, governa o submenu abaixo) ──
        st.markdown("**Provedor de busca de milhas:**")
        provider = st.radio(
            "Provedor",
            options=["BuscaMilhas", "Economilhas"],
            index=0 if st.session_state.get("miles_provider", "BuscaMilhas") == "BuscaMilhas" else 1,
            horizontal=True,
            label_visibility="collapsed",
            key="miles_provider_radio",
        )
        st.session_state["miles_provider"] = provider

        st.markdown("**Configurações**")
        use_fixtures = st.toggle("Dados Estáticos (Mock)", value=False)

        if provider == "BuscaMilhas":
            # ── Fontes: Nacionais ──
            st.markdown('<div class="cfg-group-label">📍 Nacionais</div>', unsafe_allow_html=True)
            s_latam = st.checkbox("LATAM", value=True, key="chk_latam")
            s_gol   = st.checkbox("GOL",   value=True, key="chk_gol")
            s_azul  = st.checkbox("AZUL",  value=True, key="chk_azul")

            # ── Fontes: Internacionais ──
            st.markdown('<div class="cfg-group-label">🌍 Internacionais (Busca Milhas)</div>', unsafe_allow_html=True)
            s_tap       = st.checkbox("TAP",              value=False, key="chk_tap")
            s_american  = st.checkbox("AMERICAN AIRLINES",value=False, key="chk_american")
            s_interline = st.checkbox("INTERLINE",         value=False, key="chk_interline")
            s_copa      = st.checkbox("COPA",              value=False, key="chk_copa")
            s_qatar     = st.checkbox("QATAR (vía MCP)",    value=False, key="chk_qatar")

            s_money = st.checkbox("Dinheiro (Kayak)", value=True, key="chk_money")
        else:
            # ── Fontes Economilhas ──
            st.markdown('<div class="cfg-group-label">💎 Programas Economilhas</div>', unsafe_allow_html=True)
            e_smiles    = st.checkbox("Smiles (GOL)",                value=True,  key="ec_smiles")
            e_latam_p   = st.checkbox("LATAM Pass",                  value=True,  key="ec_latam")
            e_azul      = st.checkbox("Azul Fidelidade",             value=True,  key="ec_azul")
            e_azul_int  = st.checkbox("Azul Pelo Mundo (Interline)", value=False, key="ec_azul_int")
            e_copa_e    = st.checkbox("Copa ConnectMiles",           value=False, key="ec_copa")
            e_iberia    = st.checkbox("Iberia Plus",                 value=False, key="ec_iberia")
            e_british   = st.checkbox("British Airways Avios",       value=False, key="ec_british")

            st.markdown('<div class="cfg-group-label">💵 Dinheiro</div>', unsafe_allow_html=True)
            e_money = st.checkbox("Dinheiro (Kayak)", value=True, key="ec_money")

            e_debug = st.checkbox(
                "🐛 Debug: salvar payloads brutos por programa",
                value=False, key="ec_debug",
                help="Quando marcado, cada `data` recebido é gravado em debug_dumps/.",
            )

            # ── Indicador de quota Economilhas ──
            if st.button("📊 Verificar quota", key="ec_check_quota"):
                try:
                    from economilhas_client import get_quota_cached
                    q = get_quota_cached(force=True)
                    if isinstance(q, dict):
                        limit = q.get("limit") or q.get("monthlyLimit") or q.get("plan", {}).get("limit")
                        consumed = q.get("consumed") or q.get("used")
                        remaining = q.get("remaining") or q.get("available")
                        usage_by = q.get("usageByCompany") or q.get("byCompany") or {}
                        st.success(
                            f"Quota — limite: **{limit}**, consumido: **{consumed}**, restante: **{remaining}**"
                        )
                        if usage_by:
                            st.caption(f"Uso por programa: {usage_by}")
                    else:
                        st.success(f"Quota: {q}")
                except Exception as ex:
                    st.error(f"Falha ao consultar quota: {ex}")

        st.markdown("**Parâmetros:**")
        top_n = st.slider("Qtd. resultados", 1, 15, 5)

        # ── Cotação Inteligente: flexibilidade ──
        st.markdown('<div class="cfg-group-label">🧠 Cotação Inteligente</div>', unsafe_allow_html=True)
        st.slider(
            "Flexibilidade Cotação Inteligente (± dias)",
            min_value=1, max_value=10,
            value=int(st.session_state["smart_flex_days"]),
            step=1,
            key="smart_flex_slider",
            on_change=_sync_smart_flex_from_slider,
            help="Quantos dias antes e depois da data solicitada o Kayak vai analisar.",
        )

        # ── Taxas de conversão: Nacionais ──
        st.markdown('<div class="cfg-group-label">💱 Taxas Nacionais (R$/milha)</div>', unsafe_allow_html=True)
        RATES["LATAM"] = st.number_input("LATAM",   value=RATES["LATAM"], step=0.001, format="%.4f", key="rate_latam")
        RATES["GOL"]   = st.number_input("GOL",     value=RATES["GOL"],   step=0.001, format="%.4f", key="rate_gol")
        RATES["AZUL"]  = st.number_input("AZUL",    value=RATES["AZUL"],  step=0.001, format="%.4f", key="rate_azul")

        # ── Taxas de conversão: Internacionais ──
        st.markdown('<div class="cfg-group-label">💱 Taxas Internacionais (R$/milha)</div>', unsafe_allow_html=True)
        RATES["TAP"]              = st.number_input("TAP",              value=RATES["TAP"],       step=0.001, format="%.4f", key="rate_tap")
        RATES["AMERICAN AIRLINES"]= st.number_input("AMERICAN AIRLINES",value=RATES.get("AMERICAN AIRLINES", 0.0220), step=0.001, format="%.4f", key="rate_american")
        RATES["INTERLINE"]        = st.number_input("INTERLINE",        value=RATES["INTERLINE"], step=0.001, format="%.4f", key="rate_interline")
        RATES["COPA"]             = st.number_input("COPA",             value=RATES["COPA"],      step=0.001, format="%.4f", key="rate_copa")

        # ── Taxas MCP (Programas Internacionais) ──
        st.markdown('<div class="cfg-group-label">🌍 MCP Award (R$/ponto)</div>', unsafe_allow_html=True)
        s_mcp = st.checkbox("Buscar via MCP Award", value=True, key="chk_mcp")
        RATES["AVIOS"]      = st.number_input("Avios (BA/Qatar)", value=RATES["AVIOS"],      step=0.001, format="%.4f", key="rate_avios")
        RATES["ASIA MILES"] = st.number_input("Asia Miles (CX)", value=RATES["ASIA MILES"], step=0.001, format="%.4f", key="rate_asiamiles")

        RATES["DEFAULT"] = RATES["GOL"]

# Mapa de companhias ativas — alimenta as tabs do Veredito.
# Quando o provedor é Economilhas, o mapa é construído a partir dos checkboxes
# Economilhas (que reusam SourceTypes existentes — ver economilhas_pipeline.py).
if provider == "BuscaMilhas":
    _CIA_ACTIVE = {
        "KAYAK":            s_money,
        "LATAM":            s_latam,
        "GOL":              s_gol,
        "AZUL":             s_azul,
        "TAP":              s_tap,
        "AMERICAN AIRLINES":s_american,
        "INTERLINE":        s_interline,
        "COPA":             s_copa,
        "MCP_AWARD":        s_mcp,
        "QATAR":            s_qatar,
    }
else:
    _CIA_ACTIVE = {
        "KAYAK":            e_money,
        # Smiles (GOL) → tab GOL; LATAM Pass → tab LATAM; Azul Fid./Interline → tabs AZUL/INTERLINE
        "LATAM":            e_latam_p,
        "GOL":              e_smiles,
        "AZUL":             e_azul,
        "INTERLINE":        e_azul_int,
        "COPA":             e_copa_e,
        "IBERIA":           e_iberia,
        "TAP":              False,
        "AMERICAN AIRLINES": False,
        # British Avios cai em MCP_AWARD na conversão.
        "MCP_AWARD":        e_british,
        "QATAR":            False,
    }

# Lista de companhias que o usuário ativou para buscar
companhias_selecionadas: List[str] = [c for c, ativo in _CIA_ACTIVE.items() if ativo]

# ── Prompt ──
col_prompt, col_btn = st.columns([7, 2], vertical_alignment="bottom")
with col_prompt:
    prompt_text = st.text_area(
        "Para onde vamos?",
        value=st.session_state.get("prompt_input", ""),
        height=90,
        placeholder="Ex: Brasília para Fortaleza ida 30/10/2026 volta 15/11/2026...",
    )
    st.session_state["prompt_input"] = prompt_text
with col_btn:
    use_llm = st.checkbox("Interpretar com Grok", value=True)
    buscar  = st.button("✈️  BUSCAR AGORA", use_container_width=True)

# Indicador discreto do provedor ativo logo abaixo do botão.
st.caption(f"🔌 Provedor ativo: **{provider}**")

# ── Toggle Cotação Inteligente + flexibilidade inline ──
_flex_now = int(st.session_state.get("smart_flex_days", 4))
col_smart_t, col_smart_f, _col_smart_pad = st.columns([4, 2, 8], vertical_alignment="center")
with col_smart_t:
    smart_mode = st.toggle(
        "🧠 Cotação Inteligente",
        value=st.session_state.get("smart_mode", False),
        key="smart_mode",
        help=(
            f"Analisa ±{_flex_now} dias automaticamente, identifica o dia "
            "mais barato e os melhores programas de milhas para a rota."
        ),
    )
with col_smart_f:
    if smart_mode:
        st.number_input(
            "± dias",
            min_value=1, max_value=10,
            value=int(st.session_state["smart_flex_days"]),
            step=1,
            key="smart_flex_inline",
            on_change=_sync_smart_flex_from_inline,
            help="Flexibilidade da Cotação Inteligente (sincronizado com o slider em ⚙️).",
        )

if buscar and prompt_text:
    # Limpa estado anterior — cada nova busca recomeça do zero.
    # Inclui o widget-key do select de data: opções podem mudar entre buscas
    # e um valor stale fora das novas opções dispararia erro.
    for _k in (
        "smart_result", "smart_progress", "smart_selected_date",
        "pipeline_result", "smart_active",
        "split_result", "split_data_key", "split_active",
        "split_fits", "split_fit_cache_keys", "split_fitted_combinations",
        "miles_match_cache_keys",
        "smart_date_picker",
    ):
        st.session_state.pop(_k, None)

    # Invalida o cache HTTP — nova busca pode ter mudado rota/data principal,
    # então respostas anteriores não devem ser reutilizadas. O TTL de 10 min
    # ainda protege a janela "vendedor clicou várias vezes na mesma rota",
    # mas nova busca é o sinal explícito de invalidação.
    try:
        from pcd.cache import invalidate as _cache_invalidate, stats as _cache_stats
        _dropped = _cache_invalidate()  # limpa tudo (kayak + buscamilhas + economilhas)
        # TEMP_PERF — remover após validar
        print(f"⏱ TEMP_PERF cache invalidado: {_dropped} entradas | stats anteriores: {_cache_stats()}")
    except Exception:
        pass

    flex_smart_days = int(st.session_state.get("smart_flex_days", 4))

    with st.spinner("Analisando pedido..."):
        intent = parse_intent_ptbr(prompt_text, use_llm=use_llm)
        st.session_state["parsed_intent"] = intent
        if st.session_state.get("v_flex") is not None:
            intent.flex_days = st.session_state["v_flex"]
            if (intent.flex_days or 0) > 0 and intent.flex_mode == "none":
                intent.flex_mode = "plusminus"

    _smart_date_req = intent.date_start or intent.depart_date_from
    if smart_mode and intent.origin_iata and intent.destination_iata and _smart_date_req:
        # ETAPA 1 — só Agente 1 (Kayak) + Agente 2 (mapeamento de programas).
        # Veredito PcD / tabs / itinerário ficam adiados até o vendedor
        # escolher a data e clicar "Buscar milhas para esta data".
        from pcd.agents.smart_quote import SmartQuoteAgent

        smart_progress: list[str] = []
        def _capture(msg: str):
            smart_progress.append(msg)

        with st.spinner(f"🧠 Analisando datas (±{flex_smart_days}) e programas..."):
            try:
                smart_result = SmartQuoteAgent().run(
                    origin=intent.origin_iata,
                    destination=intent.destination_iata,
                    date_requested=_smart_date_req,
                    adults=getattr(intent, "adults", 1) or 1,
                    return_date=intent.date_return,
                    flex_days=flex_smart_days,
                    progress_cb=_capture,
                )
            except Exception as e:
                smart_result = None
                smart_progress.append(f"⚠️ Cotação Inteligente falhou: {str(e)[:160]}")

        st.session_state["smart_result"] = smart_result
        st.session_state["smart_progress"] = smart_progress
        st.session_state["smart_active"] = True
        # NÃO roda run_pipeline aqui — Etapa 2 dispara sob demanda.
    else:
        # Modo clássico — toggle off (ou sem dados mínimos): pipeline completo.
        st.session_state["smart_active"] = False
        st.session_state.pop("economilhas_partial", None)
        if provider == "BuscaMilhas":
            _spinner_msg = "Buscando voos (BuscaMilhas)..."
            if intent.flex_mode == "range" and intent.depart_date_from and intent.depart_date_to:
                _n_days = (intent.depart_date_to - intent.depart_date_from).days + 1
                _n_days_capped = min(_n_days, 7)
                if _n_days > 7:
                    st.info(
                        "Flexibilidade limitada a 7 dias para preservar quota da API. "
                        "Use a Cotação Inteligente para análises mais amplas."
                    )
                _spinner_msg = f"Buscando voos com flexibilidade ({_n_days_capped} dias)..."
            elif intent.flex_mode == "plusminus" and (intent.flex_days or 0) > 0:
                _spinner_msg = f"Buscando voos com flexibilidade ±{intent.flex_days} dias..."
            with st.spinner(_spinner_msg):
                res = run_pipeline(
                    prompt=prompt_text, top_n=top_n, use_fixtures=use_fixtures,
                    origin=intent.origin_iata, destination=intent.destination_iata,
                    date_start=intent.date_start or intent.depart_date_from,
                    date_end=intent.depart_date_to,
                    date_return=intent.date_return,
                    flex_mode=intent.flex_mode,
                    flex_days=intent.flex_days or 0,
                    flex_return=intent.flex_return or False,
                    direct_only=intent.direct_only,
                    companhias=companhias_selecionadas if companhias_selecionadas else None,
                    trip_type=intent.trip_type,
                )
                st.session_state["pipeline_result"] = res
        else:
            # Provedor Economilhas — uma única chamada cobre todos os
            # programas de milhas marcados; cash continua via Kayak.
            from pcd.agents.economilhas_pipeline import run_pipeline_economilhas
            miles_airlines = []
            if e_smiles:   miles_airlines.append("SMILES")
            if e_latam_p:  miles_airlines.append("LATAM")
            if e_azul:     miles_airlines.append("AZUL")
            if e_azul_int: miles_airlines.append("AZUL_INTERLINE")
            if e_copa_e:   miles_airlines.append("COPA")
            if e_iberia:   miles_airlines.append("IBERIA")
            if e_british:  miles_airlines.append("BRITISH")

            _spinner_msg_eco = "Buscando voos (Economilhas)..."
            if intent.flex_mode == "range" and intent.depart_date_from and intent.depart_date_to:
                _n_days = (intent.depart_date_to - intent.depart_date_from).days + 1
                _n_days_capped = min(_n_days, 7)
                if _n_days > 7:
                    st.info(
                        "Flexibilidade limitada a 7 dias para preservar quota da API. "
                        "Use a Cotação Inteligente para análises mais amplas."
                    )
                _spinner_msg_eco = f"Buscando voos com flexibilidade ({_n_days_capped} dias)..."
            elif intent.flex_mode == "plusminus" and (intent.flex_days or 0) > 0:
                _spinner_msg_eco = f"Buscando voos com flexibilidade ±{intent.flex_days} dias..."
            with st.spinner(_spinner_msg_eco):
                try:
                    res, partial_failures = run_pipeline_economilhas(
                        prompt=prompt_text, top_n=top_n, use_fixtures=use_fixtures,
                        origin=intent.origin_iata, destination=intent.destination_iata,
                        date_start=intent.date_start or intent.depart_date_from,
                        date_end=intent.depart_date_to,
                        date_return=intent.date_return,
                        flex_mode=intent.flex_mode,
                        flex_days=intent.flex_days or 0,
                        flex_return=intent.flex_return or False,
                        direct_only=intent.direct_only,
                        adults=getattr(intent, "adults", 1) or 1,
                        miles_airlines=miles_airlines,
                        use_kayak_cash=bool(e_money),
                        debug=bool(e_debug),
                        trip_type=intent.trip_type,
                    )
                except Exception as ex_call:
                    res = None
                    partial_failures = [{
                        "airline": "ALL",
                        "message": f"Falha geral Economilhas: {str(ex_call)[:200]}",
                        "providerStatusCode": None, "fatal": True,
                    }]
                if res is not None:
                    st.session_state["pipeline_result"] = res
                st.session_state["economilhas_partial"] = partial_failures

# ── Chips de validação ──
if st.session_state.get("parsed_intent"):
    pi     = st.session_state["parsed_intent"]
    is_rt  = getattr(pi, "trip_type", "roundtrip") == "roundtrip"
    is_dir = getattr(pi, "direct_only", False)
    ida_f  = pi.date_start.strftime("%d/%m/%Y")  if pi.date_start  else "—"
    vol_f  = pi.date_return.strftime("%d/%m/%Y") if pi.date_return else "—"
    adults = getattr(pi, "adults", 1)

    trip_b  = '<span class="p-badge-rt">Ida e Volta</span>'  if is_rt else '<span class="p-badge-ow">Somente Ida</span>'
    dir_b   = '<span class="p-badge-dir">Voo Direto</span>'  if is_dir else ""
    vol_c   = f"<span class='p-chip'><b>Volta</b> {vol_f}</span>" if is_rt else ""

    flex_b = ""
    if pi.flex_mode == "plusminus":
        flex_b = f"<span class='p-chip'><b>Flexibilidade</b> ± {pi.flex_days} dias</span>"
    elif pi.flex_mode == "range" and pi.depart_date_from and pi.depart_date_to:
        flex_b = f"<span class='p-chip'><b>Flexibilidade</b> {pi.depart_date_from.strftime('%d/%m')} a {pi.depart_date_to.strftime('%d/%m')}</span>"

    st.markdown(f"""
<div class="parsed-wrap">
  {trip_b}
  <span class="p-chip"><b>De</b> {pi.origin_iata}</span>
  <span class="p-chip"><b>Para</b> {pi.destination_iata}</span>
  <span class="p-chip"><b>Ida</b> {ida_f}</span>
  {vol_c}
  <span class="p-chip"><b>Adultos</b> {adults}</span>
  {dir_b}
  {flex_b}
</div>
""", unsafe_allow_html=True)

    with st.expander("Ajustar campos manualmente", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.text_input("Origem (IATA)",  value=pi.origin_iata,      key="v_ori")
            st.text_input("Destino (IATA)", value=pi.destination_iata, key="v_des")
        with c2:
            st.date_input("Data de ida",   value=pi.date_start,  key="v_ida")
            st.date_input("Data de volta", value=pi.date_return, key="v_vol")
        with c3:
            st.checkbox("Incluir Bagagem 23kg",  value=False,   key="v_bagagem")
            st.checkbox("Apenas Voo Direto",     value=is_dir,  key="v_dir")
            st.number_input("Dias de Flexibilidade", min_value=0, max_value=7, value=int(pi.flex_days or 0), key="v_flex")

# ── Resultados ──
# Quando a Cotação Inteligente está ativa, a Etapa 1 (smart_result) renderiza
# acima do Veredito; a Etapa 2 (pipeline_result) só é gerada após o vendedor
# clicar em "Buscar milhas para esta data". Stop só quando nada para mostrar.
if "pipeline_result" not in st.session_state and not st.session_state.get("smart_result"):
    st.stop()


# ═══════════════════════════════════════════════════════════════
# Cotação Inteligente — exibida ANTES das tabs quando ativada
# ═══════════════════════════════════════════════════════════════
_SMART_CSS = """
<style>
/* Cotação Inteligente — visual padrão Google Flights / Kayak */
.smart-header{background:linear-gradient(135deg,#0d2b6e 0%,#1a56a0 100%);
    border-radius:14px;padding:18px 24px;display:flex;align-items:center;gap:18px;
    margin:6px 0 18px 0;box-shadow:0 2px 8px rgba(13,43,110,.12);}
.smart-icon{font-size:42px;line-height:1;}
.smart-title{color:#fff;font-size:22px;font-weight:700;letter-spacing:.2px;}
.smart-sub{color:rgba(255,255,255,.78);font-size:12px;margin-top:3px;}

.smart-card{background:#fff!important;color:#1a2236!important;border:1px solid #dde3ef;border-radius:12px;
    padding:18px 22px;box-shadow:0 1px 3px rgba(13,43,110,.04);margin-bottom:16px;}
.smart-card *{color:#1a2236;}
.smart-card .smart-card-title{color:#6b7a99!important;}
.smart-card-title{font-size:13px;font-weight:600;color:#6b7a99;
    text-transform:uppercase;letter-spacing:.06em;margin-bottom:14px;}

/* ── Calendário ── */
.price-calendar{display:flex;align-items:flex-end;justify-content:space-between;
    gap:8px;padding:10px 4px 0 4px;min-height:240px;}
.cal-col{flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;
    cursor:default;position:relative;padding:4px 2px;border-radius:8px;
    transition:background .12s ease;}
.cal-col.selected{background:rgba(26,86,160,.10);box-shadow:0 0 0 2px #1a56a0 inset;}
.cal-badge{font-size:10px;font-weight:700;color:#1a7a4a;background:#eaf4ef;
    border:1px solid #b8ddc8;padding:2px 8px;border-radius:10px;margin-bottom:4px;
    white-space:nowrap;}
.cal-badge.req{color:#c0392b;background:#fdf0f2;border-color:#f0c4c8;}
.cal-badge.spacer{visibility:hidden;}
.cal-bar-wrap{display:flex;align-items:flex-end;height:170px;width:100%;
    justify-content:center;}
.cal-bar{width:36px;border-radius:6px 6px 2px 2px;transition:transform .15s ease;
    position:relative;}
.cal-col:hover .cal-bar{transform:translateY(-3px);}
.cal-bar.anchor{background:#1a7a4a;box-shadow:0 0 0 2px #1a7a4a,0 0 12px rgba(26,122,74,.35);}
.cal-bar.req{background:#c0392b;}
.cal-bar.normal{background:#1a56a0;opacity:.7;}
.cal-date{font-size:12px;font-weight:600;color:#1a2236;margin-top:6px;}
.cal-price{font-size:11px;color:#6b7a99;font-weight:500;}
.cal-tooltip{visibility:hidden;position:absolute;bottom:100%;left:50%;
    transform:translateX(-50%);background:#0d2b6e;color:#fff;font-size:11px;
    padding:6px 10px;border-radius:6px;white-space:nowrap;z-index:50;
    box-shadow:0 2px 6px rgba(0,0,0,.18);margin-bottom:6px;}
.cal-tooltip::after{content:'';position:absolute;top:100%;left:50%;
    transform:translateX(-50%);border:5px solid transparent;border-top-color:#0d2b6e;}
.cal-col:hover .cal-tooltip{visibility:visible;}

/* ── Cards de métricas ── */
.metric-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;
    margin-top:18px;}
.metric-card{background:#fff;border:1px solid #dde3ef;border-radius:12px;
    padding:16px 18px;}
.metric-card.best{border-top:3px solid #1a7a4a;}
.metric-card.req{border-top:3px solid #c0392b;}
.metric-card.eco{border-top:3px solid #1a56a0;}
.metric-label{font-size:11px;font-weight:700;color:#6b7a99;
    text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;}
.metric-date{font-size:14px;color:#1a2236;font-weight:600;margin-bottom:6px;}
.metric-value{font-size:24px;font-weight:800;line-height:1.05;}
.metric-value.green{color:#1a7a4a;}
.metric-value.red{color:#c0392b;}
.metric-value.blue{color:#1a56a0;}
.metric-foot{font-size:11px;color:#6b7a99;margin-top:6px;}

/* ── Chips de companhias ── */
.airline-chip-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px;}
.airline-chip{display:inline-flex;align-items:center;font-size:12px;
    font-weight:600;padding:5px 12px;border-radius:14px;
    border-left:3px solid #1a56a0;background:#e8f0fb;color:#1a2236;}

/* ── Cards de programas ── */
.program-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;
    margin-top:10px;}
.program-card{background:#fff;border:1px solid #dde3ef;border-radius:12px;
    overflow:hidden;display:flex;flex-direction:column;}
.program-card.disabled{opacity:.55;background:#f5f6fa;}
.program-head{padding:14px 16px;color:#fff;font-weight:700;font-size:14px;
    display:flex;align-items:center;gap:8px;}
.program-head.smiles{background:linear-gradient(135deg,#FF5B00,#cc4900);}
.program-head.latam{background:linear-gradient(135deg,#E31837,#a8112a);}
.program-head.azul{background:linear-gradient(135deg,#0032A0,#002577);}
.program-head.disabled{background:#9aa3b3;}
.program-body{padding:14px 16px;flex:1;}
.program-section{font-size:11px;font-weight:700;color:#6b7a99;
    text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;}
.program-empty{color:#9aa3b3;font-size:12px;font-style:italic;}
.program-badges{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;}
.prog-badge{font-size:10px;font-weight:700;padding:3px 9px;border-radius:10px;
    text-transform:uppercase;letter-spacing:.04em;}
.prog-badge.own{background:#e8f0fb;color:#0d2b6e;border:1px solid #c5d3eb;}
.prog-badge.award{background:#eaf4ef;color:#1a7a4a;border:1px solid #b8ddc8;}

/* Mensagem chave */
.smart-message{padding:14px 18px;border-radius:10px;font-size:14px;
    margin-top:18px;display:flex;align-items:center;gap:10px;}
.smart-message.green{background:#eaf4ef;border:1px solid #b8ddc8;color:#1a4a30;}
.smart-message.red{background:#fdf0f2;border:1px solid #f0c4c8;color:#7a2418;}
</style>
"""

_PT_MONTHS = {
    1: "jan", 2: "fev", 3: "mar", 4: "abr", 5: "mai", 6: "jun",
    7: "jul", 8: "ago", 9: "set", 10: "out", 11: "nov", 12: "dez",
}
_PT_MONTHS_FULL = {
    1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril",
    5: "maio", 6: "junho", 7: "julho", 8: "agosto",
    9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro",
}


def _fmt_date_short(iso: str) -> str:
    try:
        from datetime import date as _d
        dt = _d.fromisoformat(iso)
        return f"{dt.day:02d} {_PT_MONTHS[dt.month]}"
    except Exception:
        return iso


def _fmt_date_long(iso: str) -> str:
    try:
        from datetime import date as _d
        dt = _d.fromisoformat(iso)
        return f"{dt.day} de {_PT_MONTHS_FULL[dt.month]} de {dt.year}"
    except Exception:
        return iso


def _render_carrier_chips(carriers: list) -> str:
    from pcd.agents.smart_quote import airline_display
    if not carriers:
        return '<span class="program-empty">—</span>'
    chips = []
    for c in carriers:
        d = airline_display(c)
        chips.append(
            f'<span class="airline-chip" '
            f'style="border-left-color:{d["color"]};background:{d["bg"]}">'
            f'{d["name"]}</span>'
        )
    return f'<div class="airline-chip-row">{"".join(chips)}</div>'


def _is_calendar_uniform(prices: list, threshold: float = 0.03) -> bool:
    """True quando a variação relativa entre min e max é menor que `threshold`
    (default 3%). Indica que o Kayak retornou efetivamente o mesmo preço para
    todas as datas — comum quando há uma tarifa âncora barata disponível em
    todo o período."""
    if not prices:
        return False
    pmin, pmax = min(prices), max(prices)
    if pmin <= 0:
        return False
    return (pmax - pmin) / pmin < threshold


def _render_calendar_html(cal: dict, carriers_cal: dict, anchor_iso: str, requested_iso: str,
                          flex_days: int = 4, selected_iso: str = "") -> str:
    """Renderiza o gráfico de barras com a data selecionada destacada.
    O clique é tratado pelo bloco de botões logo abaixo do gráfico
    (não pelo HTML, que não permite onclick nativo no Streamlit)."""
    if not cal:
        return ""
    items = sorted(cal.items())
    prices = [v for _, v in items]
    pmin, pmax = min(prices), max(prices)
    span = max(1.0, pmax - pmin)
    H_MAX, H_MIN = 160, 20
    uniform = _is_calendar_uniform(prices)

    uniform_banner = ""
    if uniform:
        uniform_banner = (
            '<div style="background:#fff8e6;border:1px dashed #e59a00;color:#856404;'
            'border-radius:8px;padding:8px 14px;text-align:center;font-size:12px;'
            'font-weight:600;margin:0 0 12px 0">'
            'ℹ️ Preços similares no período — qualquer dia tem oferta parecida (dado real do Kayak)'
            '</div>'
        )

    cols_html = []
    for iso, price in items:
        if uniform:
            h = 110.0
        else:
            h = H_MIN + (price - pmin) / span * (H_MAX - H_MIN)
        # A "data ativa" (clicada/selecionada pelo vendedor) ganha destaque
        # adicional além das marcações de âncora/solicitada.
        is_selected = (iso == selected_iso)
        kind = "normal"
        badge_html = '<div class="cal-badge spacer">·</div>'
        if iso == anchor_iso:
            kind = "anchor"
            badge_html = '<div class="cal-badge">Melhor dia ✓</div>'
        elif iso == requested_iso:
            kind = "req"
            badge_html = '<div class="cal-badge req">Sua data</div>'

        col_extra = " selected" if is_selected else ""

        from pcd.agents.smart_quote import airline_display
        carriers_for_day = carriers_cal.get(iso) or []
        carriers_str = ", ".join(airline_display(c)["name"] for c in carriers_for_day) or "—"
        tooltip = f"R$ {price:,.2f} · {carriers_str}".replace(",", "X").replace(".", ",").replace("X", ".")

        cols_html.append(f"""
<div class="cal-col{col_extra}">
  <span class="cal-tooltip">{tooltip}</span>
  {badge_html}
  <div class="cal-bar-wrap"><div class="cal-bar {kind}" style="height:{h:.0f}px"></div></div>
  <div class="cal-date">{_fmt_date_short(iso)}</div>
  <div class="cal-price">R$ {price:,.0f}</div>
</div>""")

    return f"""
<div class="smart-card">
  <div class="smart-card-title">📅 Calendário de preços (±{flex_days} dias)</div>
  {uniform_banner}
  <div class="price-calendar">{''.join(cols_html)}</div>
  <div style="text-align:center;font-size:11px;color:#6b7a99;margin-top:8px;">
    💡 Clique em uma data abaixo para ver os voos disponíveis daquele dia
  </div>
</div>"""


def _render_metrics_cards_html(cal: dict, anchor_iso: str, requested_iso: str, savings: float, already_best: bool) -> str:
    anchor_price = cal.get(anchor_iso, 0)
    req_price = cal.get(requested_iso)

    if already_best:
        best_foot = "Sua data já é a melhor!"
    elif savings > 0:
        best_foot = f"Economia de R$ {savings:,.2f} vs sua data"
    else:
        best_foot = ""

    req_color = "red" if (req_price is not None and anchor_price is not None and req_price > anchor_price) else "green"
    req_html = ""
    if req_price is not None:
        req_html = f"""
<div class="metric-card req">
  <div class="metric-label">📅 Sua data solicitada</div>
  <div class="metric-date">{_fmt_date_long(requested_iso)}</div>
  <div class="metric-value {req_color}">R$ {req_price:,.2f}</div>
</div>"""
    else:
        req_html = f"""
<div class="metric-card req">
  <div class="metric-label">📅 Sua data solicitada</div>
  <div class="metric-date">{_fmt_date_long(requested_iso)}</div>
  <div class="metric-value red">Sem preço</div>
  <div class="metric-foot">Kayak não retornou para essa data.</div>
</div>"""

    if savings > 0:
        eco_html = f"""
<div class="metric-card eco">
  <div class="metric-label">💰 Economia potencial</div>
  <div class="metric-value green">R$ {savings:,.2f}</div>
  <div class="metric-foot">Mudando para a data âncora.</div>
</div>"""
    else:
        eco_html = """
<div class="metric-card eco">
  <div class="metric-label">💰 Economia potencial</div>
  <div class="metric-value blue">Nenhuma</div>
  <div class="metric-foot">Você escolheu bem!</div>
</div>"""

    best_html = f"""
<div class="metric-card best">
  <div class="metric-label">📅 Melhor dia</div>
  <div class="metric-date">{_fmt_date_long(anchor_iso)}</div>
  <div class="metric-value green">R$ {anchor_price:,.2f}</div>
  <div class="metric-foot">{best_foot}</div>
</div>"""

    return f'<div class="metric-grid">{best_html}{req_html}{eco_html}</div>'


_PROGRAM_VISUAL = {
    "SMILES":           {"emoji": "🟠", "css": "smiles", "label": "Smiles (GOL)"},
    "LATAM_PASS":       {"emoji": "💎", "css": "latam",  "label": "LATAM Pass"},
    "AZUL_FIDELIDADE":  {"emoji": "🔵", "css": "azul",   "label": "Azul Fidelidade"},
}


def _render_programs_cards_html(rp: dict) -> str:
    if not rp:
        return ""
    own_present = set(rp.get("own_carrier_present") or [])
    award_only = rp.get("award_only", {}) or {}

    cards = []
    for pkey in ("SMILES", "LATAM_PASS", "AZUL_FIDELIDADE"):
        covered = rp.get(pkey, []) or []
        vis = _PROGRAM_VISUAL[pkey]
        if not covered:
            cards.append(f"""
<div class="program-card disabled">
  <div class="program-head disabled">{vis['emoji']} {vis['label']}</div>
  <div class="program-body">
    <div class="program-empty">Não recomendado para essa rota</div>
  </div>
</div>""")
            continue

        chips_html = _render_carrier_chips(covered)
        badges = []
        if pkey in own_present:
            badges.append('<span class="prog-badge own">🏠 Programa próprio</span>')
        award_for_this = award_only.get(pkey) if isinstance(award_only, dict) else None
        if award_for_this:
            names = ", ".join(c for c in award_for_this)
            badges.append(f'<span class="prog-badge award">🎯 Tarifa Award disponível ({names})</span>')
        elif pkey != "AZUL_FIDELIDADE":
            # Smiles e LATAM Pass não têm award_partners explícito mas a cobertura serve
            badges.append('<span class="prog-badge award">🎯 Tarifa Award disponível</span>')
        badges_html = f'<div class="program-badges">{"".join(badges)}</div>' if badges else ""

        cards.append(f"""
<div class="program-card">
  <div class="program-head {vis['css']}">{vis['emoji']} {vis['label']}</div>
  <div class="program-body">
    <div class="program-section">Companhias cobertas na rota</div>
    {chips_html}
    {badges_html}
  </div>
</div>""")
    return f'<div class="program-grid">{"".join(cards)}</div>'


def _render_smart_header_html(flex_days: int = 4) -> str:
    return f"""
<div class="smart-header">
  <div class="smart-icon">🧠</div>
  <div>
    <div class="smart-title">Cotação Inteligente</div>
    <div class="smart-sub">Análise automática de ±{flex_days} dias · Mapeamento de programas · Melhor data identificada</div>
  </div>
</div>"""


_DATE_PICKER_CSS = """
<style>
.smart-date-pick{background:#fff;border:1px solid #dde3ef;border-radius:12px;
    padding:18px 22px;box-shadow:0 1px 3px rgba(13,43,110,.04);margin-bottom:8px;
    border-left:4px solid #c0392b;}
.smart-date-pick-title{font-size:14px;font-weight:700;color:#1a2236;margin-bottom:4px;}
.smart-date-pick-sub{font-size:12px;color:#6b7a99;margin-bottom:12px;}

/* Botão vermelho para "Buscar milhas para esta data" */
div[data-testid="stButton"] button[kind="primary"][data-smart="miles"]{
    background:#c0392b !important;border-color:#a8311e !important;
}

/* Painel "Melhor oferta na data selecionada" */
.best-offer-card{background:#fff!important;color:#1a2236!important;
    border:2px solid #1a7a4a;border-radius:12px;
    padding:18px 22px;margin-bottom:14px;box-shadow:0 2px 6px rgba(26,122,74,.10);}
.best-offer-card *{color:#1a2236;}
.bo-head{font-size:11px;font-weight:700;color:#1a7a4a!important;
    text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;}
.bo-title{font-size:18px;font-weight:800;margin-bottom:6px;}
.bo-flight{font-size:14px;color:#1a2236;margin-bottom:4px;}
.bo-times{font-size:13px;color:#6b7a99!important;margin-bottom:10px;}
.bo-price{font-size:26px;font-weight:800;color:#c0392b!important;margin-bottom:4px;}
.bo-markup{font-size:12px;color:#6b7a99!important;margin-bottom:12px;}
.bo-programs-head{font-size:12px;font-weight:700;color:#1a2236;margin:8px 0 6px;}
.bo-program{display:inline-block;font-size:12px;padding:5px 12px;border-radius:14px;
    margin-right:6px;margin-bottom:4px;background:#e8f0fb!important;color:#1a2236!important;
    border-left:3px solid #1a56a0;}
.bo-program.own{border-left-color:#1a7a4a;background:#eaf4ef!important;}
.bo-program.award{border-left-color:#e07b00;background:#fff8e6!important;}
.bo-no-program{font-size:12px;color:#6b7a99!important;font-style:italic;}

/* Lista compacta "Outras opções na data" */
.alt-list-card{background:#fff!important;color:#1a2236!important;
    border:1px solid #dde3ef;border-radius:12px;padding:14px 18px;margin-bottom:14px;}
.alt-list-head{font-size:13px;font-weight:700;color:#6b7a99!important;
    text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px;}
.alt-row{display:grid;grid-template-columns:1fr 130px;gap:14px;align-items:center;
    padding:10px 12px;border-radius:8px;border:1px solid #f0f2f8;margin-bottom:6px;
    transition:box-shadow .12s,background .12s;}
.alt-row:hover{background:#f5f7fb;box-shadow:0 2px 5px rgba(13,43,110,.06);}
.alt-row.is-best{border-color:#1a7a4a;background:#f0f9f4;}
.alt-line1{font-size:13px;font-weight:700;color:#1a2236;}
.alt-line2{font-size:12px;color:#6b7a99!important;margin-top:2px;}
.alt-progs{font-size:11px;color:#1a56a0!important;margin-top:3px;font-weight:600;}
.alt-price{text-align:right;font-size:18px;font-weight:800;color:#c0392b!important;}
.alt-price-foot{text-align:right;font-size:11px;color:#6b7a99!important;}
</style>
"""

_PROGRAM_NICE_NAME = {
    "SMILES":          "Smiles (GOL)",
    "LATAM_PASS":      "LATAM Pass",
    "AZUL_FIDELIDADE": "Azul Fidelidade",
}


def _render_program_chip(prog: dict) -> str:
    """Chip de programa para um voo específico (com flags own/award)."""
    label = _PROGRAM_NICE_NAME.get(prog["program"], prog.get("label", prog["program"]))
    suffix = ""
    css = ""
    if prog.get("own_carrier"):
        suffix = " · próprio"
        css = "own"
    elif prog.get("award_partner"):
        suffix = " · Award"
        css = "award"
    else:
        suffix = " · parceiro"
    return f'<span class="bo-program {css}">{label}{suffix}</span>'


def _fmt_clock_iso(s: str | None) -> str:
    if not s:
        return "—"
    try:
        from datetime import datetime as _dt
        s2 = s.replace("Z", "+00:00") if s.endswith("Z") else s
        return _dt.fromisoformat(s2).strftime("%H:%M")
    except Exception:
        return s[-8:-3] if isinstance(s, str) and len(s) >= 8 else s


def _fmt_duration_min(m: int | None) -> str:
    if not m:
        return "—"
    h, mm = divmod(int(m), 60)
    return f"{h}h{mm:02d}m" if mm else f"{h}h"


def _fmt_brl(v: float) -> str:
    return ("R$ " + f"{v:,.2f}").replace(",", "X").replace(".", ",").replace("X", ".")


def _render_best_offer_card(option, programs: list[dict], iso_date: str) -> str:
    """Card grande verde da melhor oferta Kayak da data selecionada.
    `option` é um FlightOptionLite; `programs` é a lista de programas que
    emitem a companhia daquela oferta especificamente."""
    from pcd.agents.smart_quote import airline_display
    main = option.main_carrier_iata or "—"
    disp = airline_display(main)
    flight_lbl = option.flight_number or main
    cias = option.carriers_names or []
    cias_str = " / ".join(cias) if cias else disp["name"]
    dep = _fmt_clock_iso(option.departure_time)
    arr = _fmt_clock_iso(option.arrival_time)
    dur = _fmt_duration_min(option.duration_min)
    stops = option.stops or 0
    stops_lbl = "Direto" if stops == 0 else (
        f"{stops} escala" if stops == 1 else f"{stops} escalas"
    )
    pretty_date = _fmt_date_long(iso_date)
    price = float(option.price_brl)
    markup = kayak_sell_price(price)
    markup_lbl = f"{int(round(kayak_markup_pct() * 100))}%"

    if programs:
        chips = "".join(_render_program_chip(p) for p in programs)
        progs_html = (
            f'<div class="bo-programs-head">💎 Programas que emitem essa companhia ({disp["name"]}):</div>'
            f'{chips}'
        )
    else:
        progs_html = (
            '<div class="bo-programs-head">💎 Programas que emitem essa companhia:</div>'
            f'<div class="bo-no-program">Nenhum programa brasileiro cadastrado emite {disp["name"]} '
            f'nesse trecho. Considere emissão em dinheiro.</div>'
        )

    return f"""
<div class="best-offer-card">
  <div class="bo-head">✈️ Melhor oferta na data selecionada — {pretty_date}</div>
  <div class="bo-title" style="color:{disp['color']}">✈️ {disp['name']}</div>
  <div class="bo-flight">{flight_lbl} · {cias_str}</div>
  <div class="bo-times">{dep} → {arr} · {stops_lbl} · {dur}</div>
  <div class="bo-price">💰 {_fmt_brl(price)} <span style="font-size:12px;color:#6b7a99;font-weight:500">(mercado Kayak)</span></div>
  <div class="bo-markup">📈 Preço de venda (com markup {markup_lbl}): <b>{_fmt_brl(markup)}</b></div>
  {progs_html}
</div>"""


def _render_alt_options_list(options_with_programs: list[dict], best_iso_index: int = 0) -> str:
    """Lista compacta das demais ofertas da data — radio nada (Streamlit fará
    via botões abaixo). O primeiro item recebe o highlight 'is-best'."""
    if not options_with_programs:
        return ""
    rows = []
    from pcd.agents.smart_quote import airline_display
    for idx, item in enumerate(options_with_programs):
        opt = item["option"]
        progs = item["programs"] or []
        main = opt.main_carrier_iata or "—"
        disp = airline_display(main)
        flight_lbl = opt.flight_number or main
        dep = _fmt_clock_iso(opt.departure_time)
        arr = _fmt_clock_iso(opt.arrival_time)
        dur = _fmt_duration_min(opt.duration_min)
        stops = opt.stops or 0
        stops_lbl = "Direto" if stops == 0 else (
            f"{stops} escala" if stops == 1 else f"{stops} escalas"
        )
        progs_str = ", ".join(str(_PROGRAM_NICE_NAME.get(p["program"], p["program"])) for p in progs) or "—"
        is_best_cls = " is-best" if idx == best_iso_index else ""
        rows.append(f"""
<div class="alt-row{is_best_cls}">
  <div>
    <div class="alt-line1" style="color:{disp['color']}">✈️ {disp['name']} · {flight_lbl}</div>
    <div class="alt-line2">{dep} → {arr} · {stops_lbl} · {dur}</div>
    <div class="alt-progs">Programas: {progs_str}</div>
  </div>
  <div>
    <div class="alt-price">{_fmt_brl(float(opt.price_brl))}</div>
    <div class="alt-price-foot">mercado · venda {_fmt_brl(kayak_sell_price(float(opt.price_brl)))}</div>
  </div>
</div>""")
    return (
        '<div class="alt-list-card">'
        '<div class="alt-list-head">📋 Outras opções na data (Kayak)</div>'
        + "".join(rows) +
        '</div>'
    )


def _render_smart_quote_section():
    """Renderiza Etapa 1 (calendário, métricas, programas) + seletor de data
    e botão da Etapa 2. Retorna a data ISO escolhida + dict de programas
    relevantes quando o vendedor clica em 'Buscar milhas para esta data',
    senão None."""
    smart_result = st.session_state.get("smart_result")
    smart_progress_msgs = st.session_state.get("smart_progress") or []

    if smart_result is None and not smart_progress_msgs:
        return None

    # Guarda anti-staleness: se o objeto cacheado vem de uma versão antiga da
    # dataclass (deploy anterior, antes dos campos daily_offers / best_offer_per_date),
    # invalida silenciosamente — a UI ficará só com a barra de progresso até a
    # próxima busca.
    if smart_result is not None and not hasattr(smart_result, "best_offer_per_date"):
        st.session_state.pop("smart_result", None)
        smart_result = None
        st.warning(
            "Cotação Inteligente desatualizada por mudança de versão — refaça a busca."
        )

    flex_days_used = getattr(smart_result, "flex_days_used", 4) if smart_result else int(st.session_state.get("smart_flex_days", 4))

    st.markdown(_SMART_CSS, unsafe_allow_html=True)
    st.markdown(_DATE_PICKER_CSS, unsafe_allow_html=True)
    st.markdown(_render_smart_header_html(flex_days_used), unsafe_allow_html=True)

    if smart_progress_msgs:
        with st.status("Agentes executados", expanded=False, state="complete"):
            for msg in smart_progress_msgs:
                st.write(msg)

    if smart_result is None:
        st.warning("A Cotação Inteligente não pôde ser concluída. Tente novamente ou desligue o toggle 🧠 e refaça a busca.")
        return None

    _notes = getattr(smart_result, "notes", None) or []
    for note in _notes:
        st.info(note)

    cal = getattr(smart_result, "price_calendar", None) or {}
    anchor_iso = getattr(smart_result, "anchor_date", "") or ""
    requested_iso = getattr(smart_result, "date_requested", "") or ""
    carriers_cal = getattr(smart_result, "calendar_carriers", None) or {}
    _savings = float(getattr(smart_result, "savings_vs_requested", 0.0) or 0.0)
    _already_best = bool(getattr(smart_result, "date_is_already_best", False))
    total_dates = flex_days_used * 2 + 1

    # A "data ativa" — fonte da verdade compartilhada entre gráfico e dropdown.
    # Inicializa com a melhor data (mais barata), respeitando qualquer escolha
    # já feita pelo vendedor em runs anteriores.
    options = sorted(cal.keys())
    if not options:
        # sem dados → não há nada para selecionar
        selected_iso = ""
    else:
        prev_sel = st.session_state.get("smart_selected_date")
        if prev_sel in options:
            selected_iso = prev_sel
        elif anchor_iso in options:
            selected_iso = anchor_iso
        else:
            selected_iso = options[0]
        st.session_state["smart_selected_date"] = selected_iso

    # ── Calendário de preços ──
    if cal:
        st.markdown(
            _render_calendar_html(
                cal, carriers_cal, anchor_iso, requested_iso, flex_days_used,
                selected_iso=selected_iso or "",
            ),
            unsafe_allow_html=True,
        )

        # Linha de botões clicáveis — uma coluna por data, cada coluna abriga
        # um botão com a data abreviada que dispara a seleção daquela data.
        # Streamlit não permite onclick em HTML, então usamos botões reais.
        _date_buttons_cols = st.columns(len(options))
        for _idx, _iso in enumerate(options):
            with _date_buttons_cols[_idx]:
                _label = _fmt_date_short(_iso)
                _is_sel = _iso == selected_iso
                _btn_type = "primary" if _is_sel else "secondary"
                if st.button(_label, key=f"smart_pick_{_iso}", type=_btn_type, use_container_width=True):
                    if st.session_state.get("smart_selected_date") != _iso:
                        st.session_state["smart_selected_date"] = _iso
                        # Invalida a Cotação Completa anterior — ela pertencia
                        # à data antiga. Vendedor precisa clicar de novo em
                        # "Buscar milhas para esta data" para a nova data.
                        st.session_state.pop("pipeline_result", None)
                        st.session_state.pop("economilhas_partial", None)
                        st.session_state["smart_stale_quote"] = True
                        st.rerun()

        # Cards de métricas
        st.markdown(
            _render_metrics_cards_html(
                cal, anchor_iso, requested_iso,
                _savings,
                _already_best,
            ),
            unsafe_allow_html=True,
        )

    # ── Mensagem chave ──
    _cal_uniform = _is_calendar_uniform(list(cal.values())) if cal else False
    if _cal_uniform:
        st.markdown(
            f'<div class="smart-message green">✅ <b>Preços estáveis no período.</b> '
            f'A tarifa mais barata é praticamente a mesma nos {total_dates} dias — você pode '
            f'manter {_fmt_date_long(requested_iso)} sem perda financeira.</div>',
            unsafe_allow_html=True,
        )
    elif _already_best:
        st.markdown(
            f'<div class="smart-message green">✅ <b>Ótima escolha!</b> A data solicitada '
            f'({_fmt_date_long(requested_iso)}) já é a mais barata do período analisado.</div>',
            unsafe_allow_html=True,
        )
    elif anchor_iso and _savings > 0:
        st.markdown(
            f'<div class="smart-message green">✅ <b>Melhor dia: {_fmt_date_long(anchor_iso)}</b> — '
            f'R$ {_savings:,.2f} mais barato que '
            f'{_fmt_date_long(requested_iso)} solicitado.</div>',
            unsafe_allow_html=True,
        )

    # ── Painel "Melhor oferta na data selecionada" + lista de alternativas ──
    # Substitui o antigo bloco genérico "Programas relevantes para essa rota":
    # mostra somente os programas que cobrem a companhia do voo mais barato
    # da data ativa, e expõe top-5 alternativas com seus próprios programas.
    if cal and selected_iso:
        _best_map = getattr(smart_result, "best_offer_per_date", None) or {}
        _daily_map = getattr(smart_result, "daily_offers", None) or {}
        best_offer = _best_map.get(selected_iso)
        if _daily_map and hasattr(smart_result, "get_full_options_for_date"):
            try:
                full_options = smart_result.get_full_options_for_date(selected_iso)
            except Exception:
                full_options = []
        else:
            full_options = []
        if best_offer is not None:
            from pcd.agents.smart_quote import SmartQuoteAgent
            programs_for_best = SmartQuoteAgent.map_programs_for_carrier(best_offer.main_carrier_iata)
            st.markdown(
                _render_best_offer_card(best_offer, programs_for_best, selected_iso),
                unsafe_allow_html=True,
            )

            # Outras opções: top 5 (excluindo a já destacada como melhor)
            alt_opts = full_options[1:6]  # do 2º até o 6º — 5 alternativas
            if alt_opts:
                with st.expander(f"📋 Outras opções na data {_fmt_date_short(selected_iso)} (Kayak)", expanded=False):
                    st.markdown(
                        _render_alt_options_list(alt_opts, best_iso_index=-1),
                        unsafe_allow_html=True,
                    )

    # ── Etapa 2 — Seletor de data + botão de cotação completa ──
    if not cal:
        return None

    # Default do dropdown vem do smart_selected_date (sincronizado com o gráfico).
    default_iso = selected_iso or anchor_iso or options[0]

    def _fmt_option(iso: str) -> str:
        pretty = _fmt_date_short(iso)
        price = cal.get(iso, 0.0)
        badge = ""
        if iso == anchor_iso:
            badge = "  ✓ Melhor dia"
        elif iso == requested_iso:
            badge = "  📌 Sua data original"
        return f"{pretty} · R$ {price:,.0f}{badge}"

    st.markdown(
        '<div class="smart-date-pick">'
        '<div class="smart-date-pick-title">📌 Escolha a data para a cotação completa</div>'
        '<div class="smart-date-pick-sub">A busca de milhas (BuscaMilhas) e a quebra de '
        'trecho só disparam após sua escolha.</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # O dropdown e o gráfico são duas vias equivalentes — qualquer mudança
    # aqui propaga para `smart_selected_date` (fonte da verdade).
    # Para evitar conflito Streamlit (key existente com 'index' explícito),
    # populamos o estado antes de renderizar.
    if st.session_state.get("smart_date_picker") not in options:
        st.session_state["smart_date_picker"] = default_iso

    def _on_dropdown_change():
        _new = st.session_state.get("smart_date_picker")
        if _new and _new != st.session_state.get("smart_selected_date"):
            st.session_state["smart_selected_date"] = _new
            # Invalida a Cotação Completa anterior — pertencia à data antiga.
            st.session_state.pop("pipeline_result", None)
            st.session_state.pop("economilhas_partial", None)
            st.session_state["smart_stale_quote"] = True

    chosen_iso = st.selectbox(
        "Data da cotação completa",
        options=options,
        format_func=_fmt_option,
        key="smart_date_picker",
        label_visibility="collapsed",
        on_change=_on_dropdown_change,
    )
    # Garante que selected_date reflete a data atual do dropdown
    if chosen_iso and chosen_iso != st.session_state.get("smart_selected_date"):
        st.session_state["smart_selected_date"] = chosen_iso
        st.session_state.pop("pipeline_result", None)
        st.session_state.pop("economilhas_partial", None)
        st.session_state["smart_stale_quote"] = True

    # Aviso visual quando a Cotação Completa atual não corresponde mais à
    # data selecionada (vendedor mudou de data depois de já ter cotado).
    if st.session_state.get("smart_stale_quote") and "pipeline_result" not in st.session_state:
        _stale_iso = chosen_iso or selected_iso or ""
        st.warning(
            "↻ Dados desatualizados — clique em **Buscar milhas para esta data** "
            f"para gerar a Cotação Completa de {_fmt_date_long(_stale_iso)}."
        )

    col_b1, col_b2 = st.columns(2, gap="small")
    with col_b1:
        miles_clicked = st.button(
            "💎 Buscar milhas para esta data",
            type="primary",
            key="smart_quote_btn",
            use_container_width=True,
        )
    with col_b2:
        split_clicked = st.button(
            "✂️ Quebrar trecho nesta data",
            key="split_btn",
            use_container_width=True,
        )

    # Hub da quebra — vendedor pode escolher manualmente
    _hub_options = {
        "GRU - São Paulo (padrão recomendado)": "GRU",
        "GIG - Rio de Janeiro": "GIG",
        "CNF - Belo Horizonte": "CNF",
        "FOR - Fortaleza": "FOR",
        "REC - Recife": "REC",
        "BSB - Brasília": "BSB",
        "Outro... (IATA personalizado)": "__custom__",
    }
    _hub_keys = list(_hub_options.keys())
    _current_hub = st.session_state.get("split_hub", "GRU")
    _default_label = next(
        (k for k, v in _hub_options.items() if v == _current_hub), _hub_keys[0]
    )
    if _current_hub not in _hub_options.values():
        _default_label = "Outro... (IATA personalizado)"

    _hub_label = st.selectbox(
        "Hub de conexão",
        options=_hub_keys,
        index=_hub_keys.index(_default_label),
        key="split_hub_select",
        help="Aeroporto onde a viagem será quebrada em duas pernas.",
    )
    _hub_chosen = _hub_options[_hub_label]
    if _hub_chosen == "__custom__":
        _hub_custom = st.text_input(
            "Hub personalizado (IATA 3 letras)",
            value=_current_hub if _current_hub not in {"GRU","GIG","CNF","FOR","REC","BSB"} else "",
            max_chars=3, key="split_hub_custom",
        )
        _hub_chosen = (_hub_custom or "GRU").upper().strip()

    if not (len(_hub_chosen) == 3 and _hub_chosen.isalpha()):
        st.warning("Hub inválido — usando GRU.")
        _hub_chosen = "GRU"

    # Invalida cache da quebra anterior se o hub mudou
    if st.session_state.get("split_hub") != _hub_chosen:
        st.session_state["split_hub"] = _hub_chosen
        # Limpa cache para forçar nova busca com o novo hub
        for _k in ("split_result", "split_data_key", "split_active",
                   "split_fits", "split_fit_cache_keys", "split_fitted_combinations"):
            st.session_state.pop(_k, None)

    with_baggage_chk = st.checkbox(
        "Considerar bagagem despachada (eleva a conexão mínima de 2h30m para 4h)",
        value=st.session_state.get("split_with_baggage", False),
        key="split_with_baggage",
        help="Com bagagem despachada é preciso retirar e despachar de novo no hub.",
    )

    return {
        "chosen_iso": chosen_iso,
        "miles_clicked": bool(miles_clicked and chosen_iso),
        "split_clicked": bool(split_clicked and chosen_iso),
        "with_baggage": bool(with_baggage_chk),
        "hub": _hub_chosen,
    }


# ═══════════════════════════════════════════════════════════════
# Quebra de Trecho — render (Fase 1 simplificada: hub fixo GRU)
# ═══════════════════════════════════════════════════════════════
_SPLIT_CSS = """
<style>
.split-header{background:linear-gradient(135deg,#c0392b 0%,#a93226 100%);
    border-radius:14px;padding:18px 24px;display:flex;align-items:center;gap:18px;
    margin:14px 0 18px 0;box-shadow:0 2px 8px rgba(192,57,43,.18);}
.split-icon{font-size:42px;line-height:1;}
.split-title{color:#fff;font-size:22px;font-weight:700;letter-spacing:.2px;}
.split-sub{color:rgba(255,255,255,.82);font-size:12px;margin-top:3px;}

.split-direct{background:#fff!important;color:#1a2236!important;border:1px solid #dde3ef;border-radius:12px;
    padding:14px 20px;margin-bottom:14px;display:flex;align-items:center;gap:14px;
    box-shadow:0 1px 3px rgba(13,43,110,.04);}
.split-direct *{color:#1a2236;}
.split-direct .label{color:#6b7a99!important;}
.split-direct .foot{color:#6b7a99!important;}
.split-direct .icon{font-size:24px;}
.split-direct .label{font-size:11px;font-weight:700;color:#6b7a99;
    text-transform:uppercase;letter-spacing:.06em;}
.split-direct .price{font-size:22px;font-weight:800;color:#1a2236;line-height:1.1;}
.split-direct .foot{font-size:11px;color:#6b7a99;margin-top:2px;}

.split-leg-title{font-size:15px;font-weight:800;color:#1a2236;margin:18px 0 10px 0;
    padding:10px 14px;border-left:4px solid #c0392b;background:#fdf6f5;
    border-radius:6px;letter-spacing:.2px;}

.split-offer{background:#fff;border:1px solid #dde3ef;border-radius:12px;
    padding:14px 18px;margin-bottom:0px;
    display:grid;grid-template-columns:1fr 130px;gap:16px;align-items:center;
    box-shadow:0 1px 3px rgba(13,43,110,.04);transition:box-shadow .12s ease;}
.split-offer:hover{box-shadow:0 4px 12px rgba(13,43,110,.10);}
.split-offer .head{display:flex;flex-wrap:wrap;align-items:center;gap:8px;
    margin-bottom:6px;}
.split-offer .chip{font-size:11px;font-weight:700;padding:3px 10px;border-radius:11px;
    border-left:3px solid #1a56a0;background:#e8f0fb;color:#1a2236;}
.split-offer .route{font-family:Menlo,monospace;font-weight:700;font-size:14px;
    color:#1a2236;}
.split-offer .times{font-size:14px;font-weight:600;color:#1a2236;margin-top:2px;}
.split-offer .meta{font-size:12px;color:#6b7a99;margin-top:3px;}
.split-offer .price{text-align:right;font-size:20px;font-weight:800;color:#1a2236;}
.split-offer .price-foot{text-align:right;font-size:11px;color:#6b7a99;margin-top:2px;}
.split-empty{padding:14px 18px;background:#fdf0f2;border:1px solid #f0c4c8;
    border-radius:10px;color:#7a2418;font-size:13px;font-weight:600;
    margin-bottom:10px;}
.split-na{padding:18px 22px;background:#f0f6fc;border:1px solid #c8dcf0;
    border-radius:12px;color:#1a4a7a;}
.split-na .title{font-size:15px;font-weight:800;margin-bottom:6px;}
.split-na .body{font-size:13px;line-height:1.5;}

/* Encaixe doméstico (fase 2) */
.split-fit-block{background:#f7faff;border:1px solid #c8dcf0;border-radius:10px;
    padding:14px 16px;margin:0 0 14px 18px;}
.split-fit-block .head{font-size:13px;font-weight:800;color:#1a4a7a;
    display:flex;align-items:center;gap:8px;margin-bottom:4px;}
.split-fit-block .meta{font-size:11px;color:#1a4a7a;margin-bottom:10px;
    line-height:1.55;}
.split-fit-offer{background:#fff;border:1px solid #d6def0;border-radius:10px;
    padding:11px 14px;margin-bottom:8px;
    display:grid;grid-template-columns:1fr 105px;gap:12px;align-items:center;
    transition:box-shadow .12s,border-color .12s;}
.split-fit-offer:hover{box-shadow:0 3px 9px rgba(13,43,110,.10);}
.split-fit-offer.selected{border-color:#1a7a4a;background:#eaf4ef;
    box-shadow:0 0 0 3px rgba(26,122,74,.18);}
.split-fit-offer.dim{opacity:.55;background:#f5f6f9;}
.split-fit-offer .head{display:flex;flex-wrap:wrap;align-items:center;gap:6px;
    margin-bottom:4px;}
.split-fit-offer .chip{font-size:10px;font-weight:700;padding:2px 8px;border-radius:9px;
    border-left:3px solid #1a56a0;background:#e8f0fb;color:#1a2236;}
.split-fit-offer .route{font-family:Menlo,monospace;font-weight:700;font-size:13px;
    color:#1a2236;}
.split-fit-offer .times{font-size:13px;font-weight:600;color:#1a2236;margin-top:1px;}
.split-fit-offer .meta{font-size:11px;color:#6b7a99;margin-top:2px;}
.split-fit-offer .price{text-align:right;font-size:17px;font-weight:800;color:#1a2236;}
.split-fit-offer .price-foot{text-align:right;font-size:10px;color:#6b7a99;margin-top:1px;}
.split-fit-offer .layover{display:inline-block;font-size:11px;font-weight:700;
    padding:2px 9px;border-radius:9px;margin-top:5px;}
.split-fit-offer .layover.good{background:#eaf4ef;color:#1a7a4a;
    border:1px solid #b8ddc8;}
.split-fit-offer .layover.warn{background:#fff5e0;color:#e07b00;
    border:1px solid #f0d68a;}
.split-fit-offer .layover.bad{background:#fdf0f2;color:#7a2418;
    border:1px solid #f0c4c8;text-decoration:line-through;}
.split-fit-offer .layover.long{background:#ecedf3;color:#6b7a99;
    border:1px solid #d6dae5;}
.split-no-fit{padding:11px 14px;background:#fff7e0;border:1px solid #f0d68a;
    border-radius:8px;color:#92660a;font-size:12px;margin-bottom:10px;}

/* Resumo de combinações selecionadas */
.split-combo-block{background:#fff;border:2px solid #1a7a4a;border-radius:14px;
    padding:18px 22px;margin:24px 0 14px 0;
    box-shadow:0 2px 10px rgba(26,122,74,.15);}
.split-combo-block .title{font-size:18px;font-weight:800;color:#1a7a4a;
    display:flex;align-items:center;gap:10px;margin-bottom:14px;
    padding-bottom:10px;border-bottom:1px dashed #b8ddc8;}
.split-combo-card{background:#f7fbf9;border:1px solid #d8eadf;border-radius:10px;
    padding:14px 18px;margin-bottom:12px;}
.split-combo-card .ckhead{font-weight:800;color:#1a7a4a;font-size:13px;
    margin-bottom:8px;}
.split-combo-card .leg-line{font-size:13px;color:#1a2236;margin:4px 0;
    display:flex;justify-content:space-between;gap:14px;}
.split-combo-card .leg-line .price{font-weight:700;}
.split-combo-card .conn{font-size:12px;color:#1a7a4a;margin:6px 0;font-weight:700;}
.split-combo-card .conn.warn{color:#e07b00;}
.split-combo-card .total{font-size:16px;font-weight:800;color:#1a2236;
    margin-top:10px;padding-top:10px;border-top:1px solid #d8eadf;
    display:flex;justify-content:space-between;align-items:center;}
.split-combo-card .savings{font-size:13px;font-weight:700;color:#1a7a4a;}
.split-combo-card .nosavings{font-size:13px;color:#92660a;}

/* Cotação em milhas (fase 3) */
.miles-match-block{background:#fdfbf6;border:2px solid #1a56a0;border-radius:14px;
    padding:18px 22px;margin:8px 0 18px 0;box-shadow:0 2px 10px rgba(26,86,160,.12);}
.miles-match-block .mtitle{font-size:16px;font-weight:800;color:#1a56a0;
    display:flex;align-items:center;gap:10px;margin-bottom:8px;
    padding-bottom:8px;border-bottom:1px dashed #c8dcf0;}
.miles-match-leg{margin:14px 0;}
.miles-match-leg .lhead{font-size:14px;font-weight:800;color:#1a2236;
    margin-bottom:4px;}
.miles-match-leg .lprog{font-size:11px;color:#6b7a99;margin-bottom:10px;
    font-style:italic;}
.miles-option{background:#fff;border:1px solid #dde3ef;border-radius:10px;
    padding:11px 14px;margin-bottom:8px;
    display:grid;grid-template-columns:1fr 130px;gap:14px;align-items:center;}
.miles-option.exact{border-color:#1a7a4a;background:#f7fbf9;
    box-shadow:0 0 0 2px rgba(26,122,74,.18);}
.miles-option.dim{opacity:.78;}
.miles-option .head{display:flex;flex-wrap:wrap;align-items:center;gap:6px;
    font-size:13px;font-weight:700;color:#1a2236;margin-bottom:2px;}
.miles-option .badge-exact{background:#1a7a4a;color:#fff;font-size:10px;
    font-weight:800;padding:2px 7px;border-radius:8px;letter-spacing:.4px;}
.miles-option .badge-prog{background:#e8f0fb;color:#1a2236;font-size:10px;
    font-weight:700;padding:2px 7px;border-radius:8px;
    border-left:3px solid #1a56a0;}
.miles-option .meta{font-size:12px;color:#6b7a99;margin-top:2px;}
.miles-option .miles-line{font-size:13px;color:#1a2236;margin-top:4px;}
.miles-option .miles-line strong{color:#1a4a7a;}
.miles-option .compare{font-size:12px;margin-top:3px;font-weight:700;}
.miles-option .compare.savings{color:#1a7a4a;}
.miles-option .compare.warn{color:#e07b00;}
.miles-option .right{text-align:right;}
.miles-option .right .real{font-size:18px;font-weight:800;color:#1a2236;}
.miles-option .right .label{font-size:10px;color:#6b7a99;
    text-transform:uppercase;letter-spacing:.05em;}
.miles-empty{padding:11px 14px;background:#fdf0f2;border:1px solid #f0c4c8;
    border-radius:8px;color:#7a2418;font-size:12px;font-weight:600;
    margin-bottom:8px;}
.miles-info-note{padding:11px 14px;background:#fff7e0;border:1px solid #f0d68a;
    border-radius:8px;color:#92660a;font-size:12px;margin-bottom:8px;}
.miles-secondary-title{font-size:12px;font-weight:700;color:#6b7a99;
    margin:8px 0 6px 0;}
.miles-summary{background:#f7fbf9;border:1px solid #d8eadf;border-radius:10px;
    padding:14px 18px;margin-top:14px;}
.miles-summary .stitle{font-size:14px;font-weight:800;color:#1a4a7a;
    margin-bottom:10px;display:flex;align-items:center;gap:8px;}
.miles-summary .row{display:flex;justify-content:space-between;
    align-items:flex-start;font-size:13px;margin:5px 0;color:#1a2236;}
.miles-summary .row .v{font-weight:700;font-family:Menlo,monospace;}
.miles-summary .total-row{font-size:15px;font-weight:800;color:#1a2236;
    margin-top:10px;padding-top:10px;border-top:1px solid #d8eadf;
    display:flex;justify-content:space-between;align-items:center;}
.miles-summary .savings-line{font-size:13px;font-weight:700;margin-top:6px;}
.miles-summary .savings-line.good{color:#1a7a4a;}
.miles-summary .savings-line.bad{color:#92660a;}
</style>
"""


def _fmt_hm(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    if h <= 0:
        return f"{m}m"
    if m == 0:
        return f"{h}h"
    return f"{h}h{m:02d}m"


def _fmt_clock(dt) -> str:
    if dt is None:
        return "—"
    try:
        return dt.strftime("%H:%M")
    except Exception:
        return "—"


def _fmt_arrival_with_offset(dep_dt, arr_dt) -> str:
    """Formata HH:MM e adiciona '(+1)' se o voo aterrissa em outro dia."""
    if dep_dt is None or arr_dt is None:
        return _fmt_clock(arr_dt)
    base = _fmt_clock(arr_dt)
    try:
        diff = (arr_dt.date() - dep_dt.date()).days
        if diff > 0:
            return f"{base} (+{diff})"
    except Exception:
        pass
    return base


# Extensão local de AIRLINE_DISPLAY (smart_quote.py é proibido pelo spec).
# Cobre companhias internacionais que aparecem em rotas atendidas pelo
# Kayak mas ainda não estão na tabela central — evita exibir só a sigla
# IATA crua (ex: "AT" virava texto bruto sem nome).
_AIRLINE_DISPLAY_EXTRA: dict[str, dict[str, str]] = {
    "AT": {"name": "Air Tahiti Nui",        "color": "#003D7C", "bg": "#f0f4fa"},
    "AV": {"name": "Avianca",               "color": "#E2231A", "bg": "#fdf0f2"},
    "AM": {"name": "Aeroméxico",            "color": "#0033A0", "bg": "#f0f3ff"},
    "AR": {"name": "Aerolíneas Argentinas", "color": "#0093D0", "bg": "#f0f9ff"},
    "EI": {"name": "Aer Lingus",            "color": "#00824A", "bg": "#f0faf6"},
    "AY": {"name": "Finnair",               "color": "#1B355E", "bg": "#f0f3fa"},
    "SQ": {"name": "Singapore Airlines",    "color": "#003876", "bg": "#f0f3fa"},
    "QF": {"name": "Qantas",                "color": "#E40000", "bg": "#fdf0f0"},
    "G3": {"name": "GOL",                   "color": "#FF5B00", "bg": "#fff4f0"},
    "AD": {"name": "Azul",                  "color": "#0032A0", "bg": "#f0f3ff"},
    "LA": {"name": "LATAM Airlines",        "color": "#E31837", "bg": "#fdf0f2"},
}


def _airline_display_ext(iata: str) -> dict[str, str]:
    """`airline_display` com fallback para a extensão local.

    Lookup ordenado:
      1) tabela central em pcd.agents.smart_quote.AIRLINE_DISPLAY
      2) tabela local _AIRLINE_DISPLAY_EXTRA acima
      3) fallback genérico (cor azul PcD, nome = IATA cru)
    """
    code = (iata or "").upper().strip()
    if not code:
        return {"name": "—", "color": "#1a56a0", "bg": "#e8f0fb"}
    try:
        from pcd.agents.smart_quote import AIRLINE_DISPLAY as _CENTRAL
        if code in _CENTRAL:
            return _CENTRAL[code]
    except Exception:
        pass
    if code in _AIRLINE_DISPLAY_EXTRA:
        return _AIRLINE_DISPLAY_EXTRA[code]
    return {"name": code, "color": "#1a56a0", "bg": "#e8f0fb"}


def _airline_full_name(iata: str) -> str:
    """Retorna o nome completo da companhia (ex: 'Air Tahiti Nui') a partir
    do IATA. Cai para o próprio código se a companhia não está cadastrada."""
    return _airline_display_ext(iata).get("name", iata)


def _airline_names_pretty(iata_codes: list[str], names_fallback: list[str]) -> str:
    """Lista limpa de nomes para exibição em cards/resumos. Ex:
       ['LA', 'AT'] → 'LATAM Airlines, Air Tahiti Nui'"""
    if iata_codes:
        return ", ".join(_airline_full_name(c) for c in iata_codes)
    return ", ".join(names_fallback[:2]) if names_fallback else "—"


def _split_offer_id(off) -> str:
    """Versão local de `offer_id` para evitar dependência do módulo do agente
    em runtime — Streamlit mantém imports em sys.modules entre rerodadas e
    pode servir uma versão antiga se o agente for editado com o app rodando.
    Manter o id estável aqui também garante consistência entre chaves de
    session_state ao longo das rerodadas, mesmo quando o agente é recarregado.
    """
    raw = getattr(off, "raw", None) or {}
    leg = raw.get("leg_id") or raw.get("out_leg_id")
    if isinstance(leg, str) and leg:
        return f"{off.origin}-{off.destination}-{leg}"
    dep = off.departure_dt.isoformat() if getattr(off, "departure_dt", None) else "no-dep"
    iata = list(getattr(off, "airlines_iata", []) or [])
    names = list(getattr(off, "airlines", []) or [])
    cias = ",".join(iata) if iata else (",".join(names[:2]) if names else "")
    return f"{off.origin}-{off.destination}-{dep}-{off.price_brl:.0f}-{cias}"


def _split_airline_chips(iata_codes: list[str], airline_names: list[str]) -> str:
    """Renderiza chips coloridos com NOME COMPLETO da companhia
    (ex: 'Air Tahiti Nui' em vez de 'AT'). Usa a tabela estendida
    `_airline_display_ext` para cobrir companhias que ainda não estão
    cadastradas em pcd.agents.smart_quote.AIRLINE_DISPLAY."""
    if iata_codes:
        chips = []
        for code in iata_codes:
            d = _airline_display_ext(code)
            chips.append(
                f'<span class="chip" style="border-left-color:{d["color"]};'
                f'background:{d["bg"]}">{d["name"]}</span>'
            )
        return "".join(chips)
    if airline_names:
        return "".join(f'<span class="chip">{n}</span>' for n in airline_names[:3])
    return ""


def _render_offer_card(offer) -> str:
    """Monta um card HTML para uma oferta (KayakOffer)."""
    chips_html = _split_airline_chips(offer.airlines_iata, offer.airlines)
    dep = _fmt_clock(offer.departure_dt)
    arr = _fmt_arrival_with_offset(offer.departure_dt, offer.arrival_dt)
    dur = _fmt_hm(offer.duration_min) if offer.duration_min else "—"
    stops_label = "Direto" if offer.stops == 0 else (
        f"{offer.stops} escala" if offer.stops == 1 else f"{offer.stops} escalas"
    )
    return f"""
<div class="split-offer">
  <div>
    <div class="head">{chips_html}</div>
    <div class="route">{offer.origin} → {offer.destination}</div>
    <div class="times">{dep} → {arr}</div>
    <div class="meta">{dur} · {stops_label}</div>
  </div>
  <div>
    <div class="price">R$ {offer.price_brl:,.2f}</div>
    <div class="price-foot">por adulto</div>
  </div>
</div>"""


# ── Encaixe (fase 2) ───────────────────────────────────────────
def _layover_kind(layover_min: int, with_baggage: bool) -> tuple[str, str]:
    """Retorna (classe_css, rótulo_curto) para um valor de layover."""
    min_conn = 240 if with_baggage else 150
    max_conn = 720
    if layover_min < min_conn:
        return ("bad", "✗ muito curta")
    if layover_min < min_conn + 30:
        return ("warn", "⚠ no limite")
    if layover_min > max_conn:
        return ("long", "ⓘ pernoite")
    if layover_min > min_conn + 360:
        return ("warn", "⚠ longa")
    return ("good", "✓")


def _render_fit_offer_card(dom, with_baggage: bool, *, dim: bool = False, selected: bool = False) -> str:
    chips_html = _split_airline_chips(dom.airlines_iata, dom.airlines)
    dep = _fmt_clock(dom.departure_dt)
    arr = _fmt_arrival_with_offset(dom.departure_dt, dom.arrival_dt)
    dur = _fmt_hm(dom.duration_min) if dom.duration_min else "—"
    stops_label = "Direto" if dom.stops == 0 else (
        f"{dom.stops} escala" if dom.stops == 1 else f"{dom.stops} escalas"
    )
    cls, label = _layover_kind(int(dom.layover_minutes or 0), with_baggage)
    _hub_lbl = st.session_state.get("split_hub", "GRU")
    layover_html = (
        f'<span class="layover {cls}">⏱️ Conexão em {_hub_lbl}: '
        f'{_fmt_hm(int(dom.layover_minutes or 0))} {label}</span>'
    )
    css_extra = ""
    if selected:
        css_extra += " selected"
    if dim:
        css_extra += " dim"
    return f"""
<div class="split-fit-offer{css_extra}">
  <div>
    <div class="head">{chips_html}</div>
    <div class="route">{dom.origin} → {dom.destination}</div>
    <div class="times">{dep} → {arr}</div>
    <div class="meta">{dur} · {stops_label}</div>
    {layover_html}
  </div>
  <div>
    <div class="price">R$ {dom.price_brl:,.2f}</div>
    <div class="price-foot">por adulto</div>
  </div>
</div>"""


def _fmt_dt_dmy(d) -> str:
    """ISO yyyy-mm-dd → 'dd/mm/yyyy'."""
    if not d:
        return "—"
    try:
        from datetime import date as _d
        return _d.fromisoformat(d).strftime("%d/%m/%Y")
    except Exception:
        return str(d)


_OFFSET_LABEL = {
    "same_day": "no mesmo dia",
    "day_before": "no dia anterior",
    "day_after": "no dia seguinte",
}


def _trigger_fit(off, *, intl_direction: str, other_endpoint: str,
                 adults: int, with_baggage: bool):
    """Roda fit_domestic_leg() e armazena no session_state, com cache por
    (offer_id, other_endpoint, with_baggage). Idempotente — chamadas
    repetidas com mesma chave reusam o resultado."""
    from pcd.agents.segment_split import SegmentSplitAgent

    oid = _split_offer_id(off)
    cache_key = f"fit_{oid}_{other_endpoint}_{with_baggage}"
    fits = st.session_state.setdefault("split_fits", {})
    keys = st.session_state.setdefault("split_fit_cache_keys", {})

    if keys.get(oid) == cache_key:
        return  # já cacheado para esta combinação

    _hub_lbl = st.session_state.get("split_hub", "GRU")
    leg_label = (
        f"{other_endpoint} → {_hub_lbl}" if intl_direction == "from_gru"
        else f"{_hub_lbl} → {other_endpoint}"
    )
    with st.spinner(f"🔍 Encaixando voo doméstico ({leg_label})..."):
        fit = SegmentSplitAgent().fit_domestic_leg(
            intl_offer=off,
            other_endpoint=other_endpoint,
            intl_direction=intl_direction,
            adults=adults,
            with_baggage=with_baggage,
        )
    fits[oid] = fit
    keys[oid] = cache_key


def _render_fit_section(fit, oid: str, with_baggage: bool):
    """Renderiza o bloco do encaixe (sob um card de oferta). Mostra a janela,
    a lista de compatíveis selecionáveis e (em expander) os incompatíveis.

    Reage ao toggle de bagagem em tempo real via rebucket_fit() — não chama
    Kayak novamente. Apenas atualiza compatíveis/incompatíveis com base nas
    ofertas já em cache."""
    # Ajusta buckets para o with_baggage atual sem nova chamada Kayak.
    # rebucket_fit é importado aqui (lazy) — falha graciosa se não estiver
    # disponível (ex: agente em versão antiga ainda em sys.modules).
    if fit.with_baggage != with_baggage and fit.all_offers:
        try:
            from pcd.agents.segment_split import rebucket_fit
            fit = rebucket_fit(fit, with_baggage)
            st.session_state["split_fits"][oid] = fit
        except ImportError:
            st.info(
                "ℹ️ Para refiltrar com a nova bagagem, reinicie o Streamlit "
                "(o módulo do agente em memória está desatualizado)."
            )

    _hub_lbl = st.session_state.get("split_hub", "GRU")
    direction_label = (
        f"{fit.intl_offer.origin if fit.intl_direction == 'to_gru' else '?'} → {_hub_lbl}"
        if fit.intl_direction == "to_gru" else
        f"{_hub_lbl} → {fit.intl_offer.destination if fit.intl_direction == 'from_gru' else '?'}"
    )
    # Determina o aeroporto "outro" de forma consistente
    if fit.intl_direction == "from_gru":
        # intl é hub→X; doméstico é Y→hub
        if fit.compatible_offers:
            other = fit.compatible_offers[0].origin
        elif fit.incompatible_offers:
            other = fit.incompatible_offers[0].origin
        elif fit.all_offers:
            other = fit.all_offers[0].origin
        else:
            other = "?"
        direction_label = f"{other} → {_hub_lbl}"
    else:
        if fit.compatible_offers:
            other = fit.compatible_offers[0].destination
        elif fit.incompatible_offers:
            other = fit.incompatible_offers[0].destination
        elif fit.all_offers:
            other = fit.all_offers[0].destination
        else:
            other = "?"
        direction_label = f"{_hub_lbl} → {other}"

    win_start = _fmt_clock(fit.target_window_start) if fit.target_window_start else "—"
    win_end = _fmt_clock(fit.target_window_end) if fit.target_window_end else "—"
    offset_lbl = _OFFSET_LABEL.get(fit.search_date_offset, fit.search_date_offset or "")
    date_lbl = _fmt_dt_dmy(fit.search_date)
    window_kind_lbl = "chegada" if fit.intl_direction == "from_gru" else "partida"

    st.markdown(f"""
<div class="split-fit-block">
  <div class="head">🔍 Encaixe {direction_label}</div>
  <div class="meta">
    Pesquisa em <strong>{date_lbl}</strong>{f' ({offset_lbl})' if offset_lbl else ''}<br/>
    Janela de {window_kind_lbl}: <strong>{win_start} às {win_end}</strong>
  </div>
</div>""", unsafe_allow_html=True)

    # Notas / avisos do agente (janela apertada, etc.)
    for note in fit.notes:
        st.markdown(
            f'<div class="split-no-fit" style="margin-left:18px">{note}</div>',
            unsafe_allow_html=True,
        )

    if fit.no_results:
        return

    # Seleção atual para este voo internacional
    sel_map = st.session_state.setdefault("split_fitted_combinations", {})
    selected_dom = sel_map.get(oid)
    selected_dom_id = _split_offer_id(selected_dom) if selected_dom is not None else None

    # Renderiza compatíveis com botão "Selecionar"
    if not fit.compatible_offers:
        st.markdown(
            '<div class="split-no-fit" style="margin-left:18px">'
            'Nenhum voo compatível com a janela. Considere outra opção internacional '
            'ou veja as opções fora da janela abaixo.</div>',
            unsafe_allow_html=True,
        )

    for dom in fit.compatible_offers:
        dom_id = _split_offer_id(dom)
        is_selected = (dom_id == selected_dom_id)
        st.markdown(
            f'<div style="margin-left:18px">'
            f'{_render_fit_offer_card(dom, with_baggage, selected=is_selected)}'
            f'</div>',
            unsafe_allow_html=True,
        )
        cols = st.columns([3, 1])
        with cols[1]:
            btn_label = "✓ Selecionada" if is_selected else "Selecionar"
            btn_type = "primary" if is_selected else "secondary"
            if st.button(
                btn_label,
                key=f"fit_pick_{oid}_{dom_id}",
                type=btn_type,
                use_container_width=True,
            ):
                if is_selected:
                    sel_map.pop(oid, None)
                else:
                    sel_map[oid] = dom
                # Força nova rerodada para que o card fique verde, a seção
                # 🎯 COMBINAÇÕES SELECIONADAS apareça e o botão de cotar em
                # milhas seja exibido — tudo na mesma interação do clique.
                # Sem o rerun explícito, dependeríamos de uma segunda
                # interação do vendedor para a UI atualizar.
                st.rerun()

    # Incompatíveis em expander
    if fit.incompatible_offers:
        with st.expander(
            f"▾ Mostrar {len(fit.incompatible_offers)} opções fora da janela (acinzentadas)"
        ):
            for dom in fit.incompatible_offers:
                st.markdown(
                    f'<div style="margin-left:0px">'
                    f'{_render_fit_offer_card(dom, with_baggage, dim=True)}'
                    f'</div>',
                    unsafe_allow_html=True,
                )


def _render_offer_list_section(
    title: str,
    offers,
    *,
    intl_direction: str | None = None,
    other_endpoint: str | None = None,
    adults: int = 1,
    with_baggage: bool = False,
    empty_msg: str | None = None,
):
    """Renderiza a lista de ofertas de uma perna. Quando `intl_direction` e
    `other_endpoint` são fornecidos, cada card recebe um botão "Encaixar voo
    nacional" e exibe a seção do encaixe inline."""
    st.markdown(f'<div class="split-leg-title">{title}</div>', unsafe_allow_html=True)
    if not offers:
        msg = empty_msg or "Não foi possível buscar essa perna no momento."
        st.markdown(f'<div class="split-empty">{msg}</div>', unsafe_allow_html=True)
        return

    fit_enabled = (intl_direction is not None) and (other_endpoint is not None)

    for off in offers:
        st.markdown(_render_offer_card(off), unsafe_allow_html=True)

        if not fit_enabled or intl_direction is None or other_endpoint is None:
            continue

        oid = _split_offer_id(off)
        fits = st.session_state.get("split_fits", {})
        has_fit = oid in fits

        cols = st.columns([1.4, 4])
        with cols[0]:
            btn_label = "✓ Encaixe carregado" if has_fit else "➕ Encaixar voo nacional"
            clicked = st.button(
                btn_label,
                key=f"split_fit_btn_{oid}",
                use_container_width=True,
                type="secondary",
            )

        if clicked:
            _trigger_fit(
                off,
                intl_direction=intl_direction,
                other_endpoint=other_endpoint,
                adults=adults,
                with_baggage=with_baggage,
            )

        # Render fit se já carregado
        fit = st.session_state.get("split_fits", {}).get(oid)
        if fit is not None:
            _render_fit_section(fit, oid, with_baggage=with_baggage)
        else:
            # Espaço entre cards quando não há encaixe ainda carregado
            st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)


_PROGRAM_LABELS: dict[str, str] = {
    "SMILES": "Smiles (GOL)",
    "LATAM_PASS": "LATAM Pass",
    "AZUL_FIDELIDADE": "TudoAzul",
    "AZUL_INTERLINE": "TudoAzul Pelo Mundo",
    "COPA": "ConnectMiles (Copa)",
    "IBERIA": "Iberia Plus",
    "BRITISH": "Avios (British)",
}


def _provider_normalized() -> str:
    """Lê o provider escolhido no popover ⚙️ e devolve no formato aceito
    pelo MilesMatchAgent ('buscamilhas' | 'economilhas')."""
    raw = (st.session_state.get("miles_provider") or "BuscaMilhas").lower()
    return "economilhas" if "econom" in raw else "buscamilhas"


def _build_combo_card_html(idx: int, intl, dom, direction: str,
                           with_baggage: bool, direct_price: float) -> tuple[str, float]:
    """Constrói o HTML de um card de combinação Kayak (cinza) e devolve
    (html, total_kayak_brl)."""
    if direction == "from_gru":
        first, second = dom, intl
    else:
        first, second = intl, dom

    layover_min = int(dom.layover_minutes or 0)
    layover_kind, layover_lbl = _layover_kind(layover_min, with_baggage)
    conn_class = "" if layover_kind == "good" else " warn"

    first_date = first.departure_dt.strftime("%d/%m") if first.departure_dt else ""
    first_dep = _fmt_clock(first.departure_dt)
    first_arr = _fmt_arrival_with_offset(first.departure_dt, first.arrival_dt)
    second_date = second.departure_dt.strftime("%d/%m") if second.departure_dt else ""
    second_dep = _fmt_clock(second.departure_dt)
    second_arr = _fmt_arrival_with_offset(second.departure_dt, second.arrival_dt)

    first_cias = _airline_names_pretty(
        list(first.airlines_iata or []), list(first.airlines or []),
    )
    second_cias = _airline_names_pretty(
        list(second.airlines_iata or []), list(second.airlines or []),
    )

    total = float(intl.price_brl) + float(dom.price_brl)
    if direct_price and direct_price > 0:
        savings = direct_price - total
        if savings > 0:
            pct = (savings / direct_price) * 100.0
            savings_html = (
                f'<span class="savings">Economia vs direto '
                f'(R$ {direct_price:,.2f}): R$ {savings:,.2f} ({pct:.0f}%) ✅</span>'
            )
        else:
            savings_html = (
                f'<span class="nosavings">Sem economia vs direto '
                f'(direto R$ {direct_price:,.2f})</span>'
            )
    else:
        savings_html = '<span class="nosavings">Direto sem referência de preço</span>'

    html = f"""
<div class="split-combo-card">
  <div class="ckhead">Combinação {idx}</div>
  <div class="leg-line">
    <span>✈️ {first.origin} → {first.destination} ({first_cias or '—'}) · {first_date} {first_dep}→{first_arr}</span>
    <span class="price">R$ {first.price_brl:,.2f}</span>
  </div>
  <div class="conn{conn_class}">⏱️ Conexão em {st.session_state.get("split_hub", "GRU")}: {_fmt_hm(layover_min)} {layover_lbl}</div>
  <div class="leg-line">
    <span>✈️ {second.origin} → {second.destination} ({second_cias or '—'}) · {second_date} {second_dep}→{second_arr}</span>
    <span class="price">R$ {second.price_brl:,.2f}</span>
  </div>
  <div class="total">
    <span>Total Kayak (dinheiro)</span>
    <span>R$ {total:,.2f}</span>
  </div>
  <div style="margin-top:6px;text-align:right">{savings_html}</div>
</div>"""
    return html, total


def _trigger_miles_match(combo_key: str, intl, dom, direction: str,
                         adults: int, with_baggage: bool, provider: str):
    """Roda match_domestic_leg + match_international_leg em paralelo e
    cacheia em st.session_state[combo_key].

    Idempotente — usa um sub-dict de chaves para detectar se a combinação
    (intl_oid, dom_oid, provider) já foi consultada."""
    from concurrent.futures import ThreadPoolExecutor
    from pcd.agents.miles_match import MilesMatchAgent

    intl_oid = _split_offer_id(intl)
    dom_oid = _split_offer_id(dom)
    cache_key = f"miles_match_cache_{combo_key}_{dom_oid}_{provider}"
    keys = st.session_state.setdefault("miles_match_cache_keys", {})
    if keys.get(combo_key) == cache_key:
        return

    # Direção do voo doméstico em relação ao internacional
    if direction == "from_gru":
        # Doméstica chega em GRU ANTES da partida intl.
        # Para a busca da intl: a outra perna (doméstica) está antes → "before_intl"
        # Para a busca da doméstica: a outra perna (intl) está depois → "after_intl"
        intl_other_dt = dom.arrival_dt
        intl_dir = "before_intl"
        dom_other_dt = intl.departure_dt
        dom_dir = "after_intl"
    else:  # to_gru
        # Internacional chega em GRU; doméstica decola depois.
        intl_other_dt = dom.departure_dt
        intl_dir = "after_intl"
        dom_other_dt = intl.arrival_dt
        dom_dir = "before_intl"

    if intl_other_dt is None or dom_other_dt is None:
        st.warning("Não foi possível cotar — voos sem horário definido.")
        return

    agent = MilesMatchAgent()
    with st.spinner("💎 Consultando programas de milhas direcionados..."):
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_dom = ex.submit(
                agent.match_domestic_leg,
                kayak_offer=dom, other_leg_dt=dom_other_dt,
                other_leg_direction=dom_dir,
                with_baggage=with_baggage, adults=adults, provider=provider,
            )
            f_intl = ex.submit(
                agent.match_international_leg,
                kayak_offer=intl, domestic_leg_dt=intl_other_dt,
                domestic_leg_direction=intl_dir,
                with_baggage=with_baggage, adults=adults, provider=provider,
            )
            dom_match = f_dom.result()
            intl_match = f_intl.result()

    st.session_state[combo_key] = {
        "domestic": dom_match,
        "international": intl_match,
        "with_baggage": with_baggage,
        "provider": provider,
    }
    keys[combo_key] = cache_key


def _format_program_list(programs: list[str]) -> str:
    if not programs:
        return "—"
    return ", ".join(_PROGRAM_LABELS.get(p, p) for p in programs)


def _format_dt_long(dt) -> str:
    if dt is None:
        return "—"
    try:
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return "—"


def _render_miles_option_card(opt, kayak_price: float, *, dim: bool = False) -> str:
    """Card de uma opção de milhas. `dim=True` para opções secundárias
    (não-exato, mas dentro da janela)."""
    real = float(opt.total_real_cost_brl)
    diff = kayak_price - real
    if diff > 0:
        compare_html = (
            f'<div class="compare savings">Economia: R$ {diff:,.2f} ✅</div>'
        )
    elif diff < 0:
        compare_html = (
            f'<div class="compare warn">⚠ Milhas mais caras que dinheiro nesse voo '
            f'(+R$ {abs(diff):,.2f})</div>'
        )
    else:
        compare_html = '<div class="compare">Equivalente ao Kayak</div>'

    badges = []
    if opt.is_exact_match:
        badges.append('<span class="badge-exact">✓ EXATO</span>')
    badges.append(
        f'<span class="badge-prog">{_PROGRAM_LABELS.get(opt.program, opt.program)}</span>'
    )
    if opt.carrier:
        cd = _airline_display_ext(opt.carrier)
        badges.append(
            f'<span class="badge-prog" style="border-left-color:{cd["color"]};'
            f'background:{cd["bg"]}">{cd["name"]}</span>'
        )
    badges_html = "".join(badges)

    flight = opt.flight_number or "—"
    if opt.carrier and opt.carrier not in flight.upper():
        flight = f"{opt.carrier} {flight}"

    times = ""
    if opt.departure_dt is not None:
        dep = opt.departure_dt.strftime("%d/%m %H:%M")
        arr = opt.arrival_dt.strftime("%H:%M") if opt.arrival_dt else "—"
        times = f"{dep} → {arr}"

    classes = "miles-option"
    if opt.is_exact_match:
        classes += " exact"
    if dim:
        classes += " dim"

    return f"""
<div class="{classes}">
  <div>
    <div class="head">{badges_html} <span style="font-family:Menlo,monospace">{flight}</span></div>
    <div class="meta">{times}</div>
    <div class="miles-line">
      <strong>{opt.miles:,}</strong> milhas + R$ {opt.taxes_brl:,.2f} taxas
    </div>
    {compare_html}
  </div>
  <div class="right">
    <div class="real">R$ {real:,.2f}</div>
    <div class="label">custo real</div>
  </div>
</div>"""


def _render_miles_leg_block(match, kayak_offer, leg_label_emoji: str,
                            leg_label_text: str) -> None:
    """Renderiza a seção de uma perna no comparativo de milhas."""
    from pcd.agents.miles_match import rebucket_match  # noqa: F401 (cache OK)

    # Aplicar rebucket se with_baggage mudou — feito no caller.
    # Mostra nome completo da companhia (ex: "Air Tahiti Nui") e não a sigla.
    flight_disp = _airline_names_pretty(
        list(kayak_offer.airlines_iata or []),
        list(kayak_offer.airlines or []),
    )

    progs_label = _format_program_list(match.programs_searched)

    st.markdown(f"""
<div class="miles-match-leg">
  <div class="lhead">{leg_label_emoji} {leg_label_text} — {kayak_offer.origin} → {kayak_offer.destination} ({flight_disp or '—'})</div>
  <div class="lprog">Programas consultados: {progs_label}</div>
</div>""", unsafe_allow_html=True)

    for note in match.notes:
        st.markdown(
            f'<div class="miles-info-note">ℹ️ {note}</div>',
            unsafe_allow_html=True,
        )

    if not match.options:
        msg = match.no_results_reason or (
            "Sem disponibilidade nesse programa para essa data/companhia."
        )
        st.markdown(
            f'<div class="miles-empty">{msg}</div>',
            unsafe_allow_html=True,
        )
        return

    # Exact matches primeiro, depois secundários
    exact_options = [o for o in match.options if o.is_exact_match]
    other_options = [o for o in match.options if not o.is_exact_match]

    kayak_price = float(kayak_offer.price_brl or 0.0)

    cards = []
    for opt in exact_options:
        cards.append(_render_miles_option_card(opt, kayak_price))
    if other_options:
        if exact_options:
            cards.append(
                '<div class="miles-secondary-title">Outras opções compatíveis na janela:</div>'
            )
        for opt in other_options:
            cards.append(_render_miles_option_card(opt, kayak_price, dim=True))

    st.markdown("".join(cards), unsafe_allow_html=True)


def _render_miles_match_block(payload: dict, intl, dom, direction: str,
                              with_baggage: bool, kayak_total: float,
                              direct_price: float, combo_idx: int) -> None:
    """Renderiza o bloco completo de comparativo Kayak vs Milhas para
    uma combinação. Aplica rebucket_match client-side se with_baggage
    diferir do que foi cacheado."""
    from pcd.agents.miles_match import rebucket_match

    dom_match = payload.get("domestic")
    intl_match = payload.get("international")
    if dom_match is None or intl_match is None:
        return

    # Re-bucket client-side se a bagagem mudou
    if payload.get("with_baggage") != with_baggage:
        if dom_match.options:
            dom_match = rebucket_match(dom_match, with_baggage)
        if intl_match.options:
            intl_match = rebucket_match(intl_match, with_baggage)
        payload["domestic"] = dom_match
        payload["international"] = intl_match
        payload["with_baggage"] = with_baggage

    # Cabeçalho
    st.markdown(f"""
<div class="miles-match-block">
  <div class="mtitle">💎 COTAÇÃO EM MILHAS — Combinação {combo_idx}</div>
</div>""", unsafe_allow_html=True)

    # Perna doméstica
    _render_miles_leg_block(
        dom_match, dom,
        leg_label_emoji="🇧🇷", leg_label_text="PERNA DOMÉSTICA",
    )

    # Perna internacional
    _render_miles_leg_block(
        intl_match, intl,
        leg_label_emoji="🌎", leg_label_text="PERNA INTERNACIONAL",
    )

    # Resumo comparativo
    _render_miles_match_summary(
        intl_match=intl_match, dom_match=dom_match,
        intl=intl, dom=dom,
        kayak_total=kayak_total, direct_price=direct_price,
    )


def _render_miles_match_summary(intl_match, dom_match, intl, dom,
                                kayak_total: float, direct_price: float) -> None:
    """Bloco final: 'combinação ótima' = melhor opção de cada perna
    (preferindo exact_match, depois preço)."""
    def _best(options):
        if not options:
            return None
        # Já vem ordenado por (not is_exact_match, total_real_cost_brl)
        return options[0]

    best_dom = _best(dom_match.options)
    best_intl = _best(intl_match.options)

    if best_dom is None and best_intl is None:
        st.markdown("""
<div class="miles-summary">
  <div class="stitle">📊 RESUMO COMPARATIVO</div>
  <div class="row">
    <span>Sem opções de milhas válidas para nenhuma das pernas.</span>
  </div>
  <div class="row">
    <span>Total Kayak (dinheiro)</span>
    <span class="v">R$ """ + f"{kayak_total:,.2f}" + """</span>
  </div>
</div>""", unsafe_allow_html=True)
        return

    # Custos por perna
    dom_real = float(best_dom.total_real_cost_brl) if best_dom else float(dom.price_brl)
    intl_real = float(best_intl.total_real_cost_brl) if best_intl else float(intl.price_brl)
    dom_label_origin = "Milhas" if best_dom else "Kayak"
    intl_label_origin = "Milhas" if best_intl else "Kayak"

    miles_total = dom_real + intl_real

    dom_line = (
        f"{dom.origin}→{dom.destination} ({dom_label_origin}"
        + (f", {_PROGRAM_LABELS.get(best_dom.program, best_dom.program)}, {best_dom.flight_number}"
           if best_dom else "") + f"): R$ {dom_real:,.2f}"
    )
    intl_line = (
        f"{intl.origin}→{intl.destination} ({intl_label_origin}"
        + (f", {_PROGRAM_LABELS.get(best_intl.program, best_intl.program)}, {best_intl.flight_number}"
           if best_intl else "") + f"): R$ {intl_real:,.2f}"
    )

    diff_kayak = kayak_total - miles_total
    if diff_kayak > 0:
        pct = (diff_kayak / kayak_total) * 100.0 if kayak_total > 0 else 0.0
        savings_milhas_html = (
            f'<div class="savings-line good">Economia milhas vs Kayak: '
            f'R$ {diff_kayak:,.2f} ({pct:.0f}%) ✅</div>'
        )
    elif diff_kayak < 0:
        savings_milhas_html = (
            f'<div class="savings-line bad">Milhas saíram mais caras que Kayak '
            f'em R$ {abs(diff_kayak):,.2f} ⚠</div>'
        )
    else:
        savings_milhas_html = (
            '<div class="savings-line">Milhas e Kayak praticamente equivalentes.</div>'
        )

    direct_html = ""
    if direct_price and direct_price > 0:
        diff_direct = direct_price - miles_total
        if diff_direct > 0:
            pct_d = (diff_direct / direct_price) * 100.0
            direct_html = (
                f'<div class="savings-line good">Economia vs voo direto '
                f'{intl.origin if dom_match.kayak_reference.origin == intl.origin else dom.origin}'
                f'→{intl.destination if dom_match.kayak_reference.destination == intl.destination else dom.destination}'
                f' (R$ {direct_price:,.2f}): R$ {diff_direct:,.2f} ({pct_d:.0f}%) ✅</div>'
            )
        else:
            direct_html = (
                f'<div class="savings-line bad">Combinação em milhas saiu '
                f'R$ {abs(diff_direct):,.2f} mais cara que o voo direto '
                f'(R$ {direct_price:,.2f}).</div>'
            )

    has_both_exact = (
        best_dom is not None and best_dom.is_exact_match and
        best_intl is not None and best_intl.is_exact_match
    )
    optimal_badge = (
        '<span style="font-size:11px;background:#1a7a4a;color:#fff;'
        'padding:2px 8px;border-radius:8px;font-weight:700;'
        'margin-left:8px;">✓ COMBINAÇÃO ÓTIMA</span>'
        if has_both_exact else ""
    )

    st.markdown(f"""
<div class="miles-summary">
  <div class="stitle">📊 RESUMO COMPARATIVO {optimal_badge}</div>
  <div class="row"><span><strong>Kayak (dinheiro):</strong></span></div>
  <div class="row">
    <span>&nbsp;&nbsp;{dom.origin}→{dom.destination} R$ {dom.price_brl:,.2f} + {intl.origin}→{intl.destination} R$ {intl.price_brl:,.2f}</span>
    <span class="v">R$ {kayak_total:,.2f}</span>
  </div>
  <div class="row" style="margin-top:8px"><span><strong>Milhas (combinação ótima):</strong></span></div>
  <div class="row"><span>&nbsp;&nbsp;{dom_line}</span></div>
  <div class="row"><span>&nbsp;&nbsp;{intl_line}</span></div>
  <div class="total-row">
    <span>Total milhas (custo real)</span>
    <span>R$ {miles_total:,.2f}</span>
  </div>
  {savings_milhas_html}
  {direct_html}
</div>""", unsafe_allow_html=True)


def _render_combinations_summary(direct_price: float, with_baggage: bool,
                                 provider: str = "buscamilhas",
                                 adults: int = 1):
    """Mostra ao final um bloco com todas as combinações atualmente
    selecionadas pelo vendedor (intl + doméstico encaixado), com botão
    de cotação em milhas direcionada por combinação."""
    sel_map = st.session_state.get("split_fitted_combinations") or {}
    fits = st.session_state.get("split_fits") or {}
    if not sel_map:
        return

    # Lista de combinações válidas (intl ainda existe em fits)
    rows = []
    for intl_oid, dom_offer in sel_map.items():
        fit = fits.get(intl_oid)
        if fit is None or dom_offer is None:
            continue
        intl = fit.intl_offer
        rows.append((intl_oid, intl, dom_offer, fit.intl_direction))

    if not rows:
        return

    # Cabeçalho do bloco verde (apenas o título, os cards são interleavados
    # com botões — Streamlit não permite widgets dentro de st.markdown)
    st.markdown(f"""
<div class="split-combo-block" style="padding:14px 18px 4px 18px">
  <div class="title">🎯 COMBINAÇÕES SELECIONADAS ({len(rows)})</div>
</div>""", unsafe_allow_html=True)

    for idx, (intl_oid, intl, dom, direction) in enumerate(rows, start=1):
        card_html, kayak_total = _build_combo_card_html(
            idx=idx, intl=intl, dom=dom, direction=direction,
            with_baggage=with_baggage, direct_price=direct_price,
        )
        st.markdown(card_html, unsafe_allow_html=True)

        combo_key = f"miles_match_{intl_oid}"
        cached = st.session_state.get(combo_key)

        # Botão "Cotar em milhas"
        cols = st.columns([2, 5])
        with cols[0]:
            btn_label = (
                "✓ Milhas cotadas" if cached is not None
                else "💎 Cotar essa combinação em milhas"
            )
            clicked = st.button(
                btn_label,
                key=f"miles_match_btn_{intl_oid}",
                use_container_width=True,
                type="secondary",
            )

        if clicked:
            _trigger_miles_match(
                combo_key=combo_key, intl=intl, dom=dom, direction=direction,
                adults=adults, with_baggage=with_baggage, provider=provider,
            )

        cached = st.session_state.get(combo_key)
        if cached is not None:
            _render_miles_match_block(
                payload=cached, intl=intl, dom=dom, direction=direction,
                with_baggage=with_baggage, kayak_total=kayak_total,
                direct_price=direct_price, combo_idx=idx,
            )


def _render_split_section(result, with_baggage: bool = False, adults: int = 1):
    """Renderiza a seção de Quebra de Trecho. Recebe SimpleSegmentResult.

    Layout depende de result.route_type:
      - not_applicable → mensagem informativa
      - br_to_intl     → 1 lista (GRU → destino) com encaixe doméstico
      - intl_to_br     → 1 lista (origem → GRU) com encaixe doméstico
      - br_domestic    → 2 listas, ambas com encaixe doméstico
    Ao final, sumário das combinações selecionadas (se houver).
    """
    if result is None:
        return

    st.markdown(_SPLIT_CSS, unsafe_allow_html=True)

    # Header — sempre visível, mostra a estratégia
    date_label = ""
    if result.date:
        try:
            from datetime import date as _d
            _dt = _d.fromisoformat(result.date)
            date_label = f" · 📅 {_dt.strftime('%d/%m/%Y')}"
        except Exception:
            date_label = f" · 📅 {result.date}"

    _hub_used = getattr(result, "hub", "GRU") or "GRU"
    _hub_name_map = {
        "GRU": "São Paulo", "GIG": "Rio de Janeiro", "CNF": "Belo Horizonte",
        "FOR": "Fortaleza", "REC": "Recife", "BSB": "Brasília",
        "POA": "Porto Alegre", "CWB": "Curitiba", "SSA": "Salvador",
    }
    _hub_full = _hub_name_map.get(_hub_used, _hub_used)
    st.markdown(f"""
<div class="split-header">
  <div class="split-icon">✂️</div>
  <div>
    <div class="split-title">Quebra de Trecho — {result.origin} → {result.destination}{date_label}</div>
    <div class="split-sub">Estratégia: quebra em {_hub_full} ({_hub_used})</div>
  </div>
</div>""", unsafe_allow_html=True)

    # Não aplicável: mensagem e fim
    if result.route_type == "not_applicable":
        reason = result.not_applicable_reason or "Rota não permite quebra em GRU."
        st.markdown(f"""
<div class="split-na">
  <div class="title">ℹ️ Quebra de trecho não se aplica</div>
  <div class="body">{reason}<br/>
  A quebra de trecho é usada para rotas que não passam diretamente por São Paulo.</div>
</div>""", unsafe_allow_html=True)
        return

    # Card de referência (preço direto)
    direct_price = result.direct_offer.price_brl if result.direct_offer else 0.0
    if result.direct_offer is not None:
        st.markdown(f"""
<div class="split-direct">
  <div class="icon">💰</div>
  <div>
    <div class="label">Preço direto {result.origin} → {result.destination}</div>
    <div class="price">R$ {direct_price:,.2f}</div>
    <div class="foot">Referência: melhor tarifa direta retornada pelo Kayak</div>
  </div>
</div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"""
<div class="split-direct">
  <div class="icon">💰</div>
  <div>
    <div class="label">Preço direto {result.origin} → {result.destination}</div>
    <div class="price">—</div>
    <div class="foot">Não foi possível buscar a rota direta para referência.</div>
  </div>
</div>""", unsafe_allow_html=True)

    # Avisos parciais (falha em alguma perna, etc.)
    for note in result.notes:
        st.warning(note)

    # Pernas conforme route_type — todas com encaixe habilitado
    if result.route_type == "br_to_intl":
        # Perna intl: hub → destino. Encaixe: origem → hub (from_gru direction).
        _render_offer_list_section(
            f"🌎 PERNA INTERNACIONAL — {_hub_used} → {result.destination}",
            result.leg_from_gru,
            intl_direction="from_gru",
            other_endpoint=result.origin,
            adults=adults,
            with_baggage=with_baggage,
        )

    elif result.route_type == "intl_to_br":
        # Perna intl: origem → hub. Encaixe: hub → destino (to_gru direction).
        _render_offer_list_section(
            f"🌎 PERNA INTERNACIONAL — {result.origin} → {_hub_used}",
            result.leg_to_gru,
            intl_direction="to_gru",
            other_endpoint=result.destination,
            adults=adults,
            with_baggage=with_baggage,
        )

    else:  # br_domestic — duas pernas, ambas com encaixe independente
        # Perna 1 (origem → hub): chega no hub → encaixe é hub → destino.
        _render_offer_list_section(
            f"🇧🇷 PERNA 1 — {result.origin} → {_hub_used}",
            result.leg_to_gru,
            intl_direction="to_gru",
            other_endpoint=result.destination,
            adults=adults,
            with_baggage=with_baggage,
        )
        # Perna 2 (hub → destino): sai do hub → encaixe é origem → hub.
        _render_offer_list_section(
            f"🇧🇷 PERNA 2 — {_hub_used} → {result.destination}",
            result.leg_from_gru,
            intl_direction="from_gru",
            other_endpoint=result.origin,
            adults=adults,
            with_baggage=with_baggage,
        )

    # Sumário das combinações selecionadas + comparativo Kayak vs Milhas
    _render_combinations_summary(
        direct_price=direct_price,
        with_baggage=with_baggage,
        provider=_provider_normalized(),
        adults=adults,
    )


# ═══════════════════════════════════════════════════════════════
# Cotação Inteligente — Etapa 1 (render) + Etapa 2 (Milhas / Quebra)
# ═══════════════════════════════════════════════════════════════
_smart_events: dict | None = None
if st.session_state.get("smart_result") is not None:
    _smart_events = _render_smart_quote_section()

# ── Etapa 2A: Milhas (pipeline para data escolhida) ──
if _smart_events and _smart_events.get("miles_clicked"):
    _miles_iso = _smart_events.get("chosen_iso") or ""
    pi_state = st.session_state.get("parsed_intent")
    if pi_state is not None and isinstance(_miles_iso, str) and _miles_iso:
        from datetime import date as _date_cls
        try:
            _chosen_date = _date_cls.fromisoformat(_miles_iso)
        except ValueError:
            _chosen_date = None

        # Reaproveita TODAS as ofertas cacheadas da Cotação Inteligente para a
        # data selecionada — evita nova chamada Kayak e garante que o gráfico,
        # tabela e Veredito mostrem dados consistentes da MESMA data.
        _smart_res = st.session_state.get("smart_result")
        _cached_kayak = None
        _cached_kayak_list = []
        if _smart_res is not None:
            _cached_kayak = (getattr(_smart_res, "best_offer_per_date", None) or {}).get(_miles_iso)
            _cached_kayak_list = (getattr(_smart_res, "daily_offers", None) or {}).get(_miles_iso) or []
        # TEMP_LOG — diagnóstico Smart Quote → Cotação Completa (remover após validar)
        print(
            f"[COTACAO_COMPLETA] data_smart={_miles_iso} "
            f"ofertas_cacheadas={len(_cached_kayak_list)} "
            f"best_carrier={getattr(_cached_kayak, 'main_carrier_iata', None) if _cached_kayak else None}"
        )

        # Lista de companhias para o run_pipeline: REMOVE 'KAYAK' para não
        # refazer a chamada de dinheiro (vamos injetar a cacheada abaixo).
        _smart_companhias = [c for c in (companhias_selecionadas or []) if c.upper() != "KAYAK"]

        if _chosen_date is not None:
            with st.spinner(f"Cotando milhas para {_fmt_date_long(_miles_iso)}..."):
                if provider == "BuscaMilhas":
                    _res2 = run_pipeline(
                        prompt=st.session_state.get("prompt_input", ""),
                        top_n=top_n, use_fixtures=use_fixtures,
                        origin=pi_state.origin_iata,
                        destination=pi_state.destination_iata,
                        date_start=_chosen_date,
                        date_end=None,
                        date_return=pi_state.date_return,
                        flex_mode="none",
                        flex_days=0,
                        flex_return=False,
                        direct_only=getattr(pi_state, "direct_only", False),
                        companhias=_smart_companhias if _smart_companhias else None,
                    )
                    st.session_state["pipeline_result"] = _res2
                    st.session_state.pop("economilhas_partial", None)
                else:
                    from pcd.agents.economilhas_pipeline import run_pipeline_economilhas
                    miles_airlines = []
                    if e_smiles:   miles_airlines.append("SMILES")
                    if e_latam_p:  miles_airlines.append("LATAM")
                    if e_azul:     miles_airlines.append("AZUL")
                    if e_azul_int: miles_airlines.append("AZUL_INTERLINE")
                    if e_copa_e:   miles_airlines.append("COPA")
                    if e_iberia:   miles_airlines.append("IBERIA")
                    if e_british:  miles_airlines.append("BRITISH")
                    try:
                        _res2, _partial = run_pipeline_economilhas(
                            prompt=st.session_state.get("prompt_input", ""),
                            top_n=top_n, use_fixtures=use_fixtures,
                            origin=pi_state.origin_iata,
                            destination=pi_state.destination_iata,
                            date_start=_chosen_date,
                            date_end=None,
                            date_return=pi_state.date_return,
                            flex_mode="none",
                            flex_days=0,
                            flex_return=False,
                            direct_only=getattr(pi_state, "direct_only", False),
                            adults=getattr(pi_state, "adults", 1) or 1,
                            miles_airlines=miles_airlines,
                            # Skip Kayak cash — injetamos a oferta cacheada do Smart Quote
                            use_kayak_cash=False,
                            debug=bool(e_debug),
                        )
                        st.session_state["pipeline_result"] = _res2
                        st.session_state["economilhas_partial"] = _partial
                    except Exception as _ex:
                        st.session_state["economilhas_partial"] = [{
                            "airline": "ALL",
                            "message": f"Falha geral Economilhas: {str(_ex)[:200]}",
                            "providerStatusCode": None, "fatal": True,
                        }]

            # Injetar TODAS as ofertas Kayak cacheadas no pipeline_result —
            # garante que a tabela Dinheiro mostra múltiplas linhas, todas da
            # data selecionada na Cotação Inteligente, e com o MESMO preço do
            # gráfico. Sem markup aqui (preço de mercado) — markup é aplicado
            # apenas na exibição via kayak_sell_price().
            _pr_obj = st.session_state.get("pipeline_result")
            if _pr_obj is not None:
                _synth_offers = []
                # Itera sobre todas as ofertas da data; fallback para
                # best_offer_per_date quando daily_offers não veio (cache antigo).
                _sources = _cached_kayak_list or (
                    [_cached_kayak] if _cached_kayak is not None else []
                )
                for _lite in _sources:
                    if _lite is None:
                        continue
                    _s = _synthesize_kayak_offer_from_cache(
                        _lite,
                        origin_iata=pi_state.origin_iata or "",
                        destination_iata=pi_state.destination_iata or "",
                    )
                    if _s is not None:
                        _synth_offers.append(_s)
                # Ordena por preço de mercado crescente e limita ao top_n configurado
                _synth_offers.sort(key=lambda o: safe_float(o.equivalent_brl))
                try:
                    _top_n_cap = int(top_n) if top_n else 10
                except (TypeError, ValueError):
                    _top_n_cap = 10
                _synth_offers = _synth_offers[: max(_top_n_cap, 1)]
                if _synth_offers:
                    # Substitui qualquer money_offer existente (já não deveria ter
                    # nenhum porque pulamos Kayak no _smart_companhias, mas seguros)
                    _pr_obj.money_offers = _synth_offers
                    _pr_obj.best_money = _synth_offers[0]
                    # Recompute best_overall se ainda não houver
                    if getattr(_pr_obj, "best_overall", None) is None:
                        _pr_obj.best_overall = _synth_offers[0]
                # TEMP_LOG — diagnóstico (remover após validar)
                print(
                    f"[COTACAO_COMPLETA] sintetizadas={len(_synth_offers)} ofertas Kayak "
                    f"para data={_miles_iso} | preços={[o.equivalent_brl for o in _synth_offers[:5]]}"
                )

            st.session_state["smart_selected_date"] = _miles_iso
            # Cotação Completa gerada para a data atual — limpa flag de stale.
            st.session_state.pop("smart_stale_quote", None)

# ── Etapa 2B: Quebra de trecho (fase 1 hub fixo GRU + fase 2 encaixe) ──
# Cache key: f"split_{ori}_{dst}_{data}_{provider}". Mudou data ou rota
# → re-busca + reset dos encaixes (fase 2).
if _smart_events:
    from pcd.agents.segment_split import SegmentSplitAgent, SimpleSegmentResult

    pi_state = st.session_state.get("parsed_intent")
    _split_chosen_iso = _smart_events.get("chosen_iso") or ""
    _split_with_bag = bool(_smart_events.get("with_baggage"))

    # Invalida o render quando a data atualmente selecionada no select
    # não corresponde mais à data em cache. Limpa também os encaixes (fase 2)
    # — combinações antigas pertencem a outra data e não fazem mais sentido.
    _split_cached = st.session_state.get("split_result")
    _split_cached_date = (
        getattr(_split_cached, "date", None) if _split_cached is not None else None
    )
    if _split_cached_date is not None and _split_cached_date != _split_chosen_iso:
        st.session_state["split_active"] = False
        st.session_state["split_fits"] = {}
        st.session_state["split_fit_cache_keys"] = {}
        st.session_state["split_fitted_combinations"] = {}
        # Limpa também as cotações de milhas (fase 3) — a combinação não
        # existe mais nesta data.
        for _kk in list(st.session_state.keys()):
            _ks = str(_kk)
            if _ks.startswith("miles_match_") and not _ks.endswith("cache_keys"):
                st.session_state.pop(_kk, None)
        st.session_state["miles_match_cache_keys"] = {}

    if (
        _smart_events.get("split_clicked")
        and pi_state is not None
        and isinstance(_split_chosen_iso, str) and _split_chosen_iso
    ):
        _split_ori = (pi_state.origin_iata or "").upper()
        _split_dst = (pi_state.destination_iata or "").upper()
        _split_adults = int(getattr(pi_state, "adults", 1) or 1)
        _split_ret_iso = pi_state.date_return.isoformat() if pi_state.date_return else None
        _split_hub = (_smart_events.get("hub") or st.session_state.get("split_hub") or "GRU").upper()
        _cache_key = (
            f"split_{_split_ori}_{_split_dst}_{_split_chosen_iso}_{_split_hub}_{provider}"
        )

        if st.session_state.get("split_data_key") != _cache_key:
            with st.spinner(
                f"✂️ Quebrando trecho em {_split_hub} para {_fmt_date_long(_split_chosen_iso)}..."
            ):
                _split_result: SimpleSegmentResult = SegmentSplitAgent().run(
                    origin=_split_ori, destination=_split_dst,
                    date=_split_chosen_iso,
                    adults=_split_adults,
                    return_date=_split_ret_iso,
                    hub=_split_hub,
                )
            st.session_state["split_result"] = _split_result
            st.session_state["split_data_key"] = _cache_key
            # Mudou a chave da quebra → reset dos encaixes e cotações de
            # milhas (rota/data novas).
            st.session_state["split_fits"] = {}
            st.session_state["split_fit_cache_keys"] = {}
            st.session_state["split_fitted_combinations"] = {}
            for _kk in list(st.session_state.keys()):
                _ks = str(_kk)
                if _ks.startswith("miles_match_") and not _ks.endswith("cache_keys"):
                    st.session_state.pop(_kk, None)
            st.session_state["miles_match_cache_keys"] = {}
        st.session_state["split_active"] = True

    _split_obj = st.session_state.get("split_result")
    _split_obj_date = getattr(_split_obj, "date", None) if _split_obj is not None else None
    if (
        st.session_state.get("split_active")
        and _split_obj is not None
        and _split_obj_date == _split_chosen_iso
    ):
        _split_adults_render = int(
            getattr(pi_state, "adults", 1) if pi_state is not None else 1
        ) or 1
        _render_split_section(
            _split_obj,
            with_baggage=_split_with_bag,
            adults=_split_adults_render,
        )

# Sem pipeline_result até este ponto (smart-only): pára aqui.
if "pipeline_result" not in st.session_state:
    st.stop()

res          = st.session_state["pipeline_result"]
incluir_mala = st.session_state.get("v_bagagem", False)

if getattr(res, "direct_filter_warning", None):
    st.warning(res.direct_filter_warning)

# ── Avisos do provedor Economilhas (falhas parciais / 402 / 401) ──
_eco_partial = st.session_state.get("economilhas_partial") or []
if _eco_partial:
    _fatal_402 = next((p for p in _eco_partial if p.get("providerStatusCode") == 402), None)
    _fatal_401 = next((p for p in _eco_partial if p.get("providerStatusCode") == 401), None)
    if _fatal_402:
        st.error(
            "❌ Quota Economilhas esgotada. Verifique no popover de configurações ⚙️ "
            "ou alterne o provedor para BuscaMilhas."
        )
    elif _fatal_401:
        st.error(
            "🔑 ECONOMILHAS_API_KEY inválida ou ausente. Defina-a no `.env` e reinicie o app."
        )
    else:
        # Falhas parciais — mostra companhias que não vieram para o vendedor
        # saber por que algum programa pode estar ausente nas tabs.
        _msgs = []
        for p in _eco_partial:
            label = p.get("airline") or "—"
            msg = p.get("message") or "falhou"
            _msgs.append(f"**{label}**: {msg}")
        if _msgs:
            st.warning(
                "⚠️ Alguns programas Economilhas não retornaram nesta busca:  \n"
                + "  \n".join(f"• {m}" for m in _msgs)
            )

# Quando a Cotação Inteligente está ativa, o Veredito PcD aparece sob um
# divisor visual com a data escolhida — deixa claro que a "cotação completa"
# é da data selecionada, não da data original parseada.
if st.session_state.get("smart_active") and st.session_state.get("smart_selected_date"):
    _sel_iso = st.session_state["smart_selected_date"]
    st.markdown("---")
    st.markdown(f"### 💎 Cotação Completa para {_fmt_date_long(_sel_iso)}")
    st.caption(f"Baseado na data selecionada na Cotação Inteligente: **{_sel_iso}**")

COLS = ["ID", "Companhia", "Trecho", "Data",
        "Milhas", "Custo Real (mi+taxas)", "Taxas", "Preço Final", "Valor c/ Mala",
        "Duração", "Escalas", "Saída", "Chegada", "Local Escala"]

if adults > 1:
    COLS.insert(6, f"Total ({adults}pax)")


# ─── Monta tabs dinamicamente ──────────────────────────────────
# Ordem: Melhores Achados | Dinheiro | [nacionais] | [internacionais] | Ranking Geral

def _has_result_for(cia: str) -> bool:
    """True se a companhia está ativa E trouxe pelo menos uma oferta."""
    active = _CIA_ACTIVE.get(cia.upper(), False)
    if not active:
        return False
    src_name = f"buscamilhas_{cia.lower()}"
    miles_ofs = getattr(res, "miles_offers", []) or []
    return any(source_is(o, src_name) for o in miles_ofs)

tab_specs = [("✨ O Veredito PcD", "verdito"), ("💵 Dinheiro (Kayak)", "dinheiro")]

for cia in COMPANHIAS_NACIONAIS:
    if _CIA_ACTIVE.get(cia, False):
        meta = _CIA_META.get(cia, {"emoji": "✈️"})
        tab_specs.append((f"{meta['emoji']} {cia}", f"cia_{cia.lower()}"))

for cia in COMPANHIAS_INTERNACIONAIS:
    if _CIA_ACTIVE.get(cia, False):
        meta = _CIA_META.get(cia, {"emoji": "✈️"})
        tab_specs.append((f"{meta['emoji']} {cia}", f"cia_{cia.lower()}"))

tab_specs.append(("🌍 Internacional (MCP)", "mcp_award"))

if s_qatar:
    tab_specs.append(("🇶🇦 Qatar", "mcp_qatar"))

tab_specs.append(("📊 Ranking Geral", "ranking"))

tab_labels = [t[0] for t in tab_specs]
tab_keys   = [t[1] for t in tab_specs]


tabs = st.tabs(tab_labels)

# ─── helpers banner ───────────────────────────────────────────
def _is_money_offer(offer) -> bool:
    src = str(getattr(getattr(offer, "source", None), "value", "") or "").lower()
    return "kayak" in src or "money" in src

def _offer_main_display(offer, adults=1):
    if offer is None:
        return "—", "—", ""
    airline = str(getattr(offer, "airline", ""))
    dt_str = ""
    if getattr(offer, "outbound", None) and getattr(offer.outbound, "segments", []):
        dt = offer.outbound.segments[0].departure_dt
        if dt: dt_str = f"📅 Partida: {dt.strftime('%d/%m')} · "

    if _is_money_offer(offer):
        # Preço de MERCADO = valor cru Kayak (sem markup).
        # Preço de VENDA = mercado × (1+markup) — é o que a agência cobra.
        market_unit = safe_float(getattr(offer, "equivalent_brl", 0))
        sell_unit = kayak_sell_price(offer)
        markup_lbl = f"{int(round(kayak_markup_pct() * 100))}%"
        if adults > 1:
            sell_total = sell_unit * adults
            market_total = market_unit * adults
            return (
                f"R$ {sell_total:,.2f}",
                f"Preço de venda — Total para {adults} pax (markup {markup_lbl})",
                (
                    f"Por passageiro: R$ {sell_unit:,.2f} · "
                    f"Mercado: R$ {market_total:,.2f} (R$ {market_unit:,.2f}/pax) · "
                    f"{dt_str}{airline} Kayak"
                ),
            )
        else:
            return (
                f"R$ {sell_unit:,.2f}",
                f"{airline} · Kayak · preço de venda (markup {markup_lbl})",
                f"{dt_str}Mercado: R$ {market_unit:,.2f} · sem taxas embarque",
            )
    else:
        m  = safe_int_miles(getattr(offer, "miles", 0))
        prog = getattr(offer, "miles_program", "")
        eq = miles_to_brl(m, airline, prog)
        tx = safe_float(getattr(offer, "taxes_brl", 0))
        custo_real = eq + tx

        if adults > 1:
            tot_real = custo_real * adults
            return (f"R$ {tot_real:,.2f}",
                    f"Total para {adults} passageiros (custo real)",
                    f"Por passageiro: R$ {custo_real:,.2f} · {dt_str}{m:,} milhas (≈ R$ {eq:,.2f}) + R$ {tx:.2f} taxas")
        else:
            return (f"R$ {custo_real:,.2f}",
                    f"{airline} · custo real (milhas + taxas)",
                    f"{dt_str}{m:,} milhas (≈ R$ {eq:,.2f}) + R$ {tx:.2f} taxas")

def _miles_mini_display(offer, adults=1):
    if offer is None: return "—", "—", "—"
    a = str(getattr(offer, "airline", ""))
    m = safe_int_miles(getattr(offer, "miles", 0))
    eq = miles_to_brl(m, a); tx = safe_float(getattr(offer, "taxes_brl", 0))
    custo_real = eq + tx
    dt_str = ""
    if getattr(offer, "outbound", None) and getattr(offer.outbound, "segments", []):
        dt = offer.outbound.segments[0].departure_dt
        if dt: dt_str = f"📅 Partida: {dt.strftime('%d/%m')} · "

    if adults > 1:
        tot_real = custo_real * adults
        return (f"R$ {tot_real:,.2f}",
                f"Para {adults} passageiros (custo real)",
                f"Por pax: R$ {custo_real:,.2f} · {m:,} mi (≈ R$ {eq:,.2f}) + R$ {tx:.2f} tx")
    else:
        return (f"R$ {custo_real:,.2f}",
                f"{m:,} milhas (custo real)",
                f"{dt_str}{m:,} mi (≈ R$ {eq:,.2f}) + R$ {tx:.2f} taxas · {a}")

def _money_mini_display(offer, adults=1):
    """Card mini do Veredito 'Melhor em Dinheiro'.

    Exibe PREÇO DE VENDA (com markup) em destaque, com PREÇO DE MERCADO
    no subtítulo para o vendedor ter ambas as referências."""
    if offer is None: return "—", "—"
    a = str(getattr(offer, "airline", ""))
    market_unit = safe_float(getattr(offer, "equivalent_brl", 0))
    sell_unit = kayak_sell_price(offer)
    markup_lbl = f"{int(round(kayak_markup_pct() * 100))}%"
    dt_str = ""
    if getattr(offer, "outbound", None) and getattr(offer.outbound, "segments", []):
        dt = offer.outbound.segments[0].departure_dt
        if dt: dt_str = f"📅 Partida: {dt.strftime('%d/%m')} · "

    if adults > 1:
        sell_tot = sell_unit * adults
        market_tot = market_unit * adults
        return (
            f"R$ {sell_tot:,.2f}",
            f"Para {adults} pax · Mercado: R$ {market_tot:,.2f} · Markup {markup_lbl}",
        )
    else:
        return (
            f"R$ {sell_unit:,.2f}",
            f"{dt_str}{a} · Kayak · Mercado: R$ {market_unit:,.2f} (markup {markup_lbl})",
        )


# ─── Tab 0 — Melhores Achados ─────────────────────────────────
with tabs[tab_keys.index("verdito")]:
    if incluir_mala:
        miles_ofs = getattr(res, "miles_offers", []) or []
        money_ofs = getattr(res, "money_offers", []) or []
        all_ofs   = miles_ofs + money_ofs
        bo = min(all_ofs, key=lambda o: get_baggage_price(o, True)) if all_ofs else getattr(res, "best_overall", None)
        bm = min(miles_ofs, key=lambda o: get_baggage_price(o, True)) if miles_ofs else getattr(res, "best_miles", None)
        bd = min(money_ofs, key=lambda o: get_baggage_price(o, True)) if money_ofs else getattr(res, "best_money", None)
    else:
        bo = getattr(res, "best_overall", None)
        bm = getattr(res, "best_miles",   None)
        bd = getattr(res, "best_money",   None)

    if getattr(res, "best_depart_date", None):
        flex_dt  = res.best_depart_date.strftime("%d/%m/%Y")
        flex_val = safe_float(res.best_depart_date_equivalent_brl)
        orig_val = (res.date_best_map.get(pi.date_start.isoformat(), 0)
                    if (st.session_state.get("parsed_intent") and st.session_state["parsed_intent"].date_start)
                    else 0)
        if orig_val and orig_val > flex_val:
            eco_str = f" - Economia de R$ {orig_val - flex_val:,.2f}"
            st.success(f"📅 Melhor dia para viajar: **{flex_dt}**{eco_str}")

    bo_val, bo_sub1, bo_sub2 = _offer_main_display(bo, adults)
    bm_eq, bm_miles, bm_det  = _miles_mini_display(bm, adults)
    bd_price, bd_det         = _money_mini_display(bd, adults)

    st.markdown(f"""
<div class="banner-wrap">
  <div class="banner-main">
    <div class="bm-label">★ Melhor achado geral</div>
    <div class="bm-company">{bo_sub1}</div>
    <div class="bm-value-primary">{bo_val}</div>
    <div class="bm-taxes">{bo_sub2}</div>
  </div>
  <div class="banner-mini">
    <div class="bm-mini-label">Melhor em milhas</div>
    <div class="bm-val-main">{bm_eq}</div>
    <div class="bm-val-sub">{bm_miles}</div>
    <div class="bm-detail">{bm_det}</div>
  </div>
  <div class="banner-mini">
    <div class="bm-mini-label">Melhor em dinheiro</div>
    <div class="bm-val-main">{bd_price}</div>
    <div class="bm-detail">{bd_det}</div>
  </div>
</div>
""", unsafe_allow_html=True)

    # ── Ranking por companhia — dinâmico ──
    st.markdown('<div class="sec-title">Ranking por companhia</div>', unsafe_allow_html=True)

    ba = str(getattr(bo, "airline", "")).upper() if bo else ""

    def _best_for(cia_name: str):
        offers = getattr(res, "miles_offers", []) or []
        cands  = [o for o in offers if cia_name.upper() in str(getattr(o, "airline", "")).upper()]
        if not cands:
            return None
        def _real_cost(o):
            m  = safe_int_miles(getattr(o, "miles", 0))
            if m <= 0:
                return 10**18
            a  = str(getattr(o, "airline", ""))
            pr = getattr(o, "miles_program", "")
            return miles_to_brl(m, a, pr) + safe_float(getattr(o, "taxes_brl", 0))
        return min(cands, key=_real_cost)

    def _rhtml(label: str, adults: int = 1):
        css   = _CIA_META.get(label.upper(), {}).get("css", "latam")
        o     = _best_for(label)
        badge = '<span class="rc-best-badge">Melhor geral</span>' if label.upper() in ba else ""
        if o is None:
            return f'<div class="rank-card {css} empty"><div class="rc-header"><span class="rc-company">{label}</span></div><div class="rc-brl">—</div><div class="rc-detail">Sem resultado</div></div>'
        m   = safe_int_miles(getattr(o, "miles", 0))
        prog = getattr(o, "miles_program", "")
        eq  = miles_to_brl(m, label, prog)
        tx  = safe_float(getattr(o, "taxes_brl", 0))
        custo_real = eq + tx
        dur = format_duration(getattr(getattr(o, "outbound", None), "duration_min", 0) or 0)
        esc = int(getattr(o, "stops_out", 0) or 0)
        dt_str = ""
        if getattr(o, "outbound", None) and getattr(o.outbound, "segments", []):
            dt = o.outbound.segments[0].departure_dt
            if dt: dt_str = f"📅 {dt.strftime('%d/%m')} • "
        esc_str = f"{esc} esc" if esc > 0 else "Direto"

        if adults > 1:
            tot_real = custo_real * adults
            return f"""
<div class="rank-card {css}">
  <div class="rc-header"><span class="rc-company">{label}</span>{badge}</div>
  <div class="rc-brl" style="font-size:18px">R$ {tot_real:,.2f} <span style="font-size:11px;color:#6b7a99">({adults}pax)</span></div>
  <div class="rc-miles">R$ {custo_real:,.2f} / pax (mi+taxas)</div>
  <div class="rc-detail">{dt_str}{esc_str} • {dur}<br>{m:,} mi (≈ R$ {eq:,.2f}) + R$ {tx:.2f} tx / pax</div>
</div>"""
        else:
            return f"""
<div class="rank-card {css}">
  <div class="rc-header"><span class="rc-company">{label}</span>{badge}</div>
  <div class="rc-brl">R$ {custo_real:,.2f}</div>
  <div class="rc-miles">{m:,} milhas (custo real)</div>
  <div class="rc-detail">{dt_str}{esc_str} • {dur}<br>{m:,} mi (≈ R$ {eq:,.2f}) + R$ {tx:.2f} taxas</div>
</div>"""

    # Gerar cards apenas das companhias ativas
    cards_html = "".join(_rhtml(cia, adults) for cia in (COMPANHIAS_NACIONAIS + COMPANHIAS_INTERNACIONAIS)
                         if _CIA_ACTIVE.get(cia, False))
    st.markdown(f'<div class="rank-grid">{cards_html}</div>', unsafe_allow_html=True)

    # ── Por que escolher? ──
    if bo:
        a_bo = str(getattr(bo, "airline", "—"))
        if _is_money_offer(bo):
            market_bo = safe_float(getattr(bo, "equivalent_brl", 0))
            sell_bo = kayak_sell_price(bo)
            _markup_lbl = f"{int(round(kayak_markup_pct() * 100))}%"
            st.info(
                f"A melhor opção encontrada foi **{a_bo}** em dinheiro. "
                f"Preço de venda: **R$ {sell_bo:,.2f}** (mercado Kayak: R$ {market_bo:,.2f} + markup {_markup_lbl})."
            )
        else:
            m_bo  = safe_int_miles(getattr(bo, "miles", 0))
            prog_bo = getattr(bo, "miles_program", "")
            eq_bo = miles_to_brl(m_bo, a_bo, prog_bo); tx_bo = safe_float(getattr(bo, "taxes_brl", 0))
            custo_real_bo = eq_bo + tx_bo
            # Comparação justa: milhas vs PREÇO DE VENDA Kayak (com markup) —
            # é o valor que a agência cobraria do cliente em dinheiro.
            sell_bd = kayak_sell_price(bd) if bd else 0
            market_bd = safe_float(getattr(bd, "equivalent_brl", 0)) if bd else 0
            eco = sell_bd - custo_real_bo
            eco_t = (
                f" Comparado ao preço de venda Kayak (R$ {sell_bd:,.2f}; mercado R$ {market_bd:,.2f}), "
                f"milhas economizam R$ {eco:,.2f}."
                if eco > 0 else ""
            )
            if adults > 1:
                tot_real = custo_real_bo * adults
                st.info(f"A melhor opção foi **{a_bo}** em milhas. Custo real total ({adults} pax): **R$ {tot_real:,.2f}** — composto por {m_bo * adults:,} mi (≈ R$ {eq_bo * adults:,.2f}) + R$ {tx_bo * adults:.2f} taxas. (Unitário: R$ {custo_real_bo:,.2f}).{eco_t}")
            else:
                st.info(f"A melhor opção foi **{a_bo}** em milhas. Custo real: **R$ {custo_real_bo:,.2f}** — composto por {m_bo:,} mi (≈ R$ {eq_bo:,.2f}) + R$ {tx_bo:.2f} taxas.{eco_t}")


# ─── Tab Dinheiro ─────────────────────────────────────────────
with tabs[tab_keys.index("dinheiro")]:
    if s_money and getattr(res, "money_offers", None):
        ofs_money = sorted(res.money_offers, key=lambda o: get_baggage_price(o, incluir_mala))
        rows = build_table_rows(ofs_money, incluir_mala, id_prefix="$")
        df   = pd.DataFrame(rows)
        _render_selectable_offers_df(df, COLS, "dinheiro", "df_dinheiro")
        _markup_lbl = f"{int(round(kayak_markup_pct() * 100))}%"
        st.caption(
            f"Preços de **mercado Kayak** (sem markup) — é o que o cliente vê na pesquisa pública. "
            f"O **preço de venda** com markup de {_markup_lbl} aparece no Veredito PcD."
        )
    else:
        st.info("Sem resultados em dinheiro ou fonte desativada.")


# ─── Tabs por companhia (nacionais + internacionais) ──────────
for cia in COMPANHIAS_NACIONAIS + COMPANHIAS_INTERNACIONAIS:
    key = _tab_key(cia)
    if key not in tab_keys:
        continue  # companhia não estava ativa → tab não criada
    with tabs[tab_keys.index(key)]:
        src = _src_name(cia)
        meta = _CIA_META.get(cia, {"prefix": "X"})
        ofs = [o for o in (getattr(res, "miles_offers", []) or []) if source_is(o, src)]
        if not ofs:
            st.info(f"Sem voos {cia}.")
            continue
        ofs = sorted(ofs, key=lambda o: get_baggage_price(o, incluir_mala))
        rows = build_table_rows(ofs, incluir_mala, id_prefix=meta["prefix"])
        df   = pd.DataFrame(rows)
        _render_selectable_offers_df(df, COLS, key, f"df_{key}")


# ─── Tab MCP Award Travel Finder ─────────────────────────────
with tabs[tab_keys.index("mcp_award")]:
    pipeline_mcp = [
        o for o in (getattr(res, "miles_offers", []) or [])
        if source_is(o, "mcp_award")
    ]

    if not pipeline_mcp:
        st.info("Nenhuma oferta encontrada via MCP Award para esta busca.")
    else:
        pipeline_mcp = sorted(pipeline_mcp, key=lambda o: get_baggage_price(o, incluir_mala))
        rows_mcp = build_table_rows(pipeline_mcp, incluir_mala, id_prefix="W")
        df_mcp = pd.DataFrame(rows_mcp)
        _render_selectable_offers_df(df_mcp, COLS, "mcp_award", "df_mcp_award")


# ─── Tab QATAR (MCP PRO) ─────────────────────────────────────
if s_qatar:
    with tabs[tab_keys.index("mcp_qatar")]:
        pipeline_qatar = [
            o for o in (getattr(res, "miles_offers", []) or [])
            if source_is(o, "mcp_qatar")
        ]

        if not pipeline_qatar:
            st.info("Nenhuma oferta encontrada da Qatar para esta busca.")
        else:
            pipeline_qatar = sorted(pipeline_qatar, key=lambda o: get_baggage_price(o, incluir_mala))
            rows_qatar = build_table_rows(pipeline_qatar, incluir_mala, id_prefix="QR")
            df_qatar = pd.DataFrame(rows_qatar)
            _render_selectable_offers_df(df_qatar, COLS, "mcp_qatar", "df_mcp_qatar")


# ─── Tab Ranking Geral ────────────────────────────────────────
with tabs[tab_keys.index("ranking")]:
    rk = getattr(res, "ranked_offers", None)
    if rk:
        rk   = sorted(rk, key=lambda o: get_baggage_price(o, incluir_mala))
        rows = build_table_rows(rk, incluir_mala)
        df   = pd.DataFrame(rows)
        _render_selectable_offers_df(df, COLS, "ranking", "df_ranking")
    else:
        st.info("Sem dados de ranking.")


# ═══════════════════════════════════════════════════════════════
# ITINERÁRIO DETALHADO
# ═══════════════════════════════════════════════════════════════
# Âncora HTML usada pelo scroll automático após clique numa linha de tabela.
st.markdown('<div id="itin-anchor"></div>', unsafe_allow_html=True)
st.markdown("---")

active_tab = st.session_state.get("active_tab") or "verdito"
itin_suffix = _TAB_ITIN_TITLE.get(active_tab, "Todos os voos")
st.markdown(f"### ✈️ Itinerário Detalhado — {itin_suffix}")

all_idx: dict    = {}
pfx_count: dict  = {}

def _add_offer(o, forced_prefix=""):
    a   = str(getattr(o, "airline", "")).upper()
    src = str(getattr(getattr(o, "source", None), "value", "") or "").upper()
    pfx = forced_prefix or _id_prefix(a, src)
    n   = pfx_count.get(pfx, 0) + 1
    pfx_count[pfx] = n
    all_idx[f"{pfx}{n}"] = o

for o in (getattr(res, "money_offers", []) or []):
    _add_offer(o, "$")
for o in (getattr(res, "miles_offers", []) or []):
    _add_offer(o)
# MCP pipeline offers (se o pipeline trouxer MCP_AWARD)
for o in [o for o in (getattr(res, "miles_offers", []) or []) if source_is(o, "mcp_award")]:
    pass  # ja indexado acima; MCP fixture nao e UnifiedOffer, exibido apenas na aba MCP

if not all_idx:
    st.info("Nenhum voo disponível para detalhar.")
    st.stop()

def _itin_lbl(fid, o):
    a = str(getattr(o, "airline", "?"))
    if _is_money_offer(o):
        p = safe_float(getattr(o, "equivalent_brl", 0))
        return f"{fid} — {a} | R$ {p:,.2f} (dinheiro)"
    m  = safe_int_miles(getattr(o, "miles", 0))
    prog = getattr(o, "miles_program", "")
    eq = miles_to_brl(m, a, prog)
    tx = safe_float(getattr(o, "taxes_brl", 0))
    custo_real = eq + tx
    return f"{fid} — {a} | R$ {custo_real:,.2f} ({m:,} mi + R$ {tx:.2f} tx)"

# Filtragem do selectbox por aba ativa (Problema 4).
_allowed_prefixes = _TAB_PREFIXES.get(active_tab)  # None → libera tudo
if _allowed_prefixes is None:
    filtered_keys = list(all_idx.keys())
else:
    filtered_keys = [
        fid for fid in all_idx.keys()
        if _id_alpha_prefix(fid) in _allowed_prefixes
    ]
# Fallback: se o filtro zerar, libera tudo (evita selectbox vazio).
if not filtered_keys:
    filtered_keys = list(all_idx.keys())

def _sort_fid(k):
    pfx = _id_alpha_prefix(k)
    try:
        num = int(k[len(pfx):])
    except Exception:
        num = 0
    return (pfx, num)

options_sorted = sorted(filtered_keys, key=_sort_fid)

# Pré-seleciona o voo clicado na tabela (Problema 3).
# Streamlit persiste o valor do selectbox via `key`; aqui sincronizamos a
# session_state ANTES do widget renderizar para que (a) o clique de linha
# sobreponha a seleção anterior e (b) filtros de aba que escondem o valor
# atual caiam de volta para o primeiro item válido.
preselect = st.session_state.get("selected_flight_id")
if st.session_state.get("_scroll_to_itin") and preselect in options_sorted:
    st.session_state["itin_selectbox"] = preselect
if st.session_state.get("itin_selectbox") not in options_sorted:
    st.session_state["itin_selectbox"] = options_sorted[0] if options_sorted else None

sel = st.selectbox(
    "Selecione o voo pelo ID",
    options=options_sorted,
    format_func=lambda fid: _itin_lbl(fid, all_idx[fid]),
    key="itin_selectbox",
)
off = all_idx[sel]

# Scroll automático até o itinerário após clique numa linha de tabela.
if st.session_state.pop("_scroll_to_itin", False):
    from streamlit.components.v1 import html as _components_html
    _components_html(
        """<script>
        window.parent.document.getElementById('itin-anchor')
          ?.scrollIntoView({behavior:'smooth', block:'start'});
        </script>""",
        height=0,
    )

col_out, col_in = st.columns(2)
with col_out:
    if hasattr(off, "outbound") and off.outbound:
        render_itin_card(off, "outbound")
    else:
        st.info("Sem dados de ida.")
with col_in:
    if hasattr(off, "inbound") and off.inbound:
        render_itin_card(off, "inbound")

if incluir_mala:
    st.warning("🎒 Preços já consideram acréscimo de bagagem despachada.")

st.caption("PcD v2.3 | Agente de Cotação · PassagensComDesconto")
