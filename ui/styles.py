"""CSS e markup do topo da página, isolados do app principal."""
import streamlit as st

_CSS = """
<style>
:root{--pcd-blue:#1a56a0;--pcd-blue-dark:#0d2b6e;--pcd-blue-light:#e8f0fb;
      --pcd-red:#c0392b;--pcd-gray:#f5f6fa;--pcd-border:#dde3ef;
      --pcd-text:#1a2236;--pcd-muted:#6b7a99;
      --pcd-green:#1a7a4a;--pcd-green-light:#eaf4ef;
      color-scheme:light!important;}
/* Força tema claro independente da preferência do navegador/SO.
   Streamlit aplica algumas variáveis de dark mode automaticamente que
   resultam em texto invisível (branco sobre branco) no PcD. */
html, body, [data-testid="stAppViewContainer"]{color-scheme:light!important;}
[data-testid="stSidebar"]{display:none!important;}
section[data-testid="stSidebarContent"]{display:none!important;}
.block-container{padding-top:0!important;padding-bottom:2rem!important;}
.stApp{background-color:var(--pcd-gray)!important;color:var(--pcd-text)!important;}
/* Containers padrão herdam a cor de texto PcD */
[data-testid="stVerticalBlock"],
[data-testid="stHorizontalBlock"],
[data-testid="stExpander"],
[data-testid="stMarkdownContainer"],
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] span:not([class]),
[data-testid="stMarkdownContainer"] div:not([class]){color:var(--pcd-text);}
/* Inputs/widgets — força contraste legível independente do tema do navegador */
.stTextInput input,
.stTextArea textarea,
.stNumberInput input,
.stDateInput input,
.stSelectbox [data-baseweb="select"],
.stMultiSelect [data-baseweb="select"]{
    background:#fff!important;color:var(--pcd-text)!important;
    -webkit-text-fill-color:var(--pcd-text)!important;}
.stRadio label, .stCheckbox label, .stSlider label,
.stSelectbox label, .stTextInput label, .stTextArea label,
.stNumberInput label, .stDateInput label, .stMultiSelect label{
    color:var(--pcd-text)!important;}
/* Dropdown/menu popover de selects */
[data-baseweb="popover"] *, [data-baseweb="menu"] *{color:var(--pcd-text);}
[data-baseweb="popover"], [data-baseweb="menu"]{background:#fff!important;}
/* DataFrames legíveis em dark mode */
[data-testid="stDataFrame"], [data-testid="stDataFrame"] *{color:var(--pcd-text);}
[data-testid="stDataFrame"]{background:#fff!important;}
/* Popover ⚙️ de configurações */
[data-testid="stPopover"]{background:#fff!important;color:var(--pcd-text)!important;}
[data-testid="stPopover"] *{color:var(--pcd-text);}
/* Expander */
[data-testid="stExpander"]{background:#fff!important;border-color:var(--pcd-border)!important;}
[data-testid="stExpander"] summary{color:var(--pcd-text)!important;}
/* Tabs body */
.stTabs [data-baseweb="tab-panel"]{color:var(--pcd-text);}
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
.rank-card{background:#fff!important;color:var(--pcd-text)!important;border-radius:10px;border:1px solid var(--pcd-border);padding:14px 16px;position:relative;overflow:hidden;}
.rank-card::before{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;}
.rank-card.latam::before{background:var(--pcd-red);}
.rank-card.gol::before{background:#ff6b00;}
.rank-card.azul::before{background:#0032a0;}
.rank-card.tap::before{background:#00b761;}
.rank-card.iberia::before{background:#c8102e;}
.rank-card.american::before{background:#0078d2;}
.rank-card.interline::before{background:#6c3483;}
.rank-card.copa::before{background:#005898;}
.rank-card.mcp::before{background:#0ea47a;}
.rc-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;}
.rc-company{font-size:13px;font-weight:600;color:var(--pcd-text);}
.rc-best-badge{font-size:10px;padding:2px 8px;border-radius:10px;background:var(--pcd-green-light);color:var(--pcd-green);border:1px solid #b8ddc8;}
.rc-brl{font-size:22px;font-weight:800;color:var(--pcd-red);line-height:1.1;}
.rc-miles{font-size:13px;color:var(--pcd-blue);font-weight:500;margin-top:3px;}
.rc-detail{font-size:11px;color:var(--pcd-muted);margin-top:4px;}
.rank-card.empty .rc-brl{color:#ccc;}
/* chips */
.parsed-wrap{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 0 4px;color:var(--pcd-text)!important;}
.p-chip{background:#fff!important;color:var(--pcd-text)!important;
    border:1px solid var(--pcd-border)!important;border-radius:20px;padding:4px 12px;
    font-size:12px;display:inline-flex;align-items:center;gap:4px;}
.p-chip b{color:var(--pcd-blue)!important;}
.p-badge-rt{background:var(--pcd-blue)!important;color:#fff!important;border-radius:20px;padding:3px 12px;font-size:11px;font-weight:600;}
.p-badge-ow{background:var(--pcd-blue-light)!important;color:var(--pcd-blue)!important;border-radius:20px;padding:3px 12px;font-size:11px;font-weight:600;}
.p-badge-dir{background:var(--pcd-green-light)!important;color:var(--pcd-green)!important;border-radius:20px;padding:3px 12px;font-size:11px;border:1px solid #b8ddc8;}
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
/* Itinerário PRO — visual redesenhado por segmento */
.itin-card-pro{background:#fff;border:1px solid var(--pcd-border);border-radius:14px;
    box-shadow:0 2px 8px rgba(12,30,80,.06);overflow:hidden;margin-bottom:14px;}
.itin-pro-head{padding:14px 20px;display:flex;align-items:center;gap:18px;flex-wrap:wrap;
    border-bottom:1px solid var(--pcd-border);}
.itin-pro-leg{font-size:11px;font-weight:700;letter-spacing:.08em;
    background:rgba(255,255,255,.85);padding:3px 10px;border-radius:12px;
    border:1px solid currentColor;}
.itin-pro-route{display:flex;align-items:center;gap:10px;}
.itin-pro-iata{font-size:24px;font-weight:800;letter-spacing:.02em;}
.itin-pro-arrow{font-size:18px;opacity:.7;}
.itin-pro-meta{margin-left:auto;font-size:12px;color:var(--pcd-muted);font-weight:500;
    background:rgba(255,255,255,.9);padding:4px 10px;border-radius:8px;}
.itin-pro-body{padding:14px 18px;display:flex;flex-direction:column;gap:8px;}
.itin-pro-seg{display:flex;gap:14px;align-items:stretch;padding:10px 0;}
.itin-pro-seg-airline{flex:0 0 170px;border-left:4px solid;background:#f5f7fb;
    padding:10px 12px;border-radius:8px;display:flex;flex-direction:column;justify-content:center;}
.itin-pro-airline-name{font-size:12px;font-weight:700;line-height:1.2;}
.itin-pro-airline-flt{font-size:11px;opacity:.75;margin-top:3px;font-weight:600;letter-spacing:.04em;}
.itin-pro-seg-cities{flex:1;display:flex;flex-direction:column;gap:4px;justify-content:center;}
.itin-pro-cityline{display:flex;align-items:center;gap:8px;}
.itin-pro-city{font-size:17px;font-weight:700;color:var(--pcd-text);}
.itin-pro-sep{font-size:14px;color:var(--pcd-muted);}
.itin-pro-times{font-size:14px;color:var(--pcd-blue);font-weight:700;letter-spacing:.02em;}
.itin-pro-dur{font-size:11px;color:var(--pcd-muted);font-weight:500;}
.itin-pro-layover{background:#fff8e6;border:1px dashed #e59a00;color:#856404;
    border-radius:8px;padding:8px 14px;font-size:12px;font-weight:600;margin:2px 0;}
.itin-pro-note{font-size:11px;color:var(--pcd-muted);font-style:italic;margin-top:4px;
    padding:6px 10px;background:#f5f6fa;border-radius:6px;}
/* Row selecionada na tabela (Problema 3) — destaque sutil */
[data-testid="stDataFrame"] tr[aria-selected="true"] td{background:var(--pcd-blue-light)!important;}
.sec-title{font-size:12px;font-weight:600;color:var(--pcd-muted);text-transform:uppercase;letter-spacing:.05em;padding-bottom:8px;border-bottom:1px solid var(--pcd-border);margin:16px 0 10px;}
/* grupo de config */
.cfg-group-label{font-size:11px;font-weight:700;color:var(--pcd-muted);text-transform:uppercase;letter-spacing:.06em;margin:10px 0 4px;}
</style>
"""

_TOPBAR = """
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
"""


def inject_styles() -> None:
    """Injeta o CSS global. Chamar uma vez logo após st.set_page_config."""
    st.markdown(_CSS, unsafe_allow_html=True)


def render_topbar() -> None:
    """Renderiza a barra superior com logo e nome."""
    st.markdown(_TOPBAR, unsafe_allow_html=True)
