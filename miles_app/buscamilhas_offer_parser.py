from __future__ import annotations

"""
buscamilhas_offer_parser.py  v3.1
----------------------------------
CORREÇÃO CRÍTICA:
  Os segmentos detalhados (Conexoes[]) agora são guardados diretamente
  nos campos `outbound_segments_raw` e `inbound_segments_raw` do row/offer,
  em vez de dentro do objeto itinerary. Isso permite que o Streamlit acesse
  os segmentos via `getattr(offer, "outbound_segments_raw", [])` de forma direta,
  sem depender da estrutura interna do schema.

  Cada segmento em segments_raw contém:
    origin, destination, departure_dt, arrival_dt,
    carrier, flight_number, duration_min, layover_min
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from pcd.core.schema import Segment


# ──────────────────────────────────────────────────────────────
# Helpers de data/hora
# ──────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> Optional[datetime]:
    """'19/09/2025 17:25' ou '19/09/2025' → datetime. Retorna None se falhar."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _parse_embarque(s: str) -> Tuple[str, str]:
    """'19/09/2025 17:25' → ('2025-09-19', '17:25')"""
    s = (s or "").strip()
    if not s:
        return "", ""
    parts = s.split(" ", 1)
    hora  = parts[1].strip() if len(parts) > 1 else ""
    try:
        data_iso = datetime.strptime(parts[0], "%d/%m/%Y").strftime("%Y-%m-%d")
    except Exception:
        data_iso = parts[0]
    return data_iso, hora


def _dur_str(s: str) -> str:
    """'01:15' → '1h 15m'"""
    s = (s or "").strip()
    if not s:
        return ""
    parts = s.split(":")
    try:
        h = int(parts[0]); m = int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        return s
    if h == 0: return f"{m}m"
    if m == 0: return f"{h}h"
    return f"{h}h {m}m"


def _dur_min(s: str) -> int:
    """'03:20' → 200"""
    s = (s or "").strip()
    if not s:
        return 0
    parts = s.split(":")
    try:
        return int(parts[0]) * 60 + (int(parts[1]) if len(parts) > 1 else 0)
    except Exception:
        return 0


# ──────────────────────────────────────────────────────────────
# Construção dos segmentos reais a partir de Conexoes[]
# ──────────────────────────────────────────────────────────────

def _build_segments(voo: Dict[str, Any], companhia: str) -> List[Segment]:
    """
    Converte Conexoes[] em lista de objetos Segment estruturados.

    Voo direto (Conexoes vazio):
      → 1 segmento usando Embarque/Desembarque do próprio voo.

    Voo com escala (Conexoes preenchido):
      → 1 segmento por item em Conexoes[], usando EmbarqueCompleto/DesembarqueCompleto.
    """
    conexoes: List[Dict] = voo.get("Conexoes") or []
    segs: List[Segment] = []

    if conexoes:
        for con in conexoes:
            dep_dt = _parse_dt(con.get("EmbarqueCompleto") or "")
            arr_dt = _parse_dt(con.get("DesembarqueCompleto") or "")
            segs.append(Segment(
                origin=con.get("Origem") or "",
                destination=con.get("Destino") or "",
                departure_dt=dep_dt,
                arrival_dt=arr_dt,
                carrier=con.get("CompanhiaAparente") or companhia.upper(),
                flight_number=con.get("NumeroVoo") or ""
            ))
    else:
        dep_dt = _parse_dt(voo.get("Embarque") or "")
        arr_dt = _parse_dt(voo.get("Desembarque") or "")
        segs.append(Segment(
            origin=voo.get("Origem") or "",
            destination=voo.get("Destino") or "",
            departure_dt=dep_dt,
            arrival_dt=arr_dt,
            carrier=companhia.upper(),
            flight_number=voo.get("NumeroVoo") or ""
        ))

    return segs


# ──────────────────────────────────────────────────────────────
# Seleção de melhor tarifa
# ──────────────────────────────────────────────────────────────

def _parse_limite_bagagem(limite: Any) -> int:
    """
    Converte LimiteBagagem para um inteiro simples.

    Formatos suportados:
      - int/float simples:  1 → 1
      - dict complexo (internacionais):
        {"BagagemDespachada": {"23kg": 1, "15kg": null}, ...} → 1 se qualquer slot > 0
    Retorna 0 se sem bagagem despachada incluída.
    """
    if limite is None:
        return 0
    if isinstance(limite, (int, float)):
        try:
            return int(limite)
        except Exception:
            return 0
    if isinstance(limite, dict):
        # Formato internacionais: {"BagagemDespachada": {"23kg": 1, ...}}
        desp = limite.get("BagagemDespachada") or {}
        if isinstance(desp, dict):
            for v in desp.values():
                try:
                    if v and int(v) > 0:
                        return 1
                except Exception:
                    pass
        return 0
    try:
        return int(limite)
    except Exception:
        return 0


def _best_milhas(lst: List[Dict], companhia: str) -> Tuple[Optional[int], Optional[int], Optional[float], str]:
    """Retorna (base_pts, bag_pts, taxa, tipo_milhas)."""
    scored = []
    for m in (lst or []):
        pts = None
        for k in ("Adulto", "TotalAdulto"):
            v = m.get(k)
            if v is not None:
                try: pts = int(float(v)); break
                except: pass
        if not pts or pts <= 0:
            continue
        try:
            taxa = float(m.get("TaxaEmbarque") or 0) + float(m.get("TaxaResgate") or 0)
        except: taxa = 0.0
        bag = _parse_limite_bagagem(m.get("LimiteBagagem"))
        scored.append((pts, taxa, bag, str(m.get("TipoMilhas") or ""), m))

    if not scored:
        return None, None, None, ""
    scored.sort(key=lambda x: (x[0], x[1]))
    base, btax, _, btype, _ = scored[0]

    bag = None
    if "LATAM" in companhia.upper():
        std_cands = sorted({p for (p,_,_,t,_) in scored if "STANDARD" in t.upper() and p > base})
        if std_cands:
            bag = std_cands[0]

    if bag is None:
        bag_cands = sorted({p for (p,_,b,_,_) in scored if b >= 1 and p > base})
        bag = bag_cands[0] if bag_cands else None
        if bag is None:
            nxt = sorted({p for (p,_,_,_,_) in scored if p > base})
            bag = nxt[0] if nxt else None

    if bag is not None and bag <= base:
        bag = None
    return base, bag, btax, btype


def _best_valor(lst: List[Dict]) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
    """Retorna (base_val, bag_val, taxa, tipo_valor)."""
    scored = []
    for m in (lst or []):
        pts = None
        for k in ("Adulto", "TotalAdulto"):
            v = m.get(k)
            if v is not None:
                try: pts = float(v); break
                except: pass
        if not pts or pts <= 0:
            continue
        try: taxa = float(m.get("TaxaEmbarque") or 0)
        except: taxa = 0.0
        bag = _parse_limite_bagagem(m.get("LimiteBagagem"))
        scored.append((pts, taxa, bag, str(m.get("TipoValor") or ""), m))

    if not scored:
        return None, None, None, ""
    scored.sort(key=lambda x: (x[0], x[1]))
    base, btax, _, btype, _ = scored[0]

    bag_cands = sorted({p for (p,_,b,_,_) in scored if b >= 1 and p > base})
    bag = bag_cands[0] if bag_cands else None
    if bag is None:
        nxt = sorted({p for (p,_,_,_,_) in scored if p > base})
        bag = nxt[0] if nxt else None
    if bag is not None and bag <= base:
        bag = None
    return base, bag, btax, btype


# ──────────────────────────────────────────────────────────────
# Extrator principal
# ──────────────────────────────────────────────────────────────

def extract_rows_from_buscamilhas(
    raw: Dict[str, Any],
    companhia: str,
    trip_type: str,   # "OW" | "RT"
) -> List[Dict[str, Any]]:
    """
    Extrai rows do retorno da API Busca Milhas.

    Cada row inclui:
      outbound_segments_raw  — lista de segmentos da IDA   (para itinerário detalhado)
      inbound_segments_raw   — lista de segmentos da VOLTA (para itinerário detalhado)
    """
    status = raw.get("Status") or {}
    if status.get("Erro"):
        raise RuntimeError(f"BuscaMilhas erro: {status.get('Alerta')}")

    trechos = raw.get("Trechos") or {}
    out: List[Dict[str, Any]] = []

    for _tk, trecho_data in trechos.items():
        voos = trecho_data.get("Voos") or []
        if not voos:
            continue

        for voo in voos:
            sentido     = str(voo.get("Sentido") or "ida").lower()
            trecho_lbl  = "IDA" if sentido == "ida" else "VOLTA"

            milhas_list = voo.get("Milhas") or []
            valor_list  = voo.get("Valor")  or []
            if not milhas_list and not valor_list:
                continue

            emb   = voo.get("Embarque")   or ""
            desemb = voo.get("Desembarque") or ""
            data_iso, hora_saida   = _parse_embarque(emb)
            _,         hora_chegada = _parse_embarque(desemb)
            duracao  = _dur_str(voo.get("Duracao") or "")
            dt_dep   = _parse_dt(emb)
            dt_arr   = _parse_dt(desemb)
            n_con    = int(voo.get("NumeroConexoes") or 0)

            # ─── SEGMENTOS DETALHADOS ──────────────────────────
            segs = _build_segments(voo, companhia)

            local_escala = (
                ", ".join(s.destination for s in segs[:-1] if s.destination)
                if n_con > 0 and len(segs) > 1
                else "Direto"
            )

            # ─── BASE DO ROW ───────────────────────────────────
            # ATENÇÃO: outbound_segments_raw / inbound_segments_raw são
            # preenchidos DEPOIS de saber o sentido, mais abaixo.
            base: Dict[str, Any] = {
                "Programa":   companhia.upper(),
                "Companhia":  companhia.upper(),
                "Tipo":       trip_type,
                "Trecho":     trecho_lbl,
                "Origem":     voo.get("Origem")  or trecho_data.get("Origem")  or "",
                "Destino":    voo.get("Destino") or trecho_data.get("Destino") or "",
                "Data":       data_iso,
                "Saída":      hora_saida,
                "Chegada":    hora_chegada,
                "Duração":    duracao,
                "Escalas":    n_con,
                "Local Escala": local_escala,
                "departure_dt": dt_dep,
                "arrival_dt":   dt_arr,
                # Segmentos no level do offer — chave por sentido
                "outbound_segments_raw": segs if trecho_lbl == "IDA"   else [],
                "inbound_segments_raw":  segs if trecho_lbl == "VOLTA" else [],
                # Legado (mantido para compatibilidade)
                "Conexoes":   voo.get("Conexoes") or [],
                "segments_raw": segs,
                "NumeroVoo":  voo.get("NumeroVoo") or "",
                "GroupId":    f"{voo.get('NumeroVoo','')}_{data_iso}",
                "Link":       "",
                "_sort_trecho": 0 if trecho_lbl == "IDA" else 1,
            }

            if milhas_list:
                b_m, bag_m, taxa_m, tipo_m = _best_milhas(milhas_list, companhia)
                if b_m is not None:
                    r = base.copy()
                    r.update({
                        "IsMiles":    True,
                        "Milhas":     b_m,
                        "Taxas (R$)": taxa_m,
                        "Bagagem":    bag_m if bag_m is not None else "—",
                        "TipoMilhas": tipo_m,
                        "_sort_compare": b_m,
                    })
                    out.append(r)

            if valor_list:
                b_v, bag_v, taxa_v, tipo_v = _best_valor(valor_list)
                if b_v is not None:
                    r = base.copy()
                    r.update({
                        "IsMiles":    False,
                        "Preço":      b_v,
                        "Taxas (R$)": taxa_v,
                        "Bagagem":    bag_v if bag_v is not None else "—",
                        "TipoMilhas": tipo_v,
                        "_sort_compare": b_v,
                    })
                    out.append(r)

    out.sort(key=lambda r: (r["_sort_compare"], r["_sort_trecho"]))
    for r in out:
        r.pop("_sort_compare", None)
        r.pop("_sort_trecho",  None)

    return out


# ──────────────────────────────────────────────────────────────
# Debug
# ──────────────────────────────────────────────────────────────

def debug_raw_json(raw: Dict[str, Any], max_voos: int = 3) -> str:
    preview: Dict[str, Any] = {
        "Status":     raw.get("Status"),
        "Busca_keys": list((raw.get("Busca") or {}).keys()),
        "Trechos":    {},
    }
    for k, v in (raw.get("Trechos") or {}).items():
        preview["Trechos"][k] = {
            "Origem":     v.get("Origem"),
            "Destino":    v.get("Destino"),
            "Data":       v.get("Data"),
            "total_voos": len(v.get("Voos") or []),
            "voos_amostra": (v.get("Voos") or [])[:max_voos],
        }
    return json.dumps(preview, ensure_ascii=False, indent=2)








