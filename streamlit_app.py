# streamlit_app.py
import streamlit as st
from datetime import datetime
import json
from pathlib import Path

from nlp_parser import parse_prompt_pt
from flight_search_service import search_best_in_range
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# MCP Award Travel Finder
from mcp_offer_parser import extract_mcp_offers

_MCP_FIXTURE = Path(__file__).parent / "debug_dumps" / "mcp_all_airlines_GRU_JFK_sample.json"


def _load_mcp_fixture() -> list[dict]:
    """Carrega e parseia o sample JSON do MCP para exibicao na aba."""
    if not _MCP_FIXTURE.exists():
        return []
    try:
        with open(_MCP_FIXTURE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return extract_mcp_offers(raw)
    except Exception as exc:
        print(f"[streamlit] Erro ao carregar fixture MCP: {exc}")
        return []


# ---------------------------------------------------------------------------
# Helpers de formatacao
# ---------------------------------------------------------------------------

def fmt_time(iso_str: str | None) -> str:
    if not iso_str or not isinstance(iso_str, str):
        return "N/D"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%H:%M")
    except Exception:
        return iso_str


def fmt_date(iso_str: str | None) -> str:
    if not iso_str or not isinstance(iso_str, str):
        return "N/D"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d/%m/%Y")
    except Exception:
        try:
            dt = datetime.strptime(iso_str[:10], "%Y-%m-%d")
            return dt.strftime("%d/%m/%Y")
        except Exception:
            return iso_str


def fmt_duration(mins: int | None) -> str:
    if mins is None:
        return "N/D"
    try:
        mins = int(mins)
        h = mins // 60
        m = mins % 60
        if h <= 0:
            return f"{m}m"
        if m == 0:
            return f"{h}h"
        return f"{h}h{m:02d}m"
    except Exception:
        return str(mins)


def _airlines_str(offer: dict) -> str:
    arr = offer.get("airlines") or []
    if isinstance(arr, list) and arr:
        return ", ".join([str(x) for x in arr])
    return "(nao identificado)"


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

st.set_page_config(page_title="PCD | Chat Passagens", layout="wide")
st.title("PCD - Chat de Passagens")

tab_kayak, tab_mcp = st.tabs(["Voos Pagantes (Kayak)", "Pesquisa Internacional (MCP)"])

# ============================================================
# ABA MCP - Pesquisa Internacional Award Travel Finder
# ============================================================
with tab_mcp:
    st.subheader("Pesquisa Internacional - Award Travel Finder")
    st.caption(
        "Fonte: Award Travel Finder REST API | Programas de milhas internacionais | "
        f"Fixture: `{_MCP_FIXTURE.name}`"
    )

    col_refresh, col_info = st.columns([1, 4])
    with col_refresh:
        reload_mcp = st.button("Recarregar dados MCP", key="btn_reload_mcp")
    with col_info:
        st.info(
            "Os dados abaixo sao carregados do ultimo sample JSON gerado. "
            "Para atualizar com dados reais, execute `python scripts/fetch_mcp_sample.py` e clique em Recarregar."
        )

    if "mcp_offers" not in st.session_state or reload_mcp:
        st.session_state.mcp_offers = _load_mcp_fixture()

    mcp_offers = st.session_state.get("mcp_offers", [])

    if not mcp_offers:
        st.warning(
            "Nenhuma oferta MCP encontrada. "
            f"Verifique se `debug_dumps/{_MCP_FIXTURE.name}` existe e tem `available: true`."
        )
    else:
        # Filtro de cabine
        cabin_options = sorted(set(o["cabin_class"] for o in mcp_offers))
        selected_cabins = st.multiselect(
            "Filtrar por classe:",
            options=cabin_options,
            default=cabin_options,
            key="mcp_cabin_filter",
        )

        filtered = [o for o in mcp_offers if o["cabin_class"] in selected_cabins]

        cabin_label = {
            "economy":         "Economy",
            "premium_economy": "Premium Economy",
            "business":        "Business",
            "first":           "First",
        }

        rows = []
        for o in filtered:
            rows.append({
                "Companhia":  o["airline"].replace("_", " ").title(),
                "Classe":     cabin_label.get(o["cabin_class"], o["cabin_class"].title()),
                "Milhas":     f"{o['miles']:,}".replace(",", "."),
                "Programa":   o.get("miles_program") or "-",
                "Taxas (R$)": f"R$ {o['taxes_brl']:.2f}",
                "Rota":       o.get("route") or "-",
                "Data":       o.get("search_date") or "-",
                "Link":       o.get("booking_link") or "-",
            })

        if rows:
            st.success(f"{len(rows)} oferta(s) encontrada(s)")
            st.dataframe(
                rows,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Link": st.column_config.LinkColumn("Link", display_text="Reservar"),
                },
            )

            # Destaque: melhor por cabine
            st.markdown("---")
            st.markdown("**Melhores ofertas por classe:**")
            best_by_cabin: dict[str, dict] = {}
            for o in filtered:
                cab = o["cabin_class"]
                if cab not in best_by_cabin or o["miles"] < best_by_cabin[cab]["miles"]:
                    best_by_cabin[cab] = o

            for cab_key in ["first", "business", "premium_economy", "economy"]:
                best = best_by_cabin.get(cab_key)
                if not best:
                    continue
                label = cabin_label.get(cab_key, cab_key.title())
                airline_name = best["airline"].replace("_", " ").title()
                st.write(
                    f"**{label}**: {airline_name} - "
                    f"{best['miles']:,} {best.get('miles_program', 'milhas')} "
                    f"+ R$ {best['taxes_brl']:.2f} em taxas"
                )
        else:
            st.info("Nenhuma oferta para os filtros selecionados.")

# ============================================================
# ABA KAYAK
# ============================================================
with tab_kayak:
    with st.sidebar:
        st.header("Config")
        top_n = st.number_input("Tamanho da lista curta", min_value=3, max_value=20, value=8)
        debug_mode = st.toggle("Modo debug", value=False)

        if st.button("Limpar cache da busca"):
            st.session_state.last_user_msg = None
            st.session_state.last_result = None
            st.toast("Cache limpo. Envie o prompt novamente.")

    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": (
                    "Me peca assim (ida):\n\n"
                    "'Quero uma passagem de Brasilia para Sao Paulo somente ida dia 10/3, sem mala despachada.'\n\n"
                    "Ou assim (ida e volta FIXO):\n\n"
                    "'Quero uma passagem de Brasilia para Sao Paulo ida dia 10/3 e volta dia 15/3.'\n\n"
                    "Fonte atual: **Kayak (pagante)**."
                ),
            }
        ]

    if "last_user_msg" not in st.session_state:
        st.session_state.last_user_msg = None
    if "last_result" not in st.session_state:
        st.session_state.last_result = None

    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    user_msg = st.chat_input("Digite seu pedido de passagem...", key="kayak_chat_input")

    if user_msg:
        st.session_state.messages.append({"role": "user", "content": user_msg})
        with st.chat_message("user"):
            st.markdown(user_msg)

        with st.chat_message("assistant"):
            try:
                if st.session_state.last_user_msg == user_msg and st.session_state.last_result is not None:
                    result = st.session_state.last_result
                else:
                    parsed = parse_prompt_pt(user_msg)
                    with st.spinner("Buscando no Kayak..."):
                        result = search_best_in_range(parsed, top_n=int(top_n))
                    st.session_state.last_user_msg = user_msg
                    st.session_state.last_result = result

                meta = result.get("meta") or {}
                dbg = result.get("debug") or {}

                st.caption(f"Status da busca: {meta.get('pretty')}")
                st.caption(
                    f"Debug: offers_total={dbg.get('offers_total')} | shortlist={dbg.get('offers_shortlist')} | "
                    f"moeda={dbg.get('target_currency')} | tipo={dbg.get('trip_type')} | fonte={dbg.get('pricing_source')}"
                )

                best = result.get("best")
                options = result.get("options") or []

                if not options:
                    st.warning("Nao encontrei opcoes. Tente outra data/rota ou aumente paginas no .env (KAYAK_MAX_PAGES).")
                else:
                    st.subheader("Melhor opcao")

                    cur = best.get("currency") or ""
                    airlines = _airlines_str(best)

                    if best.get("trip_type") == "roundtrip":
                        st.write(f"**{cur} {best['price']:.2f}** - {best.get('origin')} -> {best.get('destination')} - Cia(s): {airlines}")

                        st.markdown("**IDA**")
                        st.write(
                            f"Saida: {fmt_time(best.get('out_departure_time'))} - "
                            f"Chegada: {fmt_time(best.get('out_arrival_time'))} - "
                            f"Duracao: {fmt_duration(best.get('out_duration_min'))} - "
                            f"Escalas: {best.get('out_stops')}"
                        )

                        st.markdown("**VOLTA**")
                        st.write(
                            f"Saida: {fmt_time(best.get('in_departure_time'))} - "
                            f"Chegada: {fmt_time(best.get('in_arrival_time'))} - "
                            f"Duracao: {fmt_duration(best.get('in_duration_min'))} - "
                            f"Escalas: {best.get('in_stops')}"
                        )

                    else:
                        st.write(
                            f"**{cur} {best['price']:.2f}** - {best.get('origin')} -> {best.get('destination')} - "
                            f"Data: {best.get('departure_date')} - "
                            f"Saida: {fmt_time(best.get('departure_time'))} - Chegada: {fmt_time(best.get('arrival_time'))} - "
                            f"Duracao: {fmt_duration(best.get('duration_min'))} - "
                            f"Escalas: {best.get('stops') if best.get('stops') is not None else 'N/D'} - "
                            f"Cia(s): {airlines}"
                        )

                    st.subheader("Tabela (lista curta)")

                    rows = []
                    for o in options:
                        base = {
                            "Fonte":   "KAYAK",
                            "Tipo":    "RT" if o.get("trip_type") == "roundtrip" else "OW",
                            "Origem":  o.get("origin"),
                            "Destino": o.get("destination"),
                            "Moeda":   o.get("currency"),
                            "Preco":   round(float(o["price"]), 2),
                            "Cia(s)":  _airlines_str(o),
                        }

                        if o.get("trip_type") == "roundtrip":
                            base.update({
                                "IDA Saida":    fmt_time(o.get("out_departure_time")),
                                "IDA Chegada":  fmt_time(o.get("out_arrival_time")),
                                "IDA Duracao":  fmt_duration(o.get("out_duration_min")),
                                "IDA Escalas":  o.get("out_stops"),
                                "VOLTA Saida":  fmt_time(o.get("in_departure_time")),
                                "VOLTA Chegada":fmt_time(o.get("in_arrival_time")),
                                "VOLTA Duracao":fmt_duration(o.get("in_duration_min")),
                                "VOLTA Escalas":o.get("in_stops"),
                            })
                        else:
                            base.update({
                                "Data":    o.get("departure_date"),
                                "Saida":   fmt_time(o.get("departure_time")),
                                "Chegada": fmt_time(o.get("arrival_time")),
                                "Duracao": fmt_duration(o.get("duration_min")),
                                "Escalas": o.get("stops"),
                            })

                        if debug_mode:
                            base["Provider"] = o.get("providerName") or o.get("providerCode")
                            base["Pagina"]   = o.get("page")
                            base["Link"]     = o.get("shareableUrl")

                        rows.append(base)

                    st.dataframe(rows, use_container_width=True, hide_index=True)

            except Exception as e:
                st.error(f"Nao consegui processar: {e}")
