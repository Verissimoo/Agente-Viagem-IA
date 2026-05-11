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


def _airline_meta(iata_or_name: str) -> dict:
    """Resolve nome completo, cor primária e cor de fundo da companhia.

    Lookup ordenado (igual ao usado em streamlit_app_multiagent):
      1) pcd.agents.smart_quote.AIRLINE_DISPLAY (IATA → meta)
      2) Mapeamento por nome completo (LATAM, GOL, AZUL etc.)
      3) Fallback neutro azul PcD.
    """
    code = (iata_or_name or "").upper().strip()
    if not code:
        return {"name": "—", "color": "#1a56a0", "bg": "#e8f0fb"}
    try:
        from pcd.agents.smart_quote import AIRLINE_DISPLAY as _CENTRAL
        if code in _CENTRAL:
            return _CENTRAL[code]
    except Exception:
        pass
    # Aliases por nome (carrier pode vir como "LATAM", "AZUL LINHAS AÉREAS", etc.)
    by_name = {
        "LATAM": "LA", "LATAM AIRLINES": "LA", "LATAM LINHAS AÉREAS": "LA",
        "GOL": "G3", "GOL LINHAS AÉREAS": "G3",
        "AZUL": "AD", "AZUL LINHAS AÉREAS": "AD",
        "TAP": "TP", "TAP AIR PORTUGAL": "TP",
        "AMERICAN AIRLINES": "AA", "AMERICAN": "AA",
        "COPA": "CM", "COPA AIRLINES": "CM",
        "IBERIA": "IB",
        "QATAR": "QR", "QATAR AIRWAYS": "QR",
        "INTERLINE": "INTERLINE",
    }
    if code in by_name:
        mapped = by_name[code]
        try:
            from pcd.agents.smart_quote import AIRLINE_DISPLAY as _CENTRAL
            if mapped in _CENTRAL:
                return _CENTRAL[mapped]
        except Exception:
            pass
    if code == "INTERLINE":
        return {"name": "Interline", "color": "#6c3483", "bg": "#f3e9f7"}
    return {"name": code, "color": "#1a56a0", "bg": "#e8f0fb"}


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


def _fmt_arrival_offset(dep_dt, arr_dt) -> str:
    """HH:MM com sufixo (+N) se o segmento aterrissa em outro dia."""
    if dep_dt is None or arr_dt is None:
        return "—"
    base = arr_dt.strftime("%H:%M")
    try:
        diff = (arr_dt.date() - dep_dt.date()).days
        if diff > 0:
            return f"{base} (+{diff})"
    except Exception:
        pass
    return base


def _segments_are_degraded(segs) -> bool:
    """Detecta o caso em que o adapter duplicou o segmento de leg inteiro
    (ex.: `[seg] * (escalas+1)` quando a API não trouxe Conexoes).

    Quando todos os segmentos têm a mesma origem E o mesmo destino,
    eles não representam conexões reais — são duplicatas degradadas.
    """
    if len(segs) <= 1:
        return False
    first = segs[0]
    o0, d0 = getattr(first, "origin", ""), getattr(first, "destination", "")
    return all(
        getattr(s, "origin", "") == o0 and getattr(s, "destination", "") == d0
        for s in segs
    )


def render_itin_card(offer, direction: str = "outbound"):
    """Card profissional do itinerário detalhado com renderização por segmento.

    Fontes de dados (em ordem):
      1) offer.outbound_segments_raw / inbound_segments_raw (alguns parsers)
      2) offer.outbound.segments / inbound.segments (Itinerary do schema)

    Quando o adapter degradou os segmentos (duplicou o leg inteiro), o
    detalhe por segmento é colapsado num único bloco — evita exibir a
    mesma linha N vezes.
    """
    is_volta  = direction == "inbound"
    lbl       = "Volta" if is_volta else "Ida"
    raw_key   = "inbound_segments_raw" if is_volta else "outbound_segments_raw"

    segs_raw  = getattr(offer, raw_key, None) or []
    itin      = offer.inbound if is_volta else offer.outbound
    if not itin:
        return

    # Preferir segs_raw quando disponível e não degradado; cair para itin.segments.
    candidate = list(segs_raw) if segs_raw else list(itin.segments)
    degraded  = _segments_are_degraded(candidate)
    if degraded:
        # Mantém apenas 1 entrada (o leg inteiro) para o detalhe por segmento.
        segs = [candidate[0]] if candidate else []
    else:
        segs = candidate

    if not segs:
        return

    fr, lr = segs[0], segs[-1]
    orig   = getattr(fr, "origin", "") or ""
    dest   = getattr(lr, "destination", "") or ""
    dep_dt = getattr(fr, "departure_dt", None)
    arr_dt = getattr(lr, "arrival_dt", None)
    ddate  = dep_dt.strftime("%d/%m/%Y") if dep_dt else "—"
    tot_min = int((arr_dt - dep_dt).total_seconds() // 60) if (arr_dt and dep_dt) else (itin.duration_min or 0)
    tot     = format_duration(tot_min)

    # Número real de escalas (de segments_raw quando há), com fallback no schema.
    if degraded:
        nstops = int(getattr(offer, "stops_in" if is_volta else "stops_out", 0) or 0)
    else:
        nstops = max(0, len(segs) - 1)
    slbl = "Direto" if nstops == 0 else (f"{nstops} escala" if nstops == 1 else f"{nstops} escalas")

    border_color = "#c0392b" if is_volta else "#1a56a0"
    head_bg      = "#fdf0f2" if is_volta else "#e8f0fb"
    head_fg      = "#c0392b" if is_volta else "#1a56a0"

    st.markdown(f"""
<div class="itin-card-pro" style="border-left:5px solid {border_color}">
  <div class="itin-pro-head" style="background:{head_bg};color:{head_fg}">
    <div class="itin-pro-leg">{lbl.upper()}</div>
    <div class="itin-pro-route">
      <span class="itin-pro-iata">{orig}</span>
      <span class="itin-pro-arrow">→</span>
      <span class="itin-pro-iata">{dest}</span>
    </div>
    <div class="itin-pro-meta">{ddate} · {slbl} · {tot}</div>
  </div>
  <div class="itin-pro-body">
""", unsafe_allow_html=True)

    for idx, seg in enumerate(segs):
        dep  = getattr(seg, "departure_dt", None)
        arr  = getattr(seg, "arrival_dt", None)
        ds   = dep.strftime("%H:%M") if dep else "—"
        as_  = _fmt_arrival_offset(dep, arr)
        seg_dur_min = int((arr - dep).total_seconds() // 60) if (arr and dep) else 0
        dur  = format_duration(seg_dur_min)
        carrier_code = (getattr(seg, "carrier", "") or "").upper()
        meta = _airline_meta(carrier_code)
        flt  = getattr(seg, "flight_number", "") or ""
        flight_label = (f"{carrier_code} {flt}".strip()) if flt else carrier_code
        o_s  = getattr(seg, "origin", "")
        d_s  = getattr(seg, "destination", "")

        st.markdown(f"""
<div class="itin-pro-seg">
  <div class="itin-pro-seg-airline" style="background:{meta['bg']};color:{meta['color']};border-color:{meta['color']}">
    <div class="itin-pro-airline-name">✈️ {meta['name']}</div>
    <div class="itin-pro-airline-flt">{flight_label}</div>
  </div>
  <div class="itin-pro-seg-cities">
    <div class="itin-pro-cityline">
      <span class="itin-pro-city">{o_s}</span>
      <span class="itin-pro-sep">→</span>
      <span class="itin-pro-city">{d_s}</span>
    </div>
    <div class="itin-pro-times">{ds} → {as_}</div>
    <div class="itin-pro-dur">⏱ {dur}</div>
  </div>
</div>
""", unsafe_allow_html=True)

        if idx < len(segs) - 1:
            nxt = segs[idx + 1]
            nxt_dep = getattr(nxt, "departure_dt", None)
            if arr and nxt_dep:
                lv_min = int((nxt_dep - arr).total_seconds() // 60)
                if lv_min > 0:
                    st.markdown(
                        f'<div class="itin-pro-layover">🕐 Conexão em <b>{d_s}</b> · {format_duration(lv_min)}</div>',
                        unsafe_allow_html=True,
                    )

    if degraded and nstops > 0:
        st.markdown(
            '<div class="itin-pro-note">ℹ️ A companhia retornou apenas o resumo do trecho; '
            'detalhes de cada perna da conexão não foram informados pela API.</div>',
            unsafe_allow_html=True,
        )

    st.markdown("</div></div>", unsafe_allow_html=True)
