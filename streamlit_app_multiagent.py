import streamlit as st
import pandas as pd
import json
import os
import time
import traceback
from datetime import date
from pcd.run import run_pipeline
from pcd.core.config import config
from pcd.core.schema import SourceType, TripType, CabinClass
from pcd.nlp.intent_parser import parse_intent_ptbr

def source_is(offer, name: str) -> bool:
    """Verifica se a fonte da oferta corresponde ao nome (string ou enum)"""
    if not offer or not hasattr(offer, "source"):
        return False
    s = offer.source
    if isinstance(s, str):
        return s.lower() == name.lower()
    if hasattr(s, "value"):
        return str(s.value).lower() == name.lower()
    return str(s).lower() == name.lower()

# Page Config
st.set_page_config(page_title="PCD Multi-Agent Pipeline", page_icon="✈️", layout="wide")

st.title("✈️ PCD: Multi-Agent Flight Search Pipeline")

# Initialize session state for inputs if not present
if "origin_input" not in st.session_state: st.session_state["origin_input"] = "BSB"
if "dest_input" not in st.session_state: st.session_state["dest_input"] = "GRU"
if "prompt_input" not in st.session_state: st.session_state["prompt_input"] = ""
if "date_start_input" not in st.session_state: st.session_state["date_start_input"] = date.today() + pd.Timedelta(days=7)
if "date_return_input" not in st.session_state: st.session_state["date_return_input"] = date.today() + pd.Timedelta(days=14)
if "is_roundtrip_input" not in st.session_state: st.session_state["is_roundtrip_input"] = True
if "direct_only_input" not in st.session_state: st.session_state["direct_only_input"] = False
if "parsed_intent" not in st.session_state: st.session_state["parsed_intent"] = None

# Sidebar - Settings
with st.sidebar:
    st.header("⚙️ Configurações de Origem")
    
    data_source = st.radio("Modo de Dados", ["Fixtures (Mock)", "Dados Reais (API)"], index=0)
    use_fixtures = (data_source == "Fixtures (Mock)")
    
    st.divider()
    st.header("🛡️ Proteção & Ranking")
    offline_mode = st.toggle("Bloquear Rede (Kill-Switch)", value=use_fixtures, help="Se ON, impede qualquer chamada real via PCD_OFFLINE=1")
    
    # Debug Dump Toggles
    debug_dump = False
    debug_dump_moblix = False
    if not use_fixtures:
        col_dump1, col_dump2 = st.columns(2)
        with col_dump1:
            debug_dump = st.toggle("📦 Dump Kayak", value=False)
        with col_dump2:
            debug_dump_moblix = st.toggle("💎 Dump Moblix", value=False)
    
    st.session_state["direct_only_input"] = st.checkbox("🚫 Somente voos diretos (0 escalas)", value=st.session_state["direct_only_input"])
    
    top_n = st.slider("Top N Ofertas", 1, 10, 5)
    
    st.divider()
    st.subheader("📊 Parâmetros")
    st.info("💎 LATAM (fixo): R$ 0,0285 / milha")
    cpm = 0.0285 

    st.divider()
    st.subheader("🤖 Inteligência Artificial")
    use_llm = st.toggle("Usar IA (Groq) para interpretar", value=False)
    if use_llm and not os.getenv("GROQ_API_KEY"):
        st.warning("⚠️ GROQ_API_KEY não encontrada no env. Usando Fallback.")

# Main UI
st.subheader("🔍 Pesquisa de Voos")

# Exemplos
with st.expander("📌 Exemplos de Pesquisa", expanded=False):
    c_ex1, c_ex2, c_ex3 = st.columns(3)
    if c_ex1.button("Brasília p/ São Paulo ida 20/10/2026"):
        st.session_state["prompt_input"] = "Quero uma passagem de Brasília para São Paulo ida dia 20/10/2026"
        st.rerun()
    if c_ex2.button("Rio p/ Salvador 10/05/2026 a 17/05/26"):
        st.session_state["prompt_input"] = "Ida e volta: Rio para Salvador 10/05/2026 a 17/05/2026, 2 adultos"
        st.rerun()
    if c_ex3.button("GRU para LIS 12/12/2026"):
        st.session_state["prompt_input"] = "GRU para LIS 12/12/2026"
        st.rerun()

c1_prompt, c2_btn_parse = st.columns([4, 1])

with c1_prompt:
    prompt = st.text_input("Para onde vamos voar? (Texto livre)", value=st.session_state["prompt_input"], placeholder="Ex: Natal para Londres dia 10/10")
    st.session_state["prompt_input"] = prompt

with c2_btn_parse:
    st.write("") # Spacer
    if st.button("🧠 Interpretar texto", use_container_width=True):
        if prompt:
            with st.spinner("Interpretando..."):
                intent = parse_intent_ptbr(prompt, use_llm=use_llm)
                st.session_state["parsed_intent"] = intent
                
                # Auto-fill
                if intent.origin_iata: st.session_state["origin_input"] = intent.origin_iata
                if intent.destination_iata: st.session_state["dest_input"] = intent.destination_iata
                if intent.date_start: st.session_state["date_start_input"] = intent.date_start
                if intent.date_return: st.session_state["date_return_input"] = intent.date_return
                st.session_state["is_roundtrip_input"] = (intent.trip_type == TripType.ROUNDTRIP)
                st.session_state["direct_only_input"] = intent.direct_only
                
                st.success("Interpretado com sucesso!")
                time.sleep(1)
                st.rerun()
        else:
            st.warning("Digite algo no texto livre.")

# Intent Preview
if st.session_state["parsed_intent"]:
    intent = st.session_state["parsed_intent"]
    with st.expander(f"🔮 Preview do Entendimento (Confiança: {intent.confidence*100:.0f}%)", expanded=True):
        col1, col2, col3, col4 = st.columns(4)
        col1.write(f"**Origem:** {intent.origin_city} ({intent.origin_iata})")
        col2.write(f"**Destino:** {intent.destination_city} ({intent.destination_iata})")
        col3.write(f"**Ida:** {intent.date_start}")
        col4.write(f"**Volta:** {intent.date_return if intent.date_return else 'N/A'}")
        st.write(f"**Tipo:** {intent.trip_type.value} | **Direto:** {'Sim' if intent.direct_only else 'Não'} | **Adultos:** {intent.adults}")
        st.caption(f"*Nota:* {intent.notes}")

st.divider()

c2_origin, c3_dest, c4_type = st.columns([1, 1, 1])
with c2_origin:
    origin_input = st.text_input("Origem (IATA)", value=st.session_state["origin_input"], max_chars=3)
    st.session_state["origin_input"] = origin_input

with c3_dest:
    dest_input = st.text_input("Destino (IATA)", value=st.session_state["dest_input"], max_chars=3)
    st.session_state["dest_input"] = dest_input

with c4_type:
    is_roundtrip = st.checkbox("Ida e Volta", value=st.session_state["is_roundtrip_input"])
    st.session_state["is_roundtrip_input"] = is_roundtrip

c_dates = st.columns(2)
with c_dates[0]:
    if not use_fixtures:
        date_start = st.date_input("Data Ida", value=st.session_state["date_start_input"])
        st.session_state["date_start_input"] = date_start
    else:
        st.write("📅 *Usando data Mock*")
        date_start = None

with c_dates[1]:
    if not use_fixtures and is_roundtrip:
        date_return = st.date_input("Data Volta", value=st.session_state["date_return_input"])
        st.session_state["date_return_input"] = date_return
    elif not use_fixtures:
        st.write("Solo ida")
        date_return = None
    else:
        date_return = None

# Conflict Check
has_conflict = False
if st.session_state["parsed_intent"]:
    intent = st.session_state["parsed_intent"]
    # Comparar IATAs principais
    if (intent.origin_iata and intent.origin_iata != origin_input) or \
       (intent.destination_iata and intent.destination_iata != dest_input):
        has_conflict = True
        st.warning("⚠️ **Conflito Detectado:** O texto interpretado difere dos campos manuais.")
        choice = st.radio("Qual fonte de dados deseja usar?", ["Manual (Campos)", "Interpretado (Texto)"], horizontal=True)
        if choice == "Interpretado (Texto)":
            origin_final = intent.origin_iata
            dest_final = intent.destination_iata
            date_start_final = intent.date_start
            date_return_final = intent.date_return
            is_rt_final = (intent.trip_type == TripType.ROUNDTRIP)
        else:
            origin_final = origin_input
            dest_final = dest_input
            date_start_final = date_start
            date_return_final = date_return
            is_rt_final = is_roundtrip
    else:
        origin_final = origin_input
        dest_final = dest_input
        date_start_final = date_start
        date_return_final = date_return
        is_rt_final = is_roundtrip
else:
    origin_final = origin_input
    dest_final = dest_input
    date_start_final = date_start
    date_return_final = date_return
    is_rt_final = is_roundtrip

def validate_env():
    missing = []
    if not os.getenv("RAPIDAPI_KEY"): missing.append("RAPIDAPI_KEY (Kayak)")
    if not os.getenv("MOBLIX_API_KEY"): missing.append("MOBLIX_API_KEY (Moblix)")
    return missing

# Iniciar Busca
search_btn = st.button("🚀 Iniciar Busca Multi-Agente", use_container_width=True)

if search_btn:
    missing_keys = [] if use_fixtures else validate_env()
    date_error = False
    if not use_fixtures:
        if is_rt_final and date_return_final and date_start_final and date_return_final <= date_start_final:
            st.error("A data de volta deve ser posterior à data de ida.")
            date_error = True
        if not origin_final or not dest_final:
            st.error("Origem e Destino são obrigatórios no modo real. Preencha os campos ou interprete um texto.")
            date_error = True

    if missing_keys:
        st.error("🔑 **Credenciais Ausentes!**")
        st.warning(f"Chaves faltantes: " + ", ".join(missing_keys))
    elif not date_error:
        # Reset session state for new search
        for k in list(st.session_state.keys()):
            if k.startswith("sel_") or k == "pipeline_result":
                del st.session_state[k]
        
        os.environ["PCD_OFFLINE"] = "1" if offline_mode else "0"
        os.environ["COST_PER_MILE_BRL"] = str(cpm)
        config.PCD_OFFLINE = 1 if offline_mode else 0

        trace_path = f"trace_ui_{int(time.time())}.jsonl"
        
        try:
            with st.status("🔍 Executando Pipeline Multi-Agente...", expanded=True) as status:
                res = run_pipeline(
                    prompt=prompt or f"{origin_final} para {dest_final}", 
                    top_n=top_n, 
                    use_fixtures=use_fixtures, 
                    trace_out=trace_path,
                    date_start=date_start_final,
                    date_return=date_return_final,
                    direct_only=st.session_state.get("direct_only_input", False),
                    origin=origin_final,
                    destination=dest_final,
                    debug_dump_kayak=debug_dump,
                    debug_dump_moblix=debug_dump_moblix
                )
                st.session_state["pipeline_result"] = res
                st.session_state["search_id"] = int(time.time())
                st.session_state["search_params"] = {
                    "origin": origin_final,
                    "destination": dest_final,
                    "date_start": date_start_final,
                    "trip_type": "roundtrip" if is_rt_final else "oneway",
                    "mode": "real" if not use_fixtures else "fixtures",
                    "kill_switch": offline_mode,
                    "debug_dump": debug_dump,
                    "debug_dump_moblix": debug_dump_moblix
                }
                status.update(label="Busca Concluída!", state="complete", expanded=False)
        except Exception as e:
            st.error(f"❌ Erro fatal: {str(e)}")
            with st.expander("Traceback"):
                st.code(traceback.format_exc())

# Exibição de Resultados (Persistente via Session State)
if "pipeline_result" in st.session_state:
    res = st.session_state["pipeline_result"]
    params = st.session_state.get("search_params", {})

    # PARTE 1 - Painel Debug
    with st.expander("🐞 Debug (Executado)", expanded=False):
        debug_data = {
            "request_signature": f"{params.get('origin')}|{params.get('destination')}|{params.get('date_start')}|{params.get('trip_type')}|{params.get('mode')}",
            "flags": params,
            "counts": {
                "money_results": len(res.money_offers),
                "miles_results": len(res.miles_offers),
                "top_n_ranked": len(res.ranked_offers)
            }
        }
        
        if res.money_offers:
            candidate_debug = []
            for i, o in enumerate(res.money_offers[:2]):
                candidate_debug.append({
                    "offer_id": i + 1,
                    "extracted_amount": o.price_amount,
                    "extracted_currency": o.price_currency,
                    "equivalent_brl": o.equivalent_brl,
                    "airline": o.airline,
                    "rt_calc_mode": "sum_legs" if o.price_brl_out else "already_total"
                })
            debug_data["price_candidates_money"] = candidate_debug

        if params.get("debug_dump"):
            debug_data["dump_status"] = "Files saved to debug_dumps/"

        st.json(debug_data)

    if res.best_overall:
        st.divider()
        tab_top, tab_money, tab_miles, tab_ranking = st.tabs([
            "✨ Resumo (Top)", "💵 Dinheiro (Kayak)", "💎 Milhas (LATAM)", "🔝 Ranking Geral"
        ])
        
        with tab_top:
            st.subheader("🏆 Melhores Opções Encontradas")
            c1, c2, c3 = st.columns(3)
            is_rt = params.get("trip_type") == "roundtrip"
            
            # Cálculo de Sincronia para Melhor Geral
            bm = res.best_miles
            bmo = res.best_money
            
            v_miles = float('inf')
            v_money = float('inf')
            
            if bm:
                v_miles = bm.equivalent_brl * 2 if (is_rt and not bm.miles_out) else bm.equivalent_brl
            if bmo:
                v_money = bmo.equivalent_brl * 2 if (is_rt and not bmo.price_brl_out) else bmo.equivalent_brl
            
            # Determinar vencedor UI
            best_ui = None
            if v_miles < v_money:
                best_ui = bm
                best_ui_val = v_miles
                best_ui_type = "miles"
            elif bmo:
                best_ui = bmo
                best_ui_val = v_money
                best_ui_type = "money"
            elif bm:
                best_ui = bm
                best_ui_val = v_miles
                best_ui_type = "miles"

            with c1:
                st.info("⭐ Melhor Geral")
                if best_ui:
                    label = "Total RT" if is_rt else "Total"
                    st.metric(best_ui.airline, f"R$ {best_ui_val:.2f}", help=label)
                    st.caption(f"Fonte: {best_ui.source.value.upper()}")
                    
                    # Multiplier warning
                    ui_mult = False
                    if best_ui_type == "miles":
                        ui_mult = is_rt and not best_ui.miles_out
                    else:
                        ui_mult = is_rt and not best_ui.price_brl_out
                    
                    if ui_mult:
                        st.caption("⚠️ Valor Total RT estimado (2x trecho)")
                    
                    # Base info
                    if best_ui.price_amount:
                        p_base = best_ui.price_amount * 2 if ui_mult else best_ui.price_amount
                        st.caption(f"Base: {best_ui.price_currency} {p_base:.2f}")
                    else:
                        m_base = best_ui.miles * 2 if ui_mult else best_ui.miles
                        st.caption(f"Base: {m_base:,} milhas")
                        
                    # Soma details
                    if is_rt and (best_ui.miles_out or best_ui.price_brl_out):
                        if best_ui.miles_out:
                            st.write(f"Soma: {best_ui.miles_out:,} + {best_ui.miles_in:,}")
                        else:
                            st.write(f"Soma: R$ {best_ui.price_brl_out:.2f} + R$ {best_ui.price_brl_in:.2f}")
                else:
                    st.write("Sem resultados.")

            with c2:
                st.success("💎 Melhor em Milhas")
                if bm:
                    label = "Total RT" if is_rt else "Total"
                    
                    final_equiv_miles = bm.equivalent_brl
                    mult_miles = False
                    if is_rt and not bm.miles_out:
                        final_equiv_miles = bm.equivalent_brl * 2
                        mult_miles = True
                        
                    st.metric(bm.airline, f"R$ {final_equiv_miles:.2f}", help=label)
                    
                    if mult_miles:
                        st.caption("⚠️ Valor Total RT estimado (2x trecho)")

                    if is_rt and bm.miles_out:
                        st.write(f"**{bm.miles_out:,} + {bm.miles_in:,}** milhas")
                        st.write(f"Total: {bm.miles:,} milhas")
                    else:
                        m_val = bm.miles * 2 if mult_miles else bm.miles
                        st.write(f"**{m_val:,}** milhas")
                    
                    if is_rt and bm.taxes_brl_out:
                        st.write(f"Taxas: R$ {bm.taxes_brl_out:.2f} + {bm.taxes_brl_in:.2f}")
                    else:
                        t_val = bm.taxes_brl * 2 if mult_miles else bm.taxes_brl
                        st.write(f"Taxas: R$ {t_val:.2f}")
                else:
                    st.write("Sem resultados de milhas.")

            with c3:
                st.warning("💵 Melhor em Dinheiro")
                if bmo:
                    label = "Total RT" if is_rt else "Total"
                    
                    mult_money = False
                    if is_rt and not bmo.price_brl_out:
                        mult_money = True

                    eq_brl = bmo.equivalent_brl * 2 if mult_money else bmo.equivalent_brl
                    
                    # Destaque BRL como valor principal
                    st.metric(bmo.airline, f"R$ {eq_brl:.2f}", help=label)
                    
                    if mult_money:
                        st.caption("⚠️ Valor Total RT estimado (2x trecho)")
                    
                    if is_rt and bmo.price_brl_out:
                        st.write(f"Soma RT: R$ {bmo.price_brl_out:.2f} + {bmo.price_brl_in:.2f}")
                    
                    # Moeda original apenas como referência menor
                    if bmo.price_amount and bmo.price_currency != "BRL":
                        p_amount = bmo.price_amount * 2 if mult_money else bmo.price_amount
                        st.caption(f"Base: {bmo.price_currency} {p_amount:.2f}")
                    
                    st.caption(f"Fonte: {bmo.source.value.upper()}")
                else:
                    st.write("Sem resultados em dinheiro.")
            
            st.divider()
            st.subheader("💡 Por que escolher?")
            for j in res.justification:
                st.markdown(f"- {j}")

        def flatten_offers_for_table(offers):
            """Transforma UnifiedOffer em linhas para a tabela, gerando 2 linhas se for Roundtrip"""
            rows = []
            for i, o in enumerate(offers):
                # Determinar Cias
                cias_out = list(set([s.carrier for s in o.outbound.segments]))
                cias_in = list(set([s.carrier for s in o.inbound.segments])) if o.inbound else []
                
                # Linha IDA
                row_ida = {
                    "ID": i + 1,
                    "Fonte": o.source.value.upper(),
                    "Tipo": o.trip_type.value.upper(),
                    "Trecho": "IDA",
                    "Origem": o.outbound.segments[0].origin,
                    "Destino": o.outbound.segments[-1].destination,
                }
                
                # Preços IDA
                if source_is(o, "kayak"):
                    row_ida["Moeda"] = o.price_currency or "BRL"
                    # Se tiver split, usa split. Senão usa total.
                    p_ida = o.price_brl_out if o.price_brl_out is not None else o.price_amount
                    row_ida["Preço"] = f"{p_ida:.2f}" if p_ida else "-"
                else:
                    # Milhas
                    m_ida = o.miles_out if o.miles_out is not None else o.miles
                    row_ida["Milhas"] = f"{m_ida:,}" if m_ida is not None else "-"
                    
                    # Taxas
                    t_ida = o.taxes_brl_out if o.taxes_brl_out is not None else o.taxes_brl
                    row_ida["Taxas"] = f"R$ {t_ida:.2f}" if t_ida is not None else "-"
                
                row_ida["Equivalente BRL"] = f"R$ {o.equivalent_brl:.2f}"
                row_ida["Cia(s)"] = ", ".join(cias_out)
                row_ida["Data"] = o.outbound.segments[0].departure_dt.strftime("%d/%m/%Y")
                row_ida["Saída"] = o.outbound.segments[0].departure_dt.strftime("%H:%M")
                row_ida["Chegada"] = o.outbound.segments[-1].arrival_dt.strftime("%H:%M")
                row_ida["Duração"] = f"{o.outbound.duration_min} min"
                row_ida["Escalas"] = f"{o.stops_out}"
                row_ida["Link"] = o.deeplink
                rows.append(row_ida)
                
                # Linha VOLTA (se Roundtrip)
                if o.trip_type == TripType.ROUNDTRIP and o.inbound:
                    row_volta = {
                        "ID": i + 1,
                        "Fonte": o.source.value.upper(),
                        "Tipo": o.trip_type.value.upper(),
                        "Trecho": "VOLTA",
                        "Origem": o.inbound.segments[0].origin,
                        "Destino": o.inbound.segments[-1].destination,
                        "Equivalente BRL": f"R$ {o.equivalent_brl:.2f}",
                    }
                    if source_is(o, "kayak"):
                        row_volta["Moeda"] = o.price_currency or "BRL"
                        p_volta = o.price_brl_in if o.price_brl_in is not None else o.price_amount
                        row_volta["Preço"] = f"{p_volta:.2f}" if p_volta else "-"
                    else:
                        m_volta = o.miles_in if o.miles_in is not None else o.miles
                        row_volta["Milhas"] = f"{m_volta:,}" if m_volta is not None else "-"
                            
                        t_volta = o.taxes_brl_in if o.taxes_brl_in is not None else o.taxes_brl
                        row_volta["Taxas"] = f"R$ {t_volta:.2f}" if t_volta is not None else "-"
                    
                    row_volta["Cia(s)"] = ", ".join(cias_in)
                    row_volta["Data"] = o.inbound.segments[0].departure_dt.strftime("%d/%m/%Y")
                    row_volta["Saída"] = o.inbound.segments[0].departure_dt.strftime("%H:%M")
                    row_volta["Chegada"] = o.inbound.segments[-1].arrival_dt.strftime("%H:%M")
                    row_volta["Duração"] = f"{o.inbound.duration_min} min"
                    row_volta["Escalas"] = f"{o.stops_in}"
                    row_volta["Link"] = o.deeplink
                    rows.append(row_volta)
            return rows

        def render_table_and_details(offers, key_suffix):
            if not offers:
                st.warning("Nenhuma oferta nesta categoria.")
                return

            rows_data = flatten_offers_for_table(offers)
            df = pd.DataFrame(rows_data)
            
            # Formatação visual
            st.dataframe(df.drop(columns=["ID"]), use_container_width=True, hide_index=True)

            # Detalhes abaixo
            st.write("---")
            sel_id = st.selectbox(f"📋 Ver detalhes do voo (ID)", df["ID"], key=f"sel_{key_suffix}")
            offer = offers[int(sel_id) - 1]
            
            def render_segments(it, label):
                st.markdown(f"**{label}**")
                seg_data = []
                for s in it.segments:
                    seg_data.append({
                        "Cia": s.carrier,
                        "Voo": s.flight_number or "-",
                        "De": s.origin,
                        "Para": s.destination,
                        "Saída": s.departure_dt.strftime("%d/%m %H:%M"),
                        "Chegada": s.arrival_dt.strftime("%H:%M")
                    })
                st.table(pd.DataFrame(seg_data))

            c_det1, c_det2 = st.columns(2)
            with c_det1: render_segments(offer.outbound, "🛫 Ida")
            with c_det2: 
                if offer.inbound: render_segments(offer.inbound, "🛬 Volta")
                else: st.write("Voo só de ida.")
            
            st.markdown(f"[🔗 Abrir no Site]({offer.deeplink})")

        with tab_money:
            render_table_and_details(res.money_offers, "money")
        with tab_miles:
            render_table_and_details(res.miles_offers, "miles")
        with tab_ranking:
            render_table_and_details(res.ranked_offers, "ranking")
    else:
        st.info("Nenhum resultado para exibir. Tente uma nova busca.")

# Footer
st.divider()
st.caption("PCD - Projeto Cotação de Voos | Intent Parser Dynamic | Antigravity AI")
