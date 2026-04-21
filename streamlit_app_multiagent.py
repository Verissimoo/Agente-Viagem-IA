import streamlit as st
import pandas as pd
from datetime import date
from typing import List
from pcd.run import run_pipeline
from pcd.core.schema import TripType
from pcd.nlp.intent_parser import parse_intent_ptbr
from miles_app.buscamilhas_client import COMPANHIAS_NACIONAIS, COMPANHIAS_INTERNACIONAIS

# ═══════════════════════════════════════════════════════════════
# TAXAS DE CONVERSÃO MILHAS → BRL  (por companhia)
# ═══════════════════════════════════════════════════════════════
RATES = {
    "LATAM":     0.0285,
    "GOL":       0.0200,
    "AZUL":      0.0200,
    "TAP":       0.0220,
    "AMERICAN AIRLINES": 0.0220,
    "INTERLINE": 0.0200,
    "DEFAULT":   0.0210,
}

# Metadados visuais de cada companhia (src = valor exato do SourceType)
_CIA_META = {
    "LATAM":     {"emoji": "💎", "css": "latam",     "prefix": "L",  "src": "buscamilhas_latam"},
    "GOL":       {"emoji": "🟠", "css": "gol",       "prefix": "G",  "src": "buscamilhas_gol"},
    "AZUL":      {"emoji": "🔵", "css": "azul",      "prefix": "A",  "src": "buscamilhas_azul"},
    "TAP":       {"emoji": "🟢", "css": "tap",       "prefix": "TP", "src": "buscamilhas_tap"},
    "AMERICAN AIRLINES": {"emoji": "🦅", "css": "american",  "prefix": "AA", "src": "buscamilhas_american"},
    "INTERLINE": {"emoji": "🌐", "css": "interline", "prefix": "IN", "src": "buscamilhas_interline"},
}

# Companhias internacionais sem acréscimo de bagagem (já inclusa na tarifa de milhas)
_INTERNACIONAIS_SEM_BAGAGEM_EXTRA = {"TAP", "AMERICAN AIRLINES", "INTERLINE"}


def _src_name(cia: str) -> str:
    """Retorna o valor exato do SourceType para a companhia."""
    return _CIA_META.get(cia, {}).get("src", f"buscamilhas_{cia.lower()}")


def _tab_key(cia: str) -> str:
    """Chave única da tab da companhia (sem espaços)."""
    return f"cia_{cia.lower().replace(' ', '_')}"


def miles_to_brl(miles, airline: str = "") -> float:
    try:
        a = str(airline).upper()
        key = "DEFAULT"
        for k in RATES:
            if k != "DEFAULT" and k in a:
                key = k
                break
        return float(miles) * RATES.get(key, RATES["DEFAULT"])
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════════════════════
# HELPERS GERAIS
# ═══════════════════════════════════════════════════════════════

def format_duration(min_total) -> str:
    try:
        v = int(min_total or 0)
    except Exception:
        return "—"
    if v <= 0:
        return "0m"
    h, m = divmod(v, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"

def safe_int_miles(val) -> int:
    try:
        if val is None or str(val).lower() in ("none", "", "—"):
            return 0
        return int(float(str(val).replace(",", "")))
    except Exception:
        return 0

def safe_float(val) -> float:
    try:
        return float(val) if val is not None else 0.0
    except Exception:
        return 0.0

def get_baggage_price(offer, include_baggage: bool) -> float:
    base = safe_float(getattr(offer, "equivalent_brl", 0))
    if not include_baggage:
        return base
    a = str(getattr(offer, "airline", "")).upper()

    # Internacionais já incluem bagagem — sem acréscimo
    for cia in _INTERNACIONAIS_SEM_BAGAGEM_EXTRA:
        if cia in a:
            return base

    if "GOL"  in a: return base + 130.0
    if "AZUL" in a: return base + 160.0

    if "LATAM" in a:
        m_out = getattr(offer, "baggage_miles_out", None)
        m_in  = getattr(offer, "baggage_miles_in",  None)
        has_bag_out = m_out is not None
        has_bag_in  = m_in  is not None
        if has_bag_out or has_bag_in:
            val_out = safe_int_miles(m_out) if has_bag_out else safe_int_miles(getattr(offer, "miles_out", 0) or getattr(offer, "miles", 0))
            val_in  = 0
            trip_type_val = getattr(getattr(offer, "trip_type", None), "name", str(getattr(offer, "trip_type", "")))
            if "ROUNDTRIP" in trip_type_val.upper():
                val_in = safe_int_miles(m_in) if has_bag_in else safe_int_miles(getattr(offer, "miles_in", 0) or 0)
            total_m = val_out + val_in
            eq = miles_to_brl(total_m, "LATAM")
            return eq + safe_float(getattr(offer, "taxes_brl", 0))

    return base

def source_is(offer, name: str) -> bool:
    if not offer or not hasattr(offer, "source"):
        return False
    s = offer.source
    return str(s.value if hasattr(s, "value") else s).lower() == name.lower()

def _id_prefix(airline: str, source: str = "") -> str:
    a = str(airline).upper(); s = str(source).upper()
    if "LATAM"     in a or "LATAM"     in s: return "L"
    if "GOL"       in a or "GOL"       in s: return "G"
    if "AZUL"      in a or "AZUL"      in s: return "A"
    if "IBERIA"    in a or "IBERIA"    in s: return "IB"
    if "TAP"       in a or "TAP"       in s: return "TP"
    if "AMERICAN"  in a or "AMERICAN"  in s: return "AA"
    if "INTERLINE" in a or "INTERLINE" in s: return "IN"
    return "X"

def build_table_rows(offers, include_baggage=False, id_prefix=""):
    import streamlit as st
    pi = st.session_state.get("parsed_intent")
    adults = getattr(pi, "adults", 1) if pi else 1
    
    rows = []
    for i, o in enumerate(offers):
        val_mala = get_baggage_price(o, True)
        airline  = str(getattr(o, "airline", ""))
        src_val  = str(getattr(getattr(o, "source", None), "value", "") or "")
        prefix   = id_prefix or _id_prefix(airline, src_val)
        fid      = f"{prefix}{i+1}"

        # ── Escala IDA ──
        segs_out_raw = getattr(o, "outbound_segments_raw", None) or []
        if segs_out_raw and len(segs_out_raw) > 1:
            local_out = ", ".join(getattr(s, "destination", "") for s in segs_out_raw[:-1] if getattr(s, "destination", ""))
        elif hasattr(o, "outbound") and o.outbound and len(o.outbound.segments) > 1:
            local_out = ", ".join(s.destination for s in o.outbound.segments[:-1])
        else:
            local_out = "Direto"

        # ── IDA ──
        if hasattr(o, "outbound") and o.outbound and o.outbound.segments:
            fs = o.outbound.segments[0]; ls = o.outbound.segments[-1]
            m_out = safe_int_miles(o.miles_out if o.miles_out is not None else o.miles)
            eq_unit = miles_to_brl(m_out, airline)
            
            r_out = {
                "ID": fid, "Companhia": airline, "Trecho": "IDA",
                "Data":    fs.departure_dt.strftime("%d/%m/%Y"),
                "Saída":   fs.departure_dt.strftime("%H:%M"),
                "Chegada": ls.arrival_dt.strftime("%H:%M"),
                "Milhas":      f"{m_out:,}" if m_out else "—",
                "Equiv. BRL":  f"R$ {eq_unit:.2f}" if m_out else "—",
            }
            if adults > 1:
                r_out[f"Total ({adults}pax)"] = f"R$ {eq_unit * adults:,.2f}" if m_out else "—"
                
            r_out.update({
                "Taxas":       f"R$ {safe_float(o.taxes_brl_out if o.taxes_brl_out is not None else o.taxes_brl):.2f}",
                "Preço Final": f"R$ {safe_float(o.equivalent_brl):.2f}",
                "Valor c/ Mala": f"R$ {val_mala:.2f}",
                "Duração":  format_duration(o.outbound.duration_min),
                "Escalas":  int(getattr(o, "stops_out", 0) or 0),
                "Local Escala": local_out,
            })
            rows.append(r_out)

        # ── Escala VOLTA ──
        segs_in_raw = getattr(o, "inbound_segments_raw", None) or []
        if segs_in_raw and len(segs_in_raw) > 1:
            local_in = ", ".join(getattr(s, "destination", "") for s in segs_in_raw[:-1] if getattr(s, "destination", ""))
        elif hasattr(o, "inbound") and o.inbound and o.inbound.segments and len(o.inbound.segments) > 1:
            local_in = ", ".join(s.destination for s in o.inbound.segments[:-1])
        else:
            local_in = "Direto"

        # ── VOLTA ──
        if (hasattr(o, "trip_type") and o.trip_type == TripType.ROUNDTRIP
                and hasattr(o, "inbound") and o.inbound and o.inbound.segments):
            fi = o.inbound.segments[0]; li = o.inbound.segments[-1]
            m_in = safe_int_miles(o.miles_in if o.miles_in is not None else o.miles)
            eq_unit = miles_to_brl(m_in, airline)
            
            r_in = {
                "ID": fid, "Companhia": airline, "Trecho": "VOLTA",
                "Data":    fi.departure_dt.strftime("%d/%m/%Y"),
                "Saída":   fi.departure_dt.strftime("%H:%M"),
                "Chegada": li.arrival_dt.strftime("%H:%M"),
                "Milhas":      f"{m_in:,}" if m_in else "—",
                "Equiv. BRL":  f"R$ {eq_unit:.2f}" if m_in else "—",
            }
            if adults > 1:
                r_in[f"Total ({adults}pax)"] = f"R$ {eq_unit * adults:,.2f}" if m_in else "—"
                
            r_in.update({
                "Taxas":       f"R$ {safe_float(o.taxes_brl_in if o.taxes_brl_in is not None else o.taxes_brl):.2f}",
                "Preço Final": f"R$ {safe_float(o.equivalent_brl):.2f}",
                "Valor c/ Mala": f"R$ {val_mala:.2f}",
                "Duração":  format_duration(o.inbound.duration_min),
                "Escalas":  int(getattr(o, "stops_in", 0) or 0),
                "Local Escala": local_in,
            })
            rows.append(r_in)
            
    return rows


# ═══════════════════════════════════════════════════════════════
# RENDER ITINERÁRIO — recebe o offer completo, não o itinerary
# ═══════════════════════════════════════════════════════════════

def render_itin_card(offer, direction: str = "outbound"):
    is_volta  = direction == "inbound"
    hcls      = "itin-header volta" if is_volta else "itin-header"
    lbl       = "Volta" if is_volta else "Ida"
    raw_key   = "inbound_segments_raw" if is_volta else "outbound_segments_raw"

    segs_raw  = getattr(offer, raw_key, None) or []
    itin      = offer.inbound if is_volta else offer.outbound
    if not itin:
        return

    # ── metadados ──
    if segs_raw:
        fr, lr = segs_raw[0], segs_raw[-1]
        orig = getattr(fr, "origin", ""); dest = getattr(lr, "destination", "")
        dep_dt = getattr(fr, "departure_dt", None); arr_dt = getattr(lr, "arrival_dt", None)
        dep_s  = dep_dt.strftime("%H:%M")    if dep_dt else "—"
        arr_s  = arr_dt.strftime("%H:%M")    if arr_dt else "—"
        ddate  = dep_dt.strftime("%d/%m/%Y") if dep_dt else "—"
        tot_min = int((arr_dt - dep_dt).total_seconds() // 60) if arr_dt and dep_dt else 0
        tot    = format_duration(tot_min)
        nstops = len(segs_raw) - 1
    else:
        ss = itin.segments
        fs, ls = ss[0], ss[-1]
        orig = fs.origin; dest = ls.destination
        dep_s  = fs.departure_dt.strftime("%H:%M")
        arr_s  = ls.arrival_dt.strftime("%H:%M")
        ddate  = fs.departure_dt.strftime("%d/%m/%Y")
        tot    = format_duration(itin.duration_min)
        nstops = len(ss) - 1

    slbl = "Direto" if nstops == 0 else f"{nstops} escala(s)"

    st.markdown(f"""
<div class="itin-card">
  <div class="{hcls}">
    <div>
      <div style="font-size:11px;opacity:.7;text-transform:uppercase;letter-spacing:.05em">{lbl}</div>
      <div class="ih-route">{orig} → {dest}</div>
    </div>
    <div class="ih-meta">{ddate} · {slbl} · {tot}</div>
  </div>
  <div class="itin-body">
    <div class="itin-timeline">
      <div class="itin-ap"><div class="ap-code">{orig}</div><div class="ap-time">{dep_s}</div></div>
      <div class="itin-line">
        <div class="itin-dur">{tot}</div>
        <div class="itin-bar"></div>
        <div class="itin-stops-badge">{slbl}</div>
      </div>
      <div class="itin-ap" style="text-align:right"><div class="ap-code">{dest}</div><div class="ap-time">{arr_s}</div></div>
    </div>
""", unsafe_allow_html=True)

    # ── segmentos ──
    if segs_raw:
        for idx, seg in enumerate(segs_raw):
            dep = getattr(seg, "departure_dt", None); arr = getattr(seg, "arrival_dt", None)
            ds  = dep.strftime("%H:%M") if dep else "—"
            as_ = arr.strftime("%H:%M") if arr else "—"
            seg_dur_min = int((arr - dep).total_seconds() // 60) if arr and dep else 0
            dur = format_duration(seg_dur_min)
            carrier = getattr(seg, "carrier", ""); flt = getattr(seg, "flight_number", "") or carrier
            o_s = getattr(seg, "origin", ""); d_s = getattr(seg, "destination", "")
            st.markdown(f"""
<div class="seg-row">
  <div class="seg-flt">{flt}</div>
  <div class="seg-route">{o_s} → {d_s}</div>
  <div class="seg-times">{ds} → {as_}</div>
  <div class="seg-dur">{dur}</div>
  <div class="seg-carrier">{carrier}</div>
</div>""", unsafe_allow_html=True)
            if idx < len(segs_raw) - 1:
                nxt = segs_raw[idx + 1]
                nxt_dep = getattr(nxt, "departure_dt", None)
                if arr and nxt_dep:
                    lv_min = int((nxt_dep - arr).total_seconds() // 60)
                    if lv_min > 0:
                        st.markdown(f'<div class="layover-banner">🛑 Conexão em {d_s}: {format_duration(lv_min)}</div>',
                                    unsafe_allow_html=True)
    else:
        for idx, seg in enumerate(itin.segments):
            ds_ = seg.departure_dt.strftime("%H:%M"); as__ = seg.arrival_dt.strftime("%H:%M")
            st.markdown(f"""
<div class="seg-row">
  <div class="seg-flt">{seg.carrier} {seg.flight_number or ''}</div>
  <div class="seg-route">{seg.origin} → {seg.destination}</div>
  <div class="seg-times">{ds_} → {as__}</div>
</div>""", unsafe_allow_html=True)
            if idx < len(itin.segments) - 1:
                nxt = itin.segments[idx+1]
                lv  = int((nxt.departure_dt - seg.arrival_dt).total_seconds() // 60)
                if lv > 0:
                    st.markdown(f'<div class="layover-banner">🛑 Conexão em {seg.destination}: {format_duration(lv)}</div>',
                                unsafe_allow_html=True)

    st.markdown("</div></div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════
# PAGE CONFIG
# ═══════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Agente de Cotação PcD", page_icon="✈️",
    layout="wide", initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
:root{--pcd-blue:#1a56a0;--pcd-blue-dark:#0d2b6e;--pcd-blue-light:#e8f0fb;
      --pcd-red:#c0392b;--pcd-gray:#f5f6fa;--pcd-border:#dde3ef;
      --pcd-text:#1a2236;--pcd-muted:#6b7a99;
      --pcd-green:#1a7a4a;--pcd-green-light:#eaf4ef;}
[data-testid="stSidebar"]{display:none!important;}
section[data-testid="stSidebarContent"]{display:none!important;}
.block-container{padding-top:0!important;padding-bottom:2rem!important;}
.stApp{background-color:var(--pcd-gray)!important;}
.pcd-topbar{background:var(--pcd-blue-dark);padding:0 24px;height:56px;
    display:flex;align-items:center;justify-content:space-between;
    margin:-1rem -4rem 1.5rem -4rem;position:sticky;top:0;z-index:100;}
.pcd-logo-name{color:white;font-size:16px;font-weight:600;}
.pcd-logo-sub{color:rgba(255,255,255,.55);font-size:11px;}
.stTextArea textarea{border:2px solid var(--pcd-blue)!important;border-radius:10px!important;font-size:15px!important;}
.stTextArea textarea:focus{box-shadow:0 0 0 3px rgba(26,86,160,.15)!important;}
.stButton>button{background-color:var(--pcd-red)!important;color:white!important;
    font-weight:600!important;border-radius:10px!important;border:none!important;
    font-size:15px!important;padding:.65rem 2rem!important;}
.stButton>button:hover{background-color:#a93226!important;}
.stTabs [data-baseweb="tab-list"]{gap:4px;border-bottom:2px solid var(--pcd-border)!important;background:transparent!important;}
.stTabs [data-baseweb="tab"]{font-size:13px!important;font-weight:500!important;
    color:var(--pcd-muted)!important;border-radius:8px 8px 0 0!important;
    padding:8px 16px!important;background:transparent!important;border:none!important;}
.stTabs [aria-selected="true"]{color:var(--pcd-blue)!important;
    border-bottom:2px solid var(--pcd-blue)!important;background:var(--pcd-blue-light)!important;}
/* banner */
.banner-wrap{background:var(--pcd-blue-dark);border-radius:12px;padding:16px 20px;
    display:flex;gap:12px;flex-wrap:wrap;margin-bottom:1rem;}
.banner-main{flex:1.6;min-width:220px;background:rgba(255,255,255,.97);
    border-radius:8px;padding:16px 20px;border:2px solid rgba(255,255,255,.8);}
.bm-label{font-size:11px;color:var(--pcd-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;}
.bm-company{font-size:13px;font-weight:600;color:var(--pcd-text);margin-bottom:4px;}
.bm-value-primary{font-size:28px;font-weight:800;color:var(--pcd-red);line-height:1.1;}
.bm-value-secondary{font-size:14px;font-weight:600;color:var(--pcd-blue);margin-top:4px;}
.bm-taxes{font-size:12px;color:var(--pcd-muted);margin-top:3px;}
.banner-mini{flex:1;min-width:160px;background:rgba(255,255,255,.1);
    border-radius:8px;padding:14px 16px;border:1px solid rgba(255,255,255,.15);}
.bm-mini-label{font-size:10px;color:rgba(255,255,255,.65);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;}
.bm-val-main{font-size:22px;font-weight:700;color:white;line-height:1.1;}
.bm-val-sub{font-size:12px;color:rgba(255,255,255,.7);margin-top:4px;font-weight:500;}
.bm-detail{font-size:11px;color:rgba(255,255,255,.5);margin-top:2px;}
/* ranking dinâmico */
.rank-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-bottom:1rem;}
.rank-card{background:white;border-radius:10px;border:1px solid var(--pcd-border);padding:14px 16px;position:relative;overflow:hidden;}
.rank-card::before{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;}
.rank-card.latam::before{background:var(--pcd-red);}
.rank-card.gol::before{background:#ff6b00;}
.rank-card.azul::before{background:#0032a0;}
.rank-card.tap::before{background:#00b761;}
.rank-card.iberia::before{background:#c8102e;}
.rank-card.american::before{background:#0078d2;}
.rank-card.interline::before{background:#6c3483;}
.rc-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;}
.rc-company{font-size:13px;font-weight:600;color:var(--pcd-text);}
.rc-best-badge{font-size:10px;padding:2px 8px;border-radius:10px;background:var(--pcd-green-light);color:var(--pcd-green);border:1px solid #b8ddc8;}
.rc-brl{font-size:22px;font-weight:800;color:var(--pcd-red);line-height:1.1;}
.rc-miles{font-size:13px;color:var(--pcd-blue);font-weight:500;margin-top:3px;}
.rc-detail{font-size:11px;color:var(--pcd-muted);margin-top:4px;}
.rank-card.empty .rc-brl{color:#ccc;}
/* chips */
.parsed-wrap{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 0 4px;}
.p-chip{background:white;border:1px solid var(--pcd-border);border-radius:20px;padding:4px 12px;font-size:12px;display:inline-flex;align-items:center;gap:4px;}
.p-chip b{color:var(--pcd-blue);}
.p-badge-rt{background:var(--pcd-blue);color:white;border-radius:20px;padding:3px 12px;font-size:11px;font-weight:600;}
.p-badge-ow{background:var(--pcd-blue-light);color:var(--pcd-blue);border-radius:20px;padding:3px 12px;font-size:11px;font-weight:600;}
.p-badge-dir{background:var(--pcd-green-light);color:var(--pcd-green);border-radius:20px;padding:3px 12px;font-size:11px;border:1px solid #b8ddc8;}
/* itinerário */
.itin-card{background:white;border-radius:12px;border:1px solid var(--pcd-border);overflow:hidden;margin-bottom:12px;}
.itin-header{background:var(--pcd-blue-dark);color:white;padding:10px 18px;display:flex;justify-content:space-between;align-items:center;}
.itin-header.volta{background:var(--pcd-red);}
.ih-route{font-size:16px;font-weight:600;}
.ih-meta{font-size:12px;color:rgba(255,255,255,.7);}
.itin-body{padding:14px 18px;}
.itin-timeline{display:flex;align-items:center;margin-bottom:14px;}
.itin-ap{text-align:center;min-width:64px;}
.ap-code{font-size:24px;font-weight:700;color:var(--pcd-text);}
.ap-time{font-size:14px;color:var(--pcd-blue);font-weight:600;margin-top:2px;}
.itin-line{flex:1;display:flex;flex-direction:column;align-items:center;padding:0 8px;gap:3px;}
.itin-bar{width:100%;height:2px;background:var(--pcd-border);position:relative;}
.itin-bar::after{content:'';position:absolute;right:-5px;top:-4px;border-top:5px solid transparent;border-bottom:5px solid transparent;border-left:8px solid var(--pcd-border);}
.itin-dur{font-size:11px;color:var(--pcd-muted);}
.itin-stops-badge{font-size:10px;color:var(--pcd-muted);background:var(--pcd-gray);padding:2px 8px;border-radius:10px;}
.seg-row{display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px dashed var(--pcd-border);}
.seg-row:first-child{border-top:none;}
.seg-flt{background:var(--pcd-blue-light);color:var(--pcd-blue);border-radius:6px;padding:4px 10px;font-size:12px;font-weight:600;min-width:80px;text-align:center;}
.seg-route{font-size:13px;font-weight:600;color:var(--pcd-text);min-width:90px;}
.seg-times{font-size:12px;color:var(--pcd-blue);font-weight:600;}
.seg-dur{font-size:11px;color:var(--pcd-muted);}
.seg-carrier{font-size:11px;color:var(--pcd-muted);flex:1;}
.layover-banner{background:#fff8e6;border:1px dashed #e59a00;color:#856404;border-radius:8px;padding:7px 14px;text-align:center;font-size:12px;font-weight:600;margin:6px 0;}
.sec-title{font-size:12px;font-weight:600;color:var(--pcd-muted);text-transform:uppercase;letter-spacing:.05em;padding-bottom:8px;border-bottom:1px solid var(--pcd-border);margin:16px 0 10px;}
/* grupo de config */
.cfg-group-label{font-size:11px;font-weight:700;color:var(--pcd-muted);text-transform:uppercase;letter-spacing:.06em;margin:10px 0 4px;}
</style>
""", unsafe_allow_html=True)

# ── Top bar ──
st.markdown("""
<div class="pcd-topbar">
  <div style="display:flex;align-items:center;gap:10px">
    <svg width="32" height="32" viewBox="0 0 32 32" fill="none"
         style="background:white;border-radius:6px;padding:4px">
      <path d="M4 18L16 7L28 18" stroke="#1a56a0" stroke-width="2.5" stroke-linecap="round"/>
      <path d="M16 7V25M9 25H23" stroke="#1a56a0" stroke-width="2" stroke-linecap="round"/>
      <circle cx="24" cy="10" r="4" fill="#c0392b"/>
    </svg>
    <div>
      <div class="pcd-logo-name">Agente de Cotação PcD</div>
      <div class="pcd-logo-sub">PassagensComDesconto · Brasília</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

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
        st.markdown('<div class="cfg-group-label">🌍 Internacionais</div>', unsafe_allow_html=True)
        s_tap       = st.checkbox("TAP",       value=False, key="chk_tap")
        s_american  = st.checkbox("AMERICAN AIRLINES", value=False, key="chk_american")
        s_interline = st.checkbox("INTERLINE", value=False, key="chk_interline")

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
        RATES["TAP"]       = st.number_input("TAP",       value=RATES["TAP"],       step=0.001, format="%.4f", key="rate_tap")
        RATES["AMERICAN AIRLINES"] = st.number_input("AMERICAN AIRLINES", value=RATES.get("AMERICAN AIRLINES", 0.0220), step=0.001, format="%.4f", key="rate_american")
        RATES["INTERLINE"] = st.number_input("INTERLINE", value=RATES["INTERLINE"], step=0.001, format="%.4f", key="rate_interline")

        RATES["DEFAULT"] = RATES["GOL"]

# Mapa de checkboxes por companhia BuscaMilhas
_CIA_ACTIVE = {
    "LATAM":     s_latam,
    "GOL":       s_gol,
    "AZUL":      s_azul,
    "TAP":       s_tap,
    "AMERICAN AIRLINES": s_american,
    "INTERLINE": s_interline,
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

if buscar and prompt_text:
    with st.spinner("Analisando pedido e buscando voos..."):
        intent = parse_intent_ptbr(prompt_text, use_llm=use_llm)
        st.session_state["parsed_intent"] = intent
        if st.session_state.get("v_flex") is not None:
            intent.flex_days = st.session_state["v_flex"]
            if intent.flex_days > 0 and intent.flex_mode == "none":
                intent.flex_mode = "plusminus"

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
        "Milhas", "Equiv. BRL", "Taxas", "Preço Final", "Valor c/ Mala",
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
        unit_price = safe_float(getattr(offer, "equivalent_brl", 0))
        total_price = unit_price * adults
        if adults > 1:
            return f"R$ {total_price:,.2f}", f"Total para {adults} passageiros", f"Por passageiro: R$ {unit_price:,.2f} · {dt_str}{airline} Kayak"
        else:
            return f"R$ {unit_price:,.2f}", f"{airline} · Kayak · em dinheiro", f"{dt_str}Valor s/ taxa pode variar"
    else:
        m  = safe_int_miles(getattr(offer, "miles", 0))
        eq = miles_to_brl(m, airline)
        tx = safe_float(getattr(offer, "taxes_brl", 0))
        
        if adults > 1:
            tot_eq = eq * adults
            return (f"R$ {tot_eq:,.2f}",
                    f"Total para {adults} passageiros",
                    f"Por passageiro: R$ {eq:,.2f} · {dt_str}{m:,} milhas + R$ {tx:.2f} taxas")
        else:
            return (f"R$ {eq:,.2f}",
                    f"{airline} · em milhas convertidas",
                    f"{dt_str}{m:,} milhas + R$ {tx:.2f} em taxas")

def _miles_mini_display(offer, adults=1):
    if offer is None: return "—", "—", "—"
    a = str(getattr(offer, "airline", ""))
    m = safe_int_miles(getattr(offer, "miles", 0))
    eq = miles_to_brl(m, a); tx = safe_float(getattr(offer, "taxes_brl", 0))
    dt_str = ""
    if getattr(offer, "outbound", None) and getattr(offer.outbound, "segments", []):
        dt = offer.outbound.segments[0].departure_dt
        if dt: dt_str = f"📅 Partida: {dt.strftime('%d/%m')} · "
    
    if adults > 1:
        tot_eq = eq * adults
        return f"R$ {tot_eq:,.2f}", f"Para {adults} passageiros", f"Por pax: R$ {eq:,.2f} · {m:,} mi + R${tx:.2f} tx"
    else:
        return f"R$ {eq:,.2f}", f"{m:,} milhas", f"{dt_str}Taxas R$ {tx:.2f} · {a}"

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
        eq  = miles_to_brl(m, label)
        tx  = safe_float(getattr(o, "taxes_brl", 0))
        dur = format_duration(getattr(getattr(o, "outbound", None), "duration_min", 0) or 0)
        esc = int(getattr(o, "stops_out", 0) or 0)
        dt_str = ""
        if getattr(o, "outbound", None) and getattr(o.outbound, "segments", []):
            dt = o.outbound.segments[0].departure_dt
            if dt: dt_str = f"📅 {dt.strftime('%d/%m')} • "
        esc_str = f"{esc} esc" if esc > 0 else "Direto"
        
        if adults > 1:
            tot_eq = eq * adults
            return f"""
<div class="rank-card {css}">
  <div class="rc-header"><span class="rc-company">{label}</span>{badge}</div>
  <div class="rc-brl" style="font-size:18px">R$ {tot_eq:,.2f} <span style="font-size:11px;color:#6b7a99">({adults}pax)</span></div>
  <div class="rc-miles">R$ {eq:,.2f} / pax</div>
  <div class="rc-detail">{dt_str}{esc_str} • {dur}<br>{m:,} milhas + R$ {tx:.2f} tx / pax</div>
</div>"""
        else:
            return f"""
<div class="rank-card {css}">
  <div class="rc-header"><span class="rc-company">{label}</span>{badge}</div>
  <div class="rc-brl">R$ {eq:,.2f}</div>
  <div class="rc-miles">{m:,} milhas</div>
  <div class="rc-detail">{dt_str}{esc_str} • {dur}<br>Taxas R$ {tx:.2f}</div>
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
            eq_bo = miles_to_brl(m_bo, a_bo); tx_bo = safe_float(getattr(bo, "taxes_brl", 0))
            bdo_p = safe_float(getattr(bd, "equivalent_brl", 0)) if bd else 0
            eco   = bdo_p - (eq_bo + tx_bo)
            eco_t = f" Comparado ao melhor em dinheiro (R$ {bdo_p:,.2f}), economia estimada de R$ {eco:,.2f}." if eco > 0 else ""
            if adults > 1:
                st.info(f"A melhor opção foi **{a_bo}** em milhas. Total: {m_bo * adults:,} mi ≈ R$ {eq_bo * adults:,.2f} + R$ {tx_bo * adults:.2f} taxas ({adults} pax).{eco_t} (Unitário: {m_bo:,} mi + R$ {tx_bo:.2f} tx)")
            else:
                st.info(f"A melhor opção foi **{a_bo}** em milhas: {m_bo:,} mi ≈ R$ {eq_bo:,.2f} + R$ {tx_bo:.2f} taxas.{eco_t}")


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

if not all_idx:
    st.info("Nenhum voo disponível para detalhar.")
    st.stop()

def _itin_lbl(fid, o):
    a = str(getattr(o, "airline", "?"))
    if _is_money_offer(o):
        p = safe_float(getattr(o, "equivalent_brl", 0))
        return f"{fid} — {a} | R$ {p:,.2f} (dinheiro)"
    m  = safe_int_miles(getattr(o, "miles", 0))
    eq = miles_to_brl(m, a)
    return f"{fid} — {a} | {m:,} mi ≈ R$ {eq:,.2f}"

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
