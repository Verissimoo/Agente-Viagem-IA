"""Renderização de itinerários e construção de tabelas para a UI multiagent.

Estes helpers produzem HTML via st.markdown (renderer) ou listas de dicts
(build_table_rows). Estão fora de streamlit_app_multiagent.py para
facilitar manutenção e teste isolado.
"""
import streamlit as st

from pcd.core.schema import TripType

from ui.formatters import (
    format_duration,
    get_baggage_price,
    id_prefix as _id_prefix,
    miles_to_brl,
    safe_float,
    safe_int_miles,
)


def build_table_rows(offers, include_baggage: bool = False, id_prefix: str = "", adults: int = 1):
    """Monta linhas de tabela para o app multiagent.

    Recebe `adults` explicitamente — antes lia direto do st.session_state,
    o que tornava a função difícil de reutilizar.
    """
    rows = []
    for i, o in enumerate(offers):
        val_mala = get_baggage_price(o, True)
        airline  = str(getattr(o, "airline", ""))
        src_val  = str(getattr(getattr(o, "source", None), "value", "") or "")
        prefix   = id_prefix or _id_prefix(airline, src_val)
        fid      = f"{prefix}{i+1}"

        # Escala IDA
        segs_out_raw = getattr(o, "outbound_segments_raw", None) or []
        if segs_out_raw and len(segs_out_raw) > 1:
            local_out = ", ".join(getattr(s, "destination", "") for s in segs_out_raw[:-1] if getattr(s, "destination", ""))
        elif hasattr(o, "outbound") and o.outbound and len(o.outbound.segments) > 1:
            local_out = ", ".join(s.destination for s in o.outbound.segments[:-1])
        else:
            local_out = "Direto"

        # IDA
        if hasattr(o, "outbound") and o.outbound and o.outbound.segments:
            fs = o.outbound.segments[0]
            ls = o.outbound.segments[-1]
            raw_miles = o.miles_out if o.miles_out is not None else o.miles
            m_out = safe_int_miles(raw_miles)
            # Sentinela -1: disponível mas pontos não informados pela API PRO
            miles_display = "Consultar" if (raw_miles is not None and int(raw_miles or 0) == -1) else (f"{m_out:,}" if m_out else "—")
            prog  = getattr(o, "miles_program", "")
            eq_unit = miles_to_brl(m_out, airline, prog) if m_out > 0 else 0.0
            tx = safe_float(o.taxes_brl_out if o.taxes_brl_out is not None else o.taxes_brl)
            preco_final = eq_unit + tx

            r_out = {
                "ID": fid, "Companhia": airline, "Trecho": "IDA",
                "Data":    fs.departure_dt.strftime("%d/%m/%Y"),
                "Saída":   fs.departure_dt.strftime("%H:%M"),
                "Chegada": ls.arrival_dt.strftime("%H:%M"),
                "Milhas":      miles_display,
                "Custo Real (mi+taxas)": f"R$ {preco_final:.2f}" if eq_unit > 0 else "—",
            }
            if adults > 1:
                r_out[f"Total ({adults}pax)"] = f"R$ {preco_final * adults:,.2f}" if m_out else "—"

            r_out.update({
                "Taxas":       f"R$ {tx:.2f}",
                "Preço Final": f"R$ {preco_final:.2f}" if m_out else f"R$ {safe_float(o.equivalent_brl):.2f}",
                "Valor c/ Mala": f"R$ {val_mala:.2f}",
                "Duração":  format_duration(o.outbound.duration_min),
                "Escalas":  int(getattr(o, "stops_out", 0) or 0),
                "Local Escala": local_out,
            })
            rows.append(r_out)

        # Escala VOLTA
        segs_in_raw = getattr(o, "inbound_segments_raw", None) or []
        if segs_in_raw and len(segs_in_raw) > 1:
            local_in = ", ".join(getattr(s, "destination", "") for s in segs_in_raw[:-1] if getattr(s, "destination", ""))
        elif hasattr(o, "inbound") and o.inbound and o.inbound.segments and len(o.inbound.segments) > 1:
            local_in = ", ".join(s.destination for s in o.inbound.segments[:-1])
        else:
            local_in = "Direto"

        # VOLTA
        if (hasattr(o, "trip_type") and o.trip_type == TripType.ROUNDTRIP
                and hasattr(o, "inbound") and o.inbound and o.inbound.segments):
            fi = o.inbound.segments[0]
            li = o.inbound.segments[-1]
            m_in = safe_int_miles(o.miles_in if o.miles_in is not None else o.miles)
            prog = getattr(o, "miles_program", "")
            eq_unit = miles_to_brl(m_in, airline, prog)
            tx = safe_float(o.taxes_brl_in if o.taxes_brl_in is not None else o.taxes_brl)
            preco_final = eq_unit + tx

            r_in = {
                "ID": fid, "Companhia": airline, "Trecho": "VOLTA",
                "Data":    fi.departure_dt.strftime("%d/%m/%Y"),
                "Saída":   fi.departure_dt.strftime("%H:%M"),
                "Chegada": li.arrival_dt.strftime("%H:%M"),
                "Milhas":      f"{m_in:,}" if m_in else "—",
                "Custo Real (mi+taxas)": f"R$ {preco_final:.2f}" if m_in else "—",
            }
            if adults > 1:
                r_in[f"Total ({adults}pax)"] = f"R$ {preco_final * adults:,.2f}" if m_in else "—"

            r_in.update({
                "Taxas":       f"R$ {tx:.2f}",
                "Preço Final": f"R$ {preco_final:.2f}" if m_in else f"R$ {safe_float(o.equivalent_brl):.2f}",
                "Valor c/ Mala": f"R$ {val_mala:.2f}",
                "Duração":  format_duration(o.inbound.duration_min),
                "Escalas":  int(getattr(o, "stops_in", 0) or 0),
                "Local Escala": local_in,
            })
            rows.append(r_in)

    return rows


def render_itin_card(offer, direction: str = "outbound"):
    is_volta  = direction == "inbound"
    hcls      = "itin-header volta" if is_volta else "itin-header"
    lbl       = "Volta" if is_volta else "Ida"
    raw_key   = "inbound_segments_raw" if is_volta else "outbound_segments_raw"

    segs_raw  = getattr(offer, raw_key, None) or []
    itin      = offer.inbound if is_volta else offer.outbound
    if not itin:
        return

    if segs_raw:
        fr, lr = segs_raw[0], segs_raw[-1]
        orig = getattr(fr, "origin", "")
        dest = getattr(lr, "destination", "")
        dep_dt = getattr(fr, "departure_dt", None)
        arr_dt = getattr(lr, "arrival_dt", None)
        dep_s  = dep_dt.strftime("%H:%M")    if dep_dt else "—"
        arr_s  = arr_dt.strftime("%H:%M")    if arr_dt else "—"
        ddate  = dep_dt.strftime("%d/%m/%Y") if dep_dt else "—"
        tot_min = int((arr_dt - dep_dt).total_seconds() // 60) if arr_dt and dep_dt else 0
        tot    = format_duration(tot_min)
        nstops = len(segs_raw) - 1
    else:
        ss = itin.segments
        fs, ls = ss[0], ss[-1]
        orig = fs.origin
        dest = ls.destination
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

    if segs_raw:
        for idx, seg in enumerate(segs_raw):
            dep = getattr(seg, "departure_dt", None)
            arr = getattr(seg, "arrival_dt", None)
            ds  = dep.strftime("%H:%M") if dep else "—"
            as_ = arr.strftime("%H:%M") if arr else "—"
            seg_dur_min = int((arr - dep).total_seconds() // 60) if arr and dep else 0
            dur = format_duration(seg_dur_min)
            carrier = getattr(seg, "carrier", "")
            flt = getattr(seg, "flight_number", "") or carrier
            o_s = getattr(seg, "origin", "")
            d_s = getattr(seg, "destination", "")
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
                        st.markdown(
                            f'<div class="layover-banner">🛑 Conexão em {d_s}: {format_duration(lv_min)}</div>',
                            unsafe_allow_html=True,
                        )
    else:
        for idx, seg in enumerate(itin.segments):
            ds_ = seg.departure_dt.strftime("%H:%M")
            as__ = seg.arrival_dt.strftime("%H:%M")
            st.markdown(f"""
<div class="seg-row">
  <div class="seg-flt">{seg.carrier} {seg.flight_number or ''}</div>
  <div class="seg-route">{seg.origin} → {seg.destination}</div>
  <div class="seg-times">{ds_} → {as__}</div>
</div>""", unsafe_allow_html=True)
            if idx < len(itin.segments) - 1:
                nxt = itin.segments[idx + 1]
                lv  = int((nxt.departure_dt - seg.arrival_dt).total_seconds() // 60)
                if lv > 0:
                    st.markdown(
                        f'<div class="layover-banner">🛑 Conexão em {seg.destination}: {format_duration(lv)}</div>',
                        unsafe_allow_html=True,
                    )

    st.markdown("</div></div>", unsafe_allow_html=True)
