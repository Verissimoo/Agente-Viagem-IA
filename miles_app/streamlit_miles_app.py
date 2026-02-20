from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

# garante import do pacote a partir da raiz do projeto
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from miles_app.iata_resolver import resolve_place_to_codes
from miles_app.miles_search_service import search_latam_miles_in_range
from miles_app.nlp_parser import parse_prompt_pt


# -----------------------
# Helpers (UI)
# -----------------------
def _trecho_rank(t: Any) -> int:
    t = str(t or "").upper()
    if t == "IDA":
        return 0
    if t == "VOLTA":
        return 1
    return 9


def _as_int_or_none(v: Any) -> Optional[int]:
    if v is None or isinstance(v, bool):
        return None
    try:
        s = str(v).strip()
        if s in ("", "—", "-", "None", "null"):
            return None
        return int(float(s))
    except Exception:
        return None


def _as_float_or_none(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        s = str(v).strip().replace(",", ".")
        if s in ("", "—", "-", "None", "null"):
            return None
        return float(s)
    except Exception:
        return None


def _fmt_date_br(s: Any) -> str:
    """
    Converte '2026-04-30' -> '30/04/2026'
    Se vier vazio/None, retorna ''.
    """
    if not s:
        return ""
    txt = str(s).strip()
    # já está no formato dd/mm/aaaa
    if len(txt) == 10 and txt[2] == "/" and txt[5] == "/":
        return txt
    # ISO
    try:
        d = datetime.strptime(txt[:10], "%Y-%m-%d").date()
        return d.strftime("%d/%m/%Y")
    except Exception:
        return txt


def _pick_best_group(rows: List[Dict[str, Any]]) -> str:
    best_gid = None
    best_key = None
    seen = set()
    for r in rows:
        gid = str(r.get("GroupId") or "")
        if not gid or gid in seen:
            continue
        seen.add(gid)
        miles = _as_int_or_none(r.get("Milhas"))
        taxes = _as_float_or_none(r.get("Taxas (R$)"))
        key = (miles if miles is not None else 10**18, taxes if taxes is not None else 10**18)
        if best_key is None or key < best_key:
            best_key = key
            best_gid = gid
    return best_gid or (str(rows[0].get("GroupId") or "") if rows else "")


def _best_metrics(rows_for_gid: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows_for_gid:
        return {"miles": None, "taxes": None, "bag": None, "link": None}
    r0 = rows_for_gid[0]
    return {
        "miles": _as_int_or_none(r0.get("Milhas")),
        "taxes": _as_float_or_none(r0.get("Taxas (R$)")),
        "bag": _as_int_or_none(r0.get("Bagagem")),
        "link": r0.get("Link"),
    }


def _insert_group_separators(df: pd.DataFrame, group_col: str = "GroupId") -> pd.DataFrame:
    """
    Insere uma linha separadora após cada grupo (GroupId) para dar “respiro”.
    A linha separadora terá Trecho = "—" e o resto vazio.
    """
    if group_col not in df.columns or df[group_col].isna().all():
        return df

    blocks: List[pd.DataFrame] = []
    for _, gdf in df.groupby(group_col, sort=False):
        blocks.append(gdf)

        blank = {c: "" for c in df.columns}
        if "Trecho" in blank:
            blank["Trecho"] = "—"

        # numéricos como NA pra não quebrar formatação
        for c in ["Milhas", "Taxas (R$)", "Bagagem (23kg pts)", "Escalas"]:
            if c in blank:
                blank[c] = pd.NA

        blocks.append(pd.DataFrame([blank]))

    out = pd.concat(blocks, ignore_index=True)
    return out


# -----------------------
# Streamlit
# -----------------------
st.set_page_config(page_title="Milhas LATAM (Moblix)", layout="wide")
st.title("✈️ Milhas LATAM (Moblix)")
st.caption("Mostra apenas ofertas com milhas (price.pointsInfo.totalPoints).")

colA, colB, colC = st.columns([1, 1, 2])
with colA:
    flex_days = st.number_input("Flexibilidade (± dias)", min_value=0, max_value=10, value=0, step=1)
with colB:
    list_size = st.number_input("Tamanho da lista", min_value=5, max_value=50, value=15, step=1)
with colC:
    debug = st.checkbox("Debug", value=False)

prompt = st.text_input(
    "Digite seu pedido",
    value="Quero uma passagem de Brasília para São Paulo ida dia 30/03/2026 e volta dia 05/04/2026",
)

if st.button("Buscar"):
    try:
        q = parse_prompt_pt(prompt)

        if debug:
            st.subheader("DEBUG — parsed")
            st.json(q)

        origin_place = q.get("origin_place")
        destination_place = q.get("destination_place")
        dep = q.get("date_start")
        ret = q.get("return_start")

        if not origin_place or not destination_place or not dep:
            raise ValueError(
                "Não consegui entender origem/destino/data. Ex: 'Brasília para São Paulo ida dia 30/03/2026'."
            )

        origin = resolve_place_to_codes(origin_place)[0]
        destination = resolve_place_to_codes(destination_place)[0]

        with st.spinner("Buscando LATAM..."):
            res = search_latam_miles_in_range(
                origin=origin,
                destination=destination,
                departure_date=dep,
                return_date=ret,
                flex_days=int(flex_days),
                list_size=int(list_size),
            )

        if debug and isinstance(res, dict) and "debug" in res:
            st.caption(str(res["debug"]))

        rows: List[Dict[str, Any]] = (res or {}).get("rows") or []
        if not rows:
            st.warning("Nenhuma oferta em milhas LATAM encontrada.")
            st.stop()

        # -----------------------
        # Melhor opção ✅
        # -----------------------
        best_gid = _pick_best_group(rows)
        best_block = [r for r in rows if str(r.get("GroupId") or "") == best_gid]
        best_block = sorted(best_block, key=lambda x: _trecho_rank(x.get("Trecho")))

        metrics = _best_metrics(best_block)

        st.subheader("Melhor opção ✅")
        for r in best_block:
            st.write(
                f"**{r.get('Trecho', '')}** | {r.get('Origem')} → {r.get('Destino')} | "
                f"Escalas: {r.get('Escalas')} | Duração: {r.get('Duração')} | "
                f"Data: {_fmt_date_br(r.get('Data'))} | Saída: {r.get('Saída')} | Chegada: {r.get('Chegada')}"
            )

        miles_txt = "—" if metrics["miles"] is None else str(metrics["miles"])
        taxes_txt = "—" if metrics["taxes"] is None else f"{metrics['taxes']:.2f}"
        bag_txt = "—" if metrics["bag"] is None else str(metrics["bag"])

        st.write(f"**Milhas:** {miles_txt} | **Taxas:** R$ {taxes_txt} | **Bagagem (23kg):** {bag_txt}")

        if metrics.get("link"):
            st.link_button("Abrir link da oferta", metrics["link"])

        if debug:
            st.caption(f"GroupId (melhor): {best_gid}")

        st.divider()

        # -----------------------
        # Tabela (mais limpa)
        # - sem Tipo
        # - data BR
        # - separador por grupo
        # - GroupId/Link só no debug
        # -----------------------
        df = pd.DataFrame(rows)

        # garante colunas
        needed = ["Trecho", "Origem", "Destino", "Data", "Saída", "Chegada", "Duração", "Escalas", "Milhas", "Taxas (R$)", "Bagagem", "GroupId", "Link"]
        for col in needed:
            if col not in df.columns:
                df[col] = None

        df["Milhas"] = df["Milhas"].apply(_as_int_or_none)
        df["Taxas (R$)"] = df["Taxas (R$)"].apply(_as_float_or_none)
        df["Bagagem"] = df["Bagagem"].apply(_as_int_or_none)
        df["Data"] = df["Data"].apply(_fmt_date_br)

        # ordena por group + IDA antes de VOLTA
        df["_trecho_rank"] = df["Trecho"].apply(_trecho_rank)
        df["_gid_order"] = pd.Categorical(df["GroupId"], categories=list(dict.fromkeys(df["GroupId"].tolist())), ordered=True)
        df = df.sort_values(by=["_gid_order", "_trecho_rank"], kind="stable").drop(columns=["_trecho_rank", "_gid_order"])

        # colunas de exibição (sem Tipo)
        df_view = df[
            ["Trecho", "Origem", "Destino", "Data", "Saída", "Chegada", "Duração", "Escalas", "Milhas", "Taxas (R$)", "Bagagem", "GroupId", "Link"]
        ].copy()

        df_view = df_view.rename(columns={"Bagagem": "Bagagem (23kg pts)"})

        # insere “respiro” (linha separadora) por group
        df_view = _insert_group_separators(df_view, group_col="GroupId")

        # só mostra debug cols quando debug=True
        if not debug:
            df_view = df_view.drop(columns=["GroupId", "Link"], errors="ignore")

        st.subheader("Tabela (milhas LATAM)")
        st.dataframe(
            df_view,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Milhas": st.column_config.NumberColumn(format="%d"),
                "Taxas (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
                "Bagagem (23kg pts)": st.column_config.NumberColumn(format="%d"),
                "Escalas": st.column_config.NumberColumn(format="%d"),
            },
        )

    except Exception as e:
        st.error(f"Erro: {e}")
        if debug:
            st.exception(e)











