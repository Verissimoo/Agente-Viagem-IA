# streamlit_app.py
import streamlit as st
from datetime import datetime

from nlp_parser import parse_prompt_pt
from flight_search_service import search_best_in_range
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

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
    return "(não identificado)"


st.set_page_config(page_title="PCD | Chat Passagens (Kayak)", layout="wide")
st.title("PCD — Chat de Passagens (Pagante / Kayak)")

with st.sidebar:
    st.header("Config")
    top_n = st.number_input("Tamanho da lista curta", min_value=3, max_value=20, value=8)
    debug_mode = st.toggle("Modo debug", value=False)

    if st.button("🧹 Limpar cache da busca"):
        st.session_state.last_user_msg = None
        st.session_state.last_result = None
        st.toast("Cache limpo. Envie o prompt novamente.")


if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": (
                "Me peça assim (ida):\n\n"
                "“Quero uma passagem de Brasília para São Paulo somente ida dia 10/3, sem mala despachada.”\n\n"
                "Ou assim (ida e volta FIXO):\n\n"
                "“Quero uma passagem de Brasília para São Paulo ida dia 10/3 e volta dia 15/3, sem mala despachada.”\n\n"
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

user_msg = st.chat_input("Digite seu pedido de passagem...")

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
                st.warning("Não encontrei opções. Tente outra data/rota ou aumente páginas no .env (KAYAK_MAX_PAGES).")
            else:
                st.subheader("Melhor opção ✅")

                cur = best.get("currency") or ""
                airlines = _airlines_str(best)

                if best.get("trip_type") == "roundtrip":
                    st.write(f"**{cur} {best['price']:.2f}** — {best.get('origin')} → {best.get('destination')} — Cia(s): {airlines}")

                    st.markdown("**IDA**")
                    st.write(
                        f"Saída: {fmt_time(best.get('out_departure_time'))} — "
                        f"Chegada: {fmt_time(best.get('out_arrival_time'))} — "
                        f"Duração: {fmt_duration(best.get('out_duration_min'))} — "
                        f"Escalas: {best.get('out_stops')}"
                    )

                    st.markdown("**VOLTA**")
                    st.write(
                        f"Saída: {fmt_time(best.get('in_departure_time'))} — "
                        f"Chegada: {fmt_time(best.get('in_arrival_time'))} — "
                        f"Duração: {fmt_duration(best.get('in_duration_min'))} — "
                        f"Escalas: {best.get('in_stops')}"
                    )

                else:
                    st.write(
                        f"**{cur} {best['price']:.2f}** — {best.get('origin')} → {best.get('destination')} — "
                        f"Data: {best.get('departure_date')} — "
                        f"Saída: {fmt_time(best.get('departure_time'))} — Chegada: {fmt_time(best.get('arrival_time'))} — "
                        f"Duração: {fmt_duration(best.get('duration_min'))} — "
                        f"Escalas: {best.get('stops') if best.get('stops') is not None else 'N/D'} — "
                        f"Cia(s): {airlines}"
                    )

                st.subheader("Tabela (lista curta)")

                rows = []
                for o in options:
                    base = {
                        "Fonte": "KAYAK",
                        "Tipo": "RT" if o.get("trip_type") == "roundtrip" else "OW",
                        "Origem": o.get("origin"),
                        "Destino": o.get("destination"),
                        "Moeda": o.get("currency"),
                        "Preço": round(float(o["price"]), 2),
                        "Cia(s)": _airlines_str(o),
                    }

                    if o.get("trip_type") == "roundtrip":
                        base.update({
                            "IDA Saída": fmt_time(o.get("out_departure_time")),
                            "IDA Chegada": fmt_time(o.get("out_arrival_time")),
                            "IDA Duração": fmt_duration(o.get("out_duration_min")),
                            "IDA Escalas": o.get("out_stops"),
                            "VOLTA Saída": fmt_time(o.get("in_departure_time")),
                            "VOLTA Chegada": fmt_time(o.get("in_arrival_time")),
                            "VOLTA Duração": fmt_duration(o.get("in_duration_min")),
                            "VOLTA Escalas": o.get("in_stops"),
                        })
                    else:
                        base.update({
                            "Data": o.get("departure_date"),
                            "Saída": fmt_time(o.get("departure_time")),
                            "Chegada": fmt_time(o.get("arrival_time")),
                            "Duração": fmt_duration(o.get("duration_min")),
                            "Escalas": o.get("stops"),
                        })

                    if debug_mode:
                        base["Provider"] = o.get("providerName") or o.get("providerCode")
                        base["Página"] = o.get("page")
                        base["Link"] = o.get("shareableUrl")

                    rows.append(base)

                st.dataframe(rows, use_container_width=True, hide_index=True)

        except Exception as e:
            st.error(f"Não consegui processar: {e}")























