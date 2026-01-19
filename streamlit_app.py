import streamlit as st
from datetime import datetime
from nlp_parser import parse_prompt_pt
from flight_search_service import search_best_in_range


def fmt_time(iso_str: str | None) -> str:
    if not iso_str or not isinstance(iso_str, str):
        return "N/D"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%H:%M")
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


st.set_page_config(page_title="PCD | Chat Passagens", layout="wide")
st.title("PCD — Chat de Passagens (melhor preço no intervalo)")

with st.sidebar:
    st.header("Config")
    top_n = st.number_input("Tamanho da lista curta", min_value=3, max_value=15, value=8)
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
                "Me peça assim:\n\n"
                "“Quero uma passagem de Brasília para São Paulo somente ida dia 10/3 "
                "tendo flexibilidade do dia 5 ao dia 15 de março, sem mala despachada.”\n\n"
                "Eu retorno as opções mais baratas com cia, preço, duração, escalas e horários."
            )
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
                with st.spinner("Buscando no intervalo e pegando sempre o menor preço..."):
                    result = search_best_in_range(parsed, top_n=int(top_n))
                st.session_state.last_user_msg = user_msg
                st.session_state.last_result = result

            meta = result.get("meta") or {}
            st.caption(f"Status da busca: {meta.get('pretty')}")

            dbg = result.get("debug") or {}
            st.caption(
                f"Debug: offers_total={dbg.get('offers_total')} | shortlist={dbg.get('offers_shortlist')} | moeda={dbg.get('target_currency')}"
            )

            notes = result.get("notes") or []
            if notes and debug_mode:
                st.info("Notas (FX):\n" + "\n".join(f"- {n}" for n in notes[:5]))

            best = result.get("best")
            options = result.get("options") or []

            if not options:
                st.warning("Não encontrei opções completas. Clique em “Limpar cache” e tente novamente.")
            else:
                st.subheader("Melhor valor encontrado ✅")

                cur = best.get("currency") or ""
                dep_iso = best.get("departure_time")
                arr_iso = best.get("arrival_time")
                airlines = ", ".join(best.get("airlines", [])) if best.get("airlines") else "(não identificado)"

                st.write(
                    f"**{cur} {best['price']:.2f}** — {best['origin']} → {best['destination']} — "
                    f"Data: {best.get('departure_date')} — "
                    f"Saída: {fmt_time(dep_iso)} — Chegada: {fmt_time(arr_iso)} — "
                    f"Duração: {fmt_duration(best.get('duration_min'))} — "
                    f"Escalas: {best.get('stops') if best.get('stops') is not None else 'N/D'} — "
                    f"Cia(s): {airlines}"
                )

                st.subheader("Opções mais baratas (lista curta)")
                rows = []
                for o in options:
                    row = {
                        "Data": o.get("departure_date"),
                        "Origem": o.get("origin"),
                        "Destino": o.get("destination"),
                        "Saída": fmt_time(o.get("departure_time")),
                        "Chegada": fmt_time(o.get("arrival_time")),
                        "Companhia(s)": ", ".join(o.get("airlines", [])) if o.get("airlines") else "(não identificado)",
                        "Moeda": o.get("currency"),
                        "Preço": round(float(o["price"]), 2),
                        "Duração": fmt_duration(o.get("duration_min")),
                        "Escalas": o.get("stops"),
                    }

                    if debug_mode:
                        row["Preço Original"] = o.get("price_original")
                        row["Moeda Original"] = o.get("currency_original")
                        row["FX Aplicado"] = o.get("fx_rate_applied")
                        row["LegID"] = o.get("leg_id")
                        row["Link"] = o.get("shareableUrl")

                    rows.append(row)

                st.dataframe(rows, use_container_width=True, hide_index=True)

        except Exception as e:
            st.error(f"Não consegui processar: {e}")











