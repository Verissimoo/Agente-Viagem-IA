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


# ═══════════════════════════════════════════════════════════════
# PAGE CONFIG
# ═══════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Agente de Cotação PcD", page_icon="✈️",
    layout="wide", initial_sidebar_state="collapsed",
)

inject_styles()
render_topbar()


# ── Engrenagem / Configurações ──
col_gear, _ = st.columns([1, 14])
with col_gear:
    with st.popover("⚙️"):
        st.markdown("**Configurações**")
        use_fixtures = st.toggle("Dados Estáticos (Mock)", value=False)

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

        st.markdown("**Parâmetros:**")
        top_n = st.slider("Qtd. resultados", 1, 15, 5)

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

# Mapa de checkboxes por companhia BuscaMilhas
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

# ── Toggle Cotação Inteligente ──
smart_mode = st.toggle(
    "🧠 Cotação Inteligente",
    value=False,
    help="Analisa ±4 dias automaticamente, identifica o dia mais barato e os melhores programas de milhas para a rota.",
)

if buscar and prompt_text:
    with st.spinner("Analisando pedido e buscando voos..."):
        intent = parse_intent_ptbr(prompt_text, use_llm=use_llm)
        st.session_state["parsed_intent"] = intent
        if st.session_state.get("v_flex") is not None:
            intent.flex_days = st.session_state["v_flex"]
            if intent.flex_days > 0 and intent.flex_mode == "none":
                intent.flex_mode = "plusminus"

        # Cotação Inteligente roda em paralelo ao pipeline normal.
        from concurrent.futures import ThreadPoolExecutor
        from pcd.agents.smart_quote import SmartQuoteAgent

        smart_future = None
        smart_progress: list[str] = []
        if smart_mode and intent.origin_iata and intent.destination_iata and (intent.date_start or intent.depart_date_from):
            _smart_executor = ThreadPoolExecutor(max_workers=1)
            _smart_pax = getattr(intent, "adults", 1) or 1
            _smart_origin = intent.origin_iata
            _smart_destination = intent.destination_iata
            _smart_date_req = intent.date_start or intent.depart_date_from
            _smart_date_ret = intent.date_return

            def _capture(msg: str):
                smart_progress.append(msg)

            smart_future = _smart_executor.submit(
                SmartQuoteAgent().run,
                _smart_origin, _smart_destination, _smart_date_req,
                _smart_pax, _smart_date_ret, _capture,
            )

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
        )
        st.session_state["pipeline_result"] = res

        # Aguarda a Cotação Inteligente (que rodou em paralelo). Se falhar,
        # degrada graciosamente — só o pipeline normal aparece.
        if smart_future is not None:
            try:
                smart_result = smart_future.result(timeout=180)
            except Exception as e:
                smart_result = None
                smart_progress.append(f"⚠️ Cotação Inteligente falhou: {str(e)[:160]}")
            st.session_state["smart_result"] = smart_result
            st.session_state["smart_progress"] = smart_progress
        else:
            st.session_state["smart_result"] = None
            st.session_state["smart_progress"] = []

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
if "pipeline_result" not in st.session_state:
    st.stop()

res          = st.session_state["pipeline_result"]
incluir_mala = st.session_state.get("v_bagagem", False)

if getattr(res, "direct_filter_warning", None):
    st.warning(res.direct_filter_warning)

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

.smart-card{background:#fff;border:1px solid #dde3ef;border-radius:12px;
    padding:18px 22px;box-shadow:0 1px 3px rgba(13,43,110,.04);margin-bottom:16px;}
.smart-card-title{font-size:13px;font-weight:600;color:#6b7a99;
    text-transform:uppercase;letter-spacing:.06em;margin-bottom:14px;}

/* ── Calendário ── */
.price-calendar{display:flex;align-items:flex-end;justify-content:space-between;
    gap:8px;padding:10px 4px 0 4px;min-height:240px;}
.cal-col{flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;
    cursor:default;position:relative;}
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


def _render_calendar_html(cal: dict, carriers_cal: dict, anchor_iso: str, requested_iso: str) -> str:
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
            # Barras uniformes (~70% da altura máxima) — evita ilusão de variação
            # quando a normalização amplificaria diferenças de centavos.
            h = 110.0
        else:
            h = H_MIN + (price - pmin) / span * (H_MAX - H_MIN)
        kind = "normal"
        badge_html = '<div class="cal-badge spacer">·</div>'
        if iso == anchor_iso:
            kind = "anchor"
            badge_html = '<div class="cal-badge">Melhor dia ✓</div>'
        elif iso == requested_iso:
            kind = "req"
            badge_html = '<div class="cal-badge req">Sua data</div>'

        from pcd.agents.smart_quote import airline_display
        carriers_for_day = carriers_cal.get(iso) or []
        carriers_str = ", ".join(airline_display(c)["name"] for c in carriers_for_day) or "—"
        tooltip = f"R$ {price:,.2f} · {carriers_str}".replace(",", "X").replace(".", ",").replace("X", ".")

        cols_html.append(f"""
<div class="cal-col">
  <span class="cal-tooltip">{tooltip}</span>
  {badge_html}
  <div class="cal-bar-wrap"><div class="cal-bar {kind}" style="height:{h:.0f}px"></div></div>
  <div class="cal-date">{_fmt_date_short(iso)}</div>
  <div class="cal-price">R$ {price:,.0f}</div>
</div>""")

    return f"""
<div class="smart-card">
  <div class="smart-card-title">📅 Calendário de preços (±4 dias)</div>
  {uniform_banner}
  <div class="price-calendar">{''.join(cols_html)}</div>
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


def _render_smart_header_html() -> str:
    return """
<div class="smart-header">
  <div class="smart-icon">🧠</div>
  <div>
    <div class="smart-title">Cotação Inteligente</div>
    <div class="smart-sub">Análise automática de ±4 dias · Mapeamento de programas · Melhor data identificada</div>
  </div>
</div>"""


def _render_smart_quote_section():
    smart_result = st.session_state.get("smart_result")
    smart_progress_msgs = st.session_state.get("smart_progress") or []

    if smart_result is None and not smart_progress_msgs:
        return

    st.markdown(_SMART_CSS, unsafe_allow_html=True)
    st.markdown(_render_smart_header_html(), unsafe_allow_html=True)

    if smart_progress_msgs:
        with st.status("Agentes executados", expanded=False, state="complete"):
            for msg in smart_progress_msgs:
                st.write(msg)

    if smart_result is None:
        st.warning("A Cotação Inteligente não pôde ser concluída. Veja a busca normal abaixo.")
        st.divider()
        return

    if smart_result.notes:
        for note in smart_result.notes:
            st.info(note)

    cal = smart_result.price_calendar or {}
    anchor_iso = smart_result.anchor_date or ""
    requested_iso = smart_result.date_requested or ""
    carriers_cal = smart_result.calendar_carriers or {}

    # ── Calendário de preços ──
    if cal:
        st.markdown(
            _render_calendar_html(cal, carriers_cal, anchor_iso, requested_iso),
            unsafe_allow_html=True,
        )
        # Cards de métricas
        st.markdown(
            _render_metrics_cards_html(
                cal, anchor_iso, requested_iso,
                smart_result.savings_vs_requested,
                smart_result.date_is_already_best,
            ),
            unsafe_allow_html=True,
        )

    # ── Mensagem chave ──
    _cal_uniform = _is_calendar_uniform(list(cal.values())) if cal else False
    if _cal_uniform:
        st.markdown(
            f'<div class="smart-message green">✅ <b>Preços estáveis no período.</b> '
            f'A tarifa mais barata é praticamente a mesma nos 9 dias — você pode '
            f'manter {_fmt_date_long(requested_iso)} sem perda financeira.</div>',
            unsafe_allow_html=True,
        )
    elif smart_result.date_is_already_best:
        st.markdown(
            f'<div class="smart-message green">✅ <b>Ótima escolha!</b> A data solicitada '
            f'({_fmt_date_long(requested_iso)}) já é a mais barata do período analisado.</div>',
            unsafe_allow_html=True,
        )
    elif anchor_iso and smart_result.savings_vs_requested > 0:
        st.markdown(
            f'<div class="smart-message green">✅ <b>Melhor dia: {_fmt_date_long(anchor_iso)}</b> — '
            f'R$ {smart_result.savings_vs_requested:,.2f} mais barato que '
            f'{_fmt_date_long(requested_iso)} solicitado.</div>',
            unsafe_allow_html=True,
        )

    # ── Companhias na data âncora (chips) ──
    if smart_result.anchor_carriers:
        st.markdown(f"""
<div class="smart-card">
  <div class="smart-card-title">✈️ Companhias na data âncora</div>
  {_render_carrier_chips(smart_result.anchor_carriers)}
</div>""", unsafe_allow_html=True)

    # ── Programas relevantes (cards) ──
    rp = smart_result.relevant_programs or {}
    has_any_coverage = any(rp.get(k) for k in ("SMILES", "LATAM_PASS", "AZUL_FIDELIDADE"))
    if has_any_coverage:
        st.markdown(f"""
<div class="smart-card">
  <div class="smart-card-title">💎 Programas relevantes para essa rota</div>
  {_render_programs_cards_html(rp)}
</div>""", unsafe_allow_html=True)

    # ── Cotação de milhas na data âncora ──
    if smart_result.miles_offers:
        st.markdown('<div class="smart-card-title" style="margin:18px 0 8px 0">💎 Cotação de milhas na data âncora</div>', unsafe_allow_html=True)
        for offer in smart_result.miles_offers:
            with st.expander(f"{offer['label']} — {offer['elapsed_ms']:.0f}ms"):
                if offer.get("error"):
                    st.warning(f"Falha: {offer['error']}")
                else:
                    raw = offer.get("raw") or {}
                    n_trechos = len((raw.get("Trechos") or []))
                    st.write(f"Resposta da API recebida — {n_trechos} bloco(s) de trechos.")
                    st.caption("Ofertas detalhadas aparecem nas tabs normais abaixo, na coluna do programa correspondente.")

    st.divider()


_render_smart_quote_section()


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
        unit_price = safe_float(getattr(offer, "equivalent_brl", 0))
        total_price = unit_price * adults
        if adults > 1:
            return f"R$ {total_price:,.2f}", f"Total para {adults} passageiros", f"Por passageiro: R$ {unit_price:,.2f} · {dt_str}{airline} Kayak"
        else:
            return f"R$ {unit_price:,.2f}", f"{airline} · Kayak · em dinheiro", f"{dt_str}Valor s/ taxa pode variar"
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
    if offer is None: return "—", "—"
    a = str(getattr(offer, "airline", ""))
    p = safe_float(getattr(offer, "equivalent_brl", 0))
    dt_str = ""
    if getattr(offer, "outbound", None) and getattr(offer.outbound, "segments", []):
        dt = offer.outbound.segments[0].departure_dt
        if dt: dt_str = f"📅 Partida: {dt.strftime('%d/%m')} · "
        
    if adults > 1:
        tot_p = p * adults
        return f"R$ {tot_p:,.2f}", f"Para {adults} pax (Unit: R$ {p:,.2f})"
    else:
        return f"R$ {p:,.2f}", f"{dt_str}{a} · Kayak"


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
        return min(cands, key=lambda o: safe_int_miles(getattr(o, "miles", 0)) or 10**18) if cands else None

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
            p = safe_float(getattr(bo, "equivalent_brl", 0))
            st.info(f"A melhor opção encontrada foi **{a_bo}** em dinheiro por **R$ {p:,.2f}**.")
        else:
            m_bo  = safe_int_miles(getattr(bo, "miles", 0))
            prog_bo = getattr(bo, "miles_program", "")
            eq_bo = miles_to_brl(m_bo, a_bo, prog_bo); tx_bo = safe_float(getattr(bo, "taxes_brl", 0))
            custo_real_bo = eq_bo + tx_bo
            bdo_p = safe_float(getattr(bd, "equivalent_brl", 0)) if bd else 0
            eco   = bdo_p - custo_real_bo
            eco_t = f" Comparado ao melhor em dinheiro (R$ {bdo_p:,.2f}), economia real de R$ {eco:,.2f}." if eco > 0 else ""
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
        st.dataframe(df[[c for c in COLS if c in df.columns]], use_container_width=True, hide_index=True)
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
        st.dataframe(df[[c for c in COLS if c in df.columns]], use_container_width=True, hide_index=True)


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
        st.dataframe(df_mcp[[c for c in COLS if c in df_mcp.columns]], use_container_width=True, hide_index=True)


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
            st.dataframe(df_qatar[[c for c in COLS if c in df_qatar.columns]], use_container_width=True, hide_index=True)


# ─── Tab Ranking Geral ────────────────────────────────────────
with tabs[tab_keys.index("ranking")]:
    rk = getattr(res, "ranked_offers", None)
    if rk:
        rk   = sorted(rk, key=lambda o: get_baggage_price(o, incluir_mala))
        rows = build_table_rows(rk, incluir_mala)
        df   = pd.DataFrame(rows)
        st.dataframe(df[[c for c in COLS if c in df.columns]], use_container_width=True, hide_index=True)
    else:
        st.info("Sem dados de ranking.")


# ═══════════════════════════════════════════════════════════════
# ITINERÁRIO DETALHADO
# ═══════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown("### ✈️ Itinerário Detalhado")

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

sel = st.selectbox(
    "Selecione o voo pelo ID",
    options=sorted(all_idx.keys(), key=lambda k: (k.rstrip("0123456789"), int(k.lstrip("$LGABTIAPN") or 0) if k.lstrip("$LGABTIAPN").isdigit() else 0)),
    format_func=lambda fid: _itin_lbl(fid, all_idx[fid]),
)
off = all_idx[sel]

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
