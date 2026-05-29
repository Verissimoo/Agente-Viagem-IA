"""Gerador de PDF da cotação aprovada.

Estratégia em duas etapas:
1. Renderiza HTML via Jinja2 (template.html).
2. Converte HTML → PDF via WeasyPrint. Se WeasyPrint não estiver disponível
   (Windows sem deps C), cai para fallback ReportLab simples.

A oferta passada aqui DEVE estar já sanitizada (`sanitize_offer`) — provider
name, deeplink e jargão técnico não devem ter sobrado. O gerador NÃO refaz
sanitização: ele assume input limpo. Isso impede vazamento por bug nessa camada.
"""
from __future__ import annotations

import base64
import io
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from backend.app.chat.config import settings
from backend.app.chat.domain.models import Quote, User

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent
# Versão "tight" (sem padding transparente). Proporção real: 1516x144 (~10.5:1).
_LOGO_PATH = (_TEMPLATE_DIR / "assets" / "logo-pcd-tight.png").resolve()
_LOGO_ASPECT = 1516 / 144  # ~10.53


@lru_cache(maxsize=1)
def _logo_data_url() -> str:
    """Logo embedada como data URL — evita problemas de file:// no WeasyPrint Windows."""
    try:
        raw = _LOGO_PATH.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        logger.warning("Logo não encontrada em %s (%s)", _LOGO_PATH, e)
        return ""


# IATA → nome de cidade (fallback). Reusa o que tem em providers/buscamilhas/iata_resolver
# pra consistência. Cobrimos as ~60 mais comuns aqui pra reduzir dependência.
_IATA_TO_CITY = {
    "BSB": "Brasília", "GRU": "São Paulo", "CGH": "São Paulo", "VCP": "Campinas",
    "GIG": "Rio de Janeiro", "SDU": "Rio de Janeiro",
    "CNF": "Belo Horizonte", "PLU": "Belo Horizonte",
    "POA": "Porto Alegre", "CWB": "Curitiba", "FLN": "Florianópolis",
    "SSA": "Salvador", "REC": "Recife", "FOR": "Fortaleza", "NAT": "Natal",
    "MCZ": "Maceió", "AJU": "Aracaju", "JPA": "João Pessoa", "THE": "Teresina",
    "SLZ": "São Luís", "BEL": "Belém", "MAO": "Manaus", "BVB": "Boa Vista",
    "PVH": "Porto Velho", "MCP": "Macapá", "RBR": "Rio Branco", "PMW": "Palmas",
    "VIX": "Vitória", "GYN": "Goiânia", "CGB": "Cuiabá", "CGR": "Campo Grande",
    "NVT": "Navegantes", "JOI": "Joinville", "XAP": "Chapecó", "MGF": "Maringá",
    "LDB": "Londrina", "IGU": "Foz do Iguaçu", "CXJ": "Caxias do Sul",
    "BPS": "Porto Seguro", "IOS": "Ilhéus", "UDI": "Uberlândia",
    "RAO": "Ribeirão Preto", "SJP": "São José do Rio Preto",
    # Internacional comum
    "LIS": "Lisboa", "OPO": "Porto", "MAD": "Madrid", "BCN": "Barcelona",
    "CDG": "Paris", "ORY": "Paris", "LHR": "Londres", "FCO": "Roma",
    "MXP": "Milão", "FRA": "Frankfurt", "MUC": "Munique", "ZRH": "Zurique",
    "AMS": "Amsterdam", "VIE": "Viena", "CPH": "Copenhague",
    "MIA": "Miami", "JFK": "Nova York", "EWR": "Nova York", "LGA": "Nova York",
    "LAX": "Los Angeles", "MCO": "Orlando", "FLL": "Fort Lauderdale",
    "BOS": "Boston", "ATL": "Atlanta", "ORD": "Chicago", "DFW": "Dallas",
    "IAH": "Houston", "YYZ": "Toronto", "YUL": "Montreal",
    "EZE": "Buenos Aires", "AEP": "Buenos Aires", "SCL": "Santiago",
    "LIM": "Lima", "BOG": "Bogotá", "MEX": "Cidade do México",
    "PTY": "Cidade do Panamá", "MVD": "Montevidéu", "ASU": "Assunção",
    "ROS": "Rosario", "CWB": "Curitiba",
    "JNB": "Joanesburgo", "DXB": "Dubai", "DOH": "Doha", "IST": "Istambul",
    "HND": "Tóquio", "NRT": "Tóquio", "HKG": "Hong Kong", "SIN": "Singapura",
}


def _city_name_from_iata(iata: Optional[str]) -> str:
    if not iata:
        return ""
    code = str(iata).upper().strip()
    return _IATA_TO_CITY.get(code, code)


_CABIN_LABEL = {
    "economy": "Econômica",
    "business": "Executiva",
    "first": "Primeira classe",
}


def _format_brl(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"R$ {value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _format_dt(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return iso_str


def _format_date(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "—"
    try:
        d = datetime.fromisoformat(iso_str).date()
        return d.strftime("%d/%m/%Y")
    except Exception:
        return iso_str


def _format_time(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except Exception:
        return iso_str


def _connection_label(minutes: Optional[int]) -> tuple[str, str]:
    """Devolve (texto, severidade) — severidade ∈ {tight, ok, comfortable, long}."""
    if minutes is None:
        return ("desconhecido", "ok")
    h, m = divmod(minutes, 60)
    base = f"{h}h{m:02d}" if h else f"{m}min"
    if minutes < 90:
        return (f"{base} (APERTADA — risco se houver atraso)", "tight")
    if minutes < 180:
        return (f"{base} (ok para internacional)", "ok")
    if minutes < 480:
        return (f"{base} (confortável)", "comfortable")
    return (f"{base} (longa — espera de {h}h)", "long")


def _build_segments(itinerary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not itinerary:
        return {"segments": [], "connections": []}
    raw_segs = itinerary.get("segments", []) or []
    segments = []
    for seg in raw_segs:
        segments.append({
            "origin": seg.get("origin"),
            "destination": seg.get("destination"),
            "dep_time": _format_dt(seg.get("departure_dt")),
            "arr_time": _format_time(seg.get("arrival_dt")),
            "carrier": seg.get("carrier") or "—",
            "flight_number": seg.get("flight_number"),
        })
    # Calcula conexões entre segmentos
    connections = []
    from datetime import datetime
    for i in range(len(raw_segs) - 1):
        try:
            arr = datetime.fromisoformat(str(raw_segs[i].get("arrival_dt", "")).replace("Z", "+00:00"))
            dep = datetime.fromisoformat(str(raw_segs[i+1].get("departure_dt", "")).replace("Z", "+00:00"))
            mins = int((dep - arr).total_seconds() / 60)
        except Exception:
            mins = None
        label, severity = _connection_label(mins)
        connections.append({
            "after_index": i,
            "hub": raw_segs[i].get("destination"),
            "minutes": mins,
            "label": label,
            "severity": severity,
        })
    return {"segments": segments, "connections": connections}


def _effective_pricing(offer: Dict[str, Any]) -> Dict[str, Any]:
    """Decide qual preço/forma mostrar no PDF.

    Se há `miles_alternative` verificada com `equivalent_brl` MENOR que o
    cash da oferta, mostra o caminho em milhas como destaque (e o cash vira
    referência secundária). Caso contrário, mantém a oferta original.
    """
    cash = offer.get("price_brl") or offer.get("equivalent_brl")
    alt = offer.get("miles_alternative") or {}
    alt_eq = alt.get("equivalent_brl")
    alt_miles = alt.get("miles")

    # Caminho preferido: alternativa em milhas existente e mais barata que cash
    if alt_miles and alt_eq and cash and alt_eq < cash:
        return {
            "mode": "miles_alternative",   # exibe milhas como destaque
            "total_label": _format_brl(alt_eq),
            "miles_label": (
                f"{alt_miles:,} mi".replace(",", ".")
                + (f" + {_format_brl(alt.get('taxes_brl'))}" if alt.get("taxes_brl") else "")
            ),
            "carrier_program": alt.get("airline") or "",
            "savings_brl": float(cash - alt_eq),
            "original_cash_brl": float(cash),
            "cat_label": "Mesmo bilhete em milhas (mais barato)",
        }
    # Caminho padrão: cash da oferta (ou milhas se for oferta puramente em milhas)
    if offer.get("miles") is not None and not offer.get("price_brl"):
        miles = offer.get("miles") or 0
        tax = offer.get("taxes_brl") or 0
        return {
            "mode": "miles_original",
            "total_label": _format_brl(offer.get("equivalent_brl") or (tax + miles * 0.025)),
            "miles_label": f"{int(miles):,} mi".replace(",", "") + (f" + {_format_brl(tax)}" if tax else ""),
            "carrier_program": offer.get("airline") or "",
        }
    return {
        "mode": "cash",
        "total_label": _format_brl(cash),
    }


def _origin_dest_date_from(
    quote: Quote, offer: Dict[str, Any],
) -> tuple[str, str, str, Optional[str]]:
    """Extrai origem/destino/data_ida/data_volta robustamente.

    Ordem de preferência: search_request → outbound segments → "—".
    """
    sr = quote.search_request or {}
    out = (offer.get("outbound") or {}).get("segments") or []
    inb = (offer.get("inbound") or {}).get("segments") or []

    origin = (
        sr.get("origin_iata") or sr.get("origin")
        or (out[0].get("origin") if out else None) or "—"
    )
    destination = (
        sr.get("destination_iata") or sr.get("destination")
        or (out[-1].get("destination") if out else None) or "—"
    )
    date_start = sr.get("date_start") or (
        str(out[0].get("departure_dt", ""))[:10] if out else None
    )
    date_return = sr.get("date_return") or (
        str(inb[0].get("departure_dt", ""))[:10] if inb else None
    )
    # Se origin tiver lista, pega o primeiro
    if isinstance(origin, list):
        origin = origin[0] if origin else "—"
    if isinstance(destination, list):
        destination = destination[0] if destination else "—"
    return origin, destination, date_start, date_return


def _render_html(
    quote: Quote,
    user: User,
    offer: Dict[str, Any],
    *,
    notes: Optional[List[str]] = None,
) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("template.html")

    origin, destination, date_start, date_return = _origin_dest_date_from(quote, offer)
    rota = f"{origin} → {destination}"
    sr = quote.search_request or {}
    cabin = sr.get("cabin", "economy")

    return template.render(
        quote={"id_short": quote.id[:8]},
        logo_data_url=_logo_data_url(),
        generated_at=datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M"),
        rota=rota,
        trip={
            "date_start": _format_date(date_start),
            "date_return": _format_date(date_return) if date_return else None,
            "adults": sr.get("adults", 1),
            "cabin_label": _CABIN_LABEL.get(cabin, cabin.title()),
        },
        price={
            "total": _format_brl(offer.get("price_brl") or offer.get("equivalent_brl")),
            "miles": (
                f"{offer.get('miles'):,} mi".replace(",", ".") + (
                    f" + {_format_brl(offer.get('taxes_brl'))} taxas"
                    if offer.get("taxes_brl") else ""
                )
            ) if offer.get("miles") else None,
        },
        offer={
            "category": offer.get("category") or "Padrão",
            "category_why": offer.get("category_why") or "",
            "risk_notes": offer.get("risk_notes"),
        },
        outbound=_build_segments(offer.get("outbound")),
        inbound=_build_segments(offer.get("inbound")) if offer.get("inbound") else None,
        notes=notes or [],
        seller={
            "name": user.display_name or user.email.split("@")[0],
            "store": user.store_name or "",
            "email": user.email,
        },
    )


def _html_to_pdf_weasyprint(html: str) -> Optional[bytes]:
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as e:
        logger.warning("WeasyPrint indisponível (%s); usando fallback", e)
        return None
    try:
        return HTML(string=html, base_url=str(_TEMPLATE_DIR)).write_pdf()
    except Exception as e:
        logger.error("WeasyPrint falhou (%s); usando fallback", e)
        return None


def _html_to_pdf_reportlab(quote: Quote, user: User, offer: Dict[str, Any]) -> bytes:
    """Fallback — PDF profissional usando ReportLab puro.

    Usado quando WeasyPrint não tá disponível (Windows sem GTK).
    Visualmente próximo ao template HTML: header com logo, route card,
    grid de preços, why-box, itinerário em tabela, footer de vendedor.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Image as RImage,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
        HRFlowable,
    )

    # Paleta inspirada no PDF de referência: azul-marinho dominante,
    # vermelho como acento, verde pra confirmações, âmbar pra avisos.
    NAVY = colors.HexColor("#0d1c3d")          # azul-marinho profundo (header/footer/títulos)
    NAVY_LIGHT = colors.HexColor("#1e2c4f")    # variação um pouco mais clara
    BRAND = colors.HexColor("#dc2626")          # vermelho — acento (linha sob header, "para", linhas finas)
    BRAND_DARK = colors.HexColor("#991b1b")
    GRAY_50 = colors.HexColor("#f9fafb")
    GRAY_100 = colors.HexColor("#f3f4f6")
    GRAY_200 = colors.HexColor("#e5e7eb")
    GRAY_500 = colors.HexColor("#6b7280")
    GRAY_700 = colors.HexColor("#374151")
    GRAY_900 = colors.HexColor("#111827")
    AMBER_50 = colors.HexColor("#fffbeb")
    AMBER_800 = colors.HexColor("#92400e")
    BLUE_50 = colors.HexColor("#f0f9ff")
    BLUE_900 = colors.HexColor("#075985")
    GREEN_50 = colors.HexColor("#f0fdf4")        # includes
    GREEN_700 = colors.HexColor("#15803d")
    RED_50 = colors.HexColor("#fef2f2")          # disclaimers
    RED_700 = colors.HexColor("#b91c1c")
    HIGHLIGHT_BG = colors.HexColor("#fef2f2")

    buf = io.BytesIO()

    def _draw_page_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GRAY_500)
        page_w = A4[0]
        y = 12 * mm
        canvas.drawString(16 * mm, y, "Passagens com Desconto")
        canvas.drawRightString(
            page_w - 16 * mm, y,
            f"Cotação #{quote.id[:8]} · pág. {doc.page}",
        )
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=16 * mm, bottomMargin=22 * mm,
        leftMargin=16 * mm, rightMargin=16 * mm,
        title=f"Cotação {quote.id[:8]} — Passagens com Desconto",
        author="Passagens com Desconto",
    )
    styles = getSampleStyleSheet()
    s_title = ParagraphStyle(
        "title", parent=styles["Heading1"],
        textColor=BRAND_DARK, fontName="Helvetica-Bold",
        fontSize=22, leading=26, spaceAfter=2,
    )
    s_subtitle = ParagraphStyle(
        "subtitle", parent=styles["BodyText"],
        textColor=GRAY_500, fontSize=9.5, leading=13, spaceAfter=16,
    )
    s_section = ParagraphStyle(
        "section", parent=styles["Heading2"],
        textColor=GRAY_900, fontName="Helvetica-Bold",
        fontSize=11, leading=14, spaceBefore=14, spaceAfter=8,
        textTransform="uppercase",
    )
    s_route = ParagraphStyle(
        "route", parent=styles["Heading2"],
        textColor=BRAND_DARK, fontName="Helvetica-Bold",
        fontSize=18, leading=22, spaceAfter=4,
    )
    s_when = ParagraphStyle(
        "when", parent=styles["BodyText"],
        textColor=GRAY_500, fontSize=9.5, leading=14,
    )
    s_body = ParagraphStyle(
        "body", parent=styles["BodyText"],
        textColor=GRAY_700, fontSize=10, leading=14,
    )
    s_label = ParagraphStyle(
        "label", parent=styles["BodyText"],
        textColor=GRAY_500, fontSize=7.5, leading=10,
        fontName="Helvetica-Bold",
    )
    s_value_big = ParagraphStyle(
        "value-big", parent=styles["BodyText"],
        textColor=GRAY_900, fontName="Helvetica-Bold",
        fontSize=16, leading=20,
    )
    s_value_sm = ParagraphStyle(
        "value-sm", parent=styles["BodyText"],
        textColor=GRAY_900, fontName="Helvetica-Bold",
        fontSize=11, leading=14,
    )
    s_why = ParagraphStyle(
        "why", parent=styles["BodyText"],
        textColor=BLUE_900, fontSize=9.5, leading=14,
    )
    s_risk = ParagraphStyle(
        "risk", parent=styles["BodyText"],
        textColor=AMBER_800, fontSize=9.5, leading=14,
    )
    s_leg_header = ParagraphStyle(
        "leg-header", parent=styles["BodyText"],
        textColor=GRAY_700, fontName="Helvetica-Bold",
        fontSize=9, leading=12,
    )
    s_seg_time = ParagraphStyle(
        "seg-time", parent=styles["BodyText"],
        textColor=GRAY_900, fontName="Helvetica-Bold",
        fontSize=9.5, leading=12,
    )
    s_seg_route = ParagraphStyle(
        "seg-route", parent=styles["BodyText"],
        textColor=GRAY_700, fontSize=9.5, leading=12,
    )
    s_seg_carrier = ParagraphStyle(
        "seg-carrier", parent=styles["BodyText"],
        textColor=GRAY_500, fontSize=8.5, leading=12, alignment=2,
    )
    s_footer = ParagraphStyle(
        "footer", parent=styles["BodyText"],
        textColor=GRAY_700, fontSize=8.5, leading=12,
    )
    s_footer_dim = ParagraphStyle(
        "footer-dim", parent=styles["BodyText"],
        textColor=GRAY_500, fontSize=7.5, leading=10,
    )

    story = []

    # ── Header: 2 bandas. Topo navy com logo grande. Faixa abaixo com
    # metadados (nº cotação, emitido, válido, vendedor + badge "disponível").
    from backend.app.chat.report.company import COMPANY, quote_number, QUOTE_VALIDITY_HOURS

    LOGO_W_MM = 92
    LOGO_H_MM = LOGO_W_MM / _LOGO_ASPECT
    logo_cell: Any = ""
    if _LOGO_PATH.exists():
        try:
            logo_cell = RImage(str(_LOGO_PATH), width=LOGO_W_MM * mm, height=LOGO_H_MM * mm)
        except Exception as e:
            logger.warning("Falha carregando logo: %s", e)

    # Linha 1: logo (esquerda) + URL (direita) no NAVY
    site_html = (
        "<para align='right'>"
        f"<font color='#a1b0d0' size='7'>{COMPANY['website'].upper()}</font>"
        "</para>"
    )
    top_band = Table(
        [[logo_cell, Paragraph(site_html, s_body)]],
        colWidths=[120 * mm, 58 * mm],
    )
    top_band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 16),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
        ("LEFTPADDING", (0, 0), (-1, -1), 18),
        ("RIGHTPADDING", (0, 0), (-1, -1), 18),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#2a3c66")),
    ]))
    story.append(top_band)

    # Linha 2: metadados (NAVY_LIGHT). Cada coluna tem um Paragraph que
    # COMBINA label+valor com <br/> — garante alinhamento vertical homogêneo.
    issued_dt = datetime.now(timezone.utc).astimezone()
    valid_until = (issued_dt + __import__("datetime").timedelta(hours=QUOTE_VALIDITY_HOURS))
    seller_name = user.display_name or user.email.split("@")[0]
    qnum = quote_number(quote.id)

    s_meta_p = ParagraphStyle(
        "meta_p", parent=styles["BodyText"],
        textColor=colors.white, fontSize=9, leading=14,
        fontName="Helvetica-Bold", spaceBefore=0, spaceAfter=0,
    )

    def _meta_col(label: str, value: str) -> Any:
        return Paragraph(
            f'<font color="#7a8db5" size="6.5"><b>{label.upper()}</b></font><br/>'
            f'<font color="#ffffff" size="10"><b>{value}</b></font>',
            s_meta_p,
        )

    badge_html = (
        '<para align="right">'
        '<font color="#10b981" size="10">●</font>'
        ' <font color="#ffffff" size="8"><b>Disponível para fechamento</b></font>'
        '</para>'
    )
    # Larguras balanceadas — soma 178mm. COTAÇÃO precisa de mais espaço
    # pra evitar quebra ("PCD-XXXX" cabe em 30mm).
    meta_band = Table(
        [[
            _meta_col("Cotação Nº", qnum),
            _meta_col("Emitida em", issued_dt.strftime("%d %b %Y").upper()),
            _meta_col("Válida até", valid_until.strftime("%d %b %Y").upper()),
            _meta_col("Vendedor", seller_name[:18]),
            Paragraph(badge_html, s_body),
        ]],
        colWidths=[30 * mm, 33 * mm, 33 * mm, 32 * mm, 50 * mm],
    )
    meta_band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY_LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, 0), (-1, -1), 2.5, BRAND),
    ]))
    story.append(meta_band)
    story.append(Spacer(1, 22))

    # ── Tag de tipo de viagem + título + route card visual ──────────
    origin, destination, date_start, date_return = _origin_dest_date_from(quote, offer)
    sr = quote.search_request or {}
    is_round = bool(date_return)
    trip_tag = "VIAGEM · IDA E VOLTA" if is_round else "VIAGEM · SOMENTE IDA"

    # Tag pequena dourada/marrom (estilo destaque)
    s_tag = ParagraphStyle(
        "tag", parent=styles["BodyText"],
        textColor=colors.HexColor("#b8923a"), fontSize=8.5, leading=12,
        fontName="Helvetica-Bold",
    )
    story.append(Paragraph(f"— &nbsp;&nbsp;{trip_tag}", s_tag))
    story.append(Spacer(1, 2))

    # Título grande com "Origem para Destino" — "para" em vermelho como acento
    origin_name = _city_name_from_iata(origin)
    dest_name = _city_name_from_iata(destination)
    title_text = (
        f"<font color='#0d1c3d'>{origin_name} ({origin})</font> "
        f"<font color='#dc2626'>para</font> "
        f"<font color='#0d1c3d'>{dest_name} ({destination})</font>"
    )
    s_title_big = ParagraphStyle(
        "title-big", parent=styles["Heading1"],
        fontSize=20, leading=24, spaceAfter=2,
        fontName="Helvetica-Bold",
    )

    # Card lateral ROS → ✈ → BSB
    s_route_lbl_l = ParagraphStyle(
        "rt-l", parent=styles["BodyText"], textColor=colors.white,
        fontSize=14, fontName="Helvetica-Bold", alignment=1, leading=16,
    )
    s_route_sub = ParagraphStyle(
        "rt-s", parent=styles["BodyText"], textColor=colors.HexColor("#a1b0d0"),
        fontSize=6, alignment=1, leading=8,
    )
    s_route_mid = ParagraphStyle(
        "rt-m", parent=styles["BodyText"], textColor=colors.HexColor("#a1b0d0"),
        fontSize=8, alignment=1, leading=10,
    )
    route_card_inner = Table(
        [[
            Table(
                [[Paragraph(f"<b>{origin}</b>", s_route_lbl_l)],
                 [Paragraph(origin_name.upper()[:14], s_route_sub)]],
                colWidths=[None],
            ),
            Table(
                [[Paragraph("✈", s_route_lbl_l)],
                 [Paragraph("SOMENTE<br/>IDA" if not is_round else "IDA E<br/>VOLTA", s_route_mid)]],
                colWidths=[None],
            ),
            Table(
                [[Paragraph(f"<b>{destination}</b>", s_route_lbl_l)],
                 [Paragraph(dest_name.upper()[:14], s_route_sub)]],
                colWidths=[None],
            ),
        ]],
        colWidths=[22 * mm, 18 * mm, 22 * mm],
    )
    route_card_inner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))

    # Junta título grande à esquerda + card visual à direita
    client_name = (
        (quote.presented_payload or {}).get("client_name")
        if isinstance(quote.presented_payload, dict) else None
    )
    subtitle_msg = (
        f"Proposta personalizada para <b>{client_name}</b> — com o melhor "
        "preço disponível e suporte completo."
        if client_name else
        "Proposta personalizada — com o melhor preço disponível e suporte completo."
    )
    title_block = Table(
        [
            [Paragraph(title_text, s_title_big)],
            [Paragraph(subtitle_msg, s_subtitle)],
        ],
        colWidths=[110 * mm],
    )
    title_block.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    title_row = Table(
        [[title_block, route_card_inner]],
        colWidths=[112 * mm, 66 * mm],
    )
    title_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(title_row)
    story.append(Spacer(1, 14))

    # ── Grid 6 cards de metadata (Partida, Retorno, Duração, Cia, Tarifa, Pax) ─
    s_meta_label = ParagraphStyle(
        "mtl", parent=styles["BodyText"], textColor=GRAY_500,
        fontSize=6.5, fontName="Helvetica-Bold", leading=9,
    )
    s_meta_value = ParagraphStyle(
        "mtv", parent=styles["BodyText"], textColor=GRAY_900,
        fontSize=9, fontName="Helvetica-Bold", leading=11,
    )
    s_meta_sub = ParagraphStyle(
        "mts", parent=styles["BodyText"], textColor=GRAY_500,
        fontSize=7, leading=9,
    )

    out_segs = (offer.get("outbound") or {}).get("segments") or []
    duration_str = "—"
    stops_str = "—"
    primary_carrier = "—"
    flight_no = ""
    if out_segs:
        from datetime import datetime as _dt
        try:
            dep = _dt.fromisoformat(str(out_segs[0].get("departure_dt", "")).replace("Z", "+00:00"))
            arr = _dt.fromisoformat(str(out_segs[-1].get("arrival_dt", "")).replace("Z", "+00:00"))
            mins = int((arr - dep).total_seconds() / 60)
            h, m = divmod(mins, 60)
            duration_str = f"{h}h {m:02d}min"
        except Exception:
            pass
        stops = len(out_segs) - 1
        stops_str = "Direto" if stops == 0 else f"{stops} escala(s)"
        primary_carrier = out_segs[0].get("carrier") or "—"
        flight_no = out_segs[0].get("flight_number") or ""

    cabin_label = _CABIN_LABEL.get(sr.get("cabin", "economy"), "Econômica")
    adults_count = int(sr.get("adults", 1) or 1)
    pax_str = f"{adults_count} Adulto{'s' if adults_count != 1 else ''}"
    if sr.get("children"):
        pax_str += f" + {sr['children']} Cri."
    if sr.get("infants"):
        pax_str += f" + {sr['infants']} Bebê(s)"

    def _meta_card(label: str, value: str, sub: str = "") -> Any:
        # Junta tudo num só Paragraph pra garantir alinhamento vertical e
        # mesma altura entre cards (rowHeights fixo na outer table).
        html = (
            f'<font color="#6b7280" size="6.5"><b>{label.upper()}</b></font><br/>'
            f'<font color="#111827" size="9"><b>{value}</b></font>'
        )
        if sub:
            html += f'<br/><font color="#6b7280" size="7">{sub}</font>'
        t = Table([[Paragraph(html, s_meta_value)]], colWidths=[None])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 0.6, GRAY_200),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        return t

    meta_grid = Table([[
        _meta_card("Partida", _format_date(date_start) or "—", "Trecho de ida"),
        _meta_card(
            "Retorno",
            _format_date(date_return) if date_return else "Somente ida",
            "Trecho de volta" if date_return else "Sem volta",
        ),
        _meta_card("Duração", duration_str, stops_str),
        _meta_card("Companhia", primary_carrier, flight_no or "—"),
        _meta_card("Tarifa", "Normal", cabin_label),
        _meta_card("Passageiros", pax_str, "—"),
    ]],
        colWidths=[29 * mm, 29 * mm, 29 * mm, 30 * mm, 27 * mm, 34 * mm],
        rowHeights=[20 * mm],   # força mesma altura → alinhamento perfeito
    )
    meta_grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1),
    ]))
    story.append(meta_grid)
    story.append(Spacer(1, 14))

    # ── Section: opção selecionada ───────────────────────────────────
    story.append(Paragraph("Opção selecionada", s_section))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GRAY_200, spaceAfter=8))

    # Grid de 3 preços
    total_price = _format_brl(offer.get("price_brl") or offer.get("equivalent_brl"))
    miles_str = None
    if offer.get("miles"):
        miles_v = offer.get("miles")
        tax = f" + {_format_brl(offer.get('taxes_brl'))}" if offer.get("taxes_brl") else ""
        miles_str = f"{miles_v:,}".replace(",", ".") + " mi" + tax

    # Estilo unificado: label SMALL+UPPERCASE em cima, value GRANDE+BOLD
    # embaixo. Mesma altura forçada via rowHeights. Diferença visual entre
    # cells fica só no destaque (fundo amarelo da Valor Total).
    s_value_unified = ParagraphStyle(
        "value-unified", parent=styles["BodyText"],
        textColor=GRAY_900, fontName="Helvetica-Bold",
        fontSize=14, leading=18,
    )
    s_value_brand = ParagraphStyle(
        "value-brand", parent=styles["BodyText"],
        textColor=BRAND_DARK, fontName="Helvetica-Bold",
        fontSize=14, leading=18,
    )

    def _price_cell(label: str, value_text: str, *,
                    highlight: bool = False, brand: bool = False):
        bg = colors.HexColor("#fffbeb") if highlight else GRAY_50
        border = colors.HexColor("#fde68a") if highlight else GRAY_200
        value_style = s_value_brand if brand else s_value_unified
        inner = Table(
            [
                [Paragraph(label.upper(), s_label)],
                [Paragraph(value_text, value_style)],
            ],
            colWidths=[None],
            rowHeights=[10, 28],   # label fixo curto, value com folga uniforme
        )
        inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), bg),
            ("BOX", (0, 0), (-1, -1), 0.6, border),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, 0), 10),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
            ("TOPPADDING", (0, 1), (-1, 1), 2),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
            ("VALIGN", (0, 1), (-1, 1), "MIDDLE"),
        ]))
        return inner

    # Decide preço efetivo: se há miles_alternative mais barata, usa ela
    eff = _effective_pricing(offer)
    if eff["mode"] == "miles_alternative":
        # Valor total = preço em milhas; mostra cash original como referência
        cells = [_price_cell("Valor total (em milhas)", eff["total_label"], highlight=True)]
        cells.append(_price_cell("Milhas + taxas", eff["miles_label"]))
        if eff.get("carrier_program"):
            cells.append(_price_cell("Programa", eff["carrier_program"], brand=True))
    elif eff["mode"] == "miles_original":
        cells = [_price_cell("Valor total estimado", eff["total_label"], highlight=True)]
        cells.append(_price_cell("Milhas + taxas", eff["miles_label"]))
        cells.append(_price_cell(
            "Tipo de oferta", offer.get("category") or "Padrão", brand=True,
        ))
    else:
        cells = [_price_cell("Valor total", total_price, highlight=True)]
        if miles_str:
            cells.append(_price_cell("Em milhas", miles_str))
        cells.append(_price_cell(
            "Tipo de oferta", offer.get("category") or "Padrão", brand=True,
        ))

    n = len(cells)
    col_w = 170 * mm / n
    # rowHeights força todas as células do grid externo à mesma altura
    # → alinhamento perfeito entre Valor Total, Em milhas e Tipo de Oferta.
    grid = Table([cells], colWidths=[col_w] * n, rowHeights=[22 * mm])
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(grid)
    story.append(Spacer(1, 14))

    # ── Detalhamento por passageiro (se houver crianças/bebês/+1 adulto) ─
    bd = offer.get("pax_breakdown")
    if bd and bd.get("lines"):
        story.append(Paragraph("Detalhamento por passageiro", s_section))
        story.append(HRFlowable(width="100%", thickness=0.5, color=GRAY_200, spaceAfter=8))

        s_pax_head = ParagraphStyle(
            "pax-head", parent=styles["BodyText"],
            textColor=GRAY_500, fontName="Helvetica-Bold",
            fontSize=8, leading=10, alignment=0,
        )
        s_pax_cell = ParagraphStyle(
            "pax-cell", parent=styles["BodyText"],
            textColor=GRAY_700, fontSize=9.5, leading=12,
        )
        s_pax_cell_r = ParagraphStyle(
            "pax-cell-r", parent=styles["BodyText"],
            textColor=GRAY_900, fontSize=9.5, leading=12,
            fontName="Helvetica-Bold", alignment=2,
        )
        s_pax_total = ParagraphStyle(
            "pax-total", parent=styles["BodyText"],
            textColor=BRAND_DARK, fontSize=11, leading=14,
            fontName="Helvetica-Bold", alignment=2,
        )
        s_pax_total_lbl = ParagraphStyle(
            "pax-total-lbl", parent=styles["BodyText"],
            textColor=GRAY_900, fontSize=10, leading=14,
            fontName="Helvetica-Bold",
        )

        is_miles = bool(bd.get("is_miles"))
        header = ["PASSAGEIRO", "QTD", "UNITÁRIO", "SUBTOTAL"]
        pax_rows: List[List[Any]] = [[
            Paragraph(h, s_pax_head) for h in header
        ]]
        for line in bd["lines"]:
            if is_miles:
                unit = ""
                if line.get("unit_miles"):
                    unit = f"{line['unit_miles']:,} mi".replace(",", ".")
                if line.get("unit_taxes_brl"):
                    unit += f"<br/>+ {_format_brl(line['unit_taxes_brl'])}"
                tot = ""
                if line.get("line_total_miles"):
                    tot = f"{line['line_total_miles']:,} mi".replace(",", ".")
                if line.get("line_total_taxes_brl"):
                    tot += f"<br/>+ {_format_brl(line['line_total_taxes_brl'])}"
            else:
                unit = _format_brl(line.get("unit_price_brl"))
                tot = _format_brl(line.get("line_total_brl"))
            pax_rows.append([
                Paragraph(line["label"], s_pax_cell),
                Paragraph(str(line["quantity"]), s_pax_cell),
                Paragraph(unit, s_pax_cell_r),
                Paragraph(tot, s_pax_cell_r),
            ])

        # Linha de total
        if is_miles:
            grand = ""
            if bd.get("grand_total_miles"):
                grand = f"{bd['grand_total_miles']:,} mi".replace(",", ".")
            if bd.get("grand_total_taxes_brl"):
                grand += f"<br/>+ {_format_brl(bd['grand_total_taxes_brl'])}"
        else:
            grand = _format_brl(bd.get("grand_total_brl"))
        pax_rows.append([
            Paragraph("TOTAL ESTIMADO", s_pax_total_lbl),
            Paragraph("", s_pax_cell),
            Paragraph("", s_pax_cell),
            Paragraph(grand, s_pax_total),
        ])

        pax_tbl = Table(pax_rows, colWidths=[70 * mm, 20 * mm, 40 * mm, 40 * mm])
        last_row_idx = len(pax_rows) - 1
        pax_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), GRAY_50),
            ("BOX", (0, 0), (-1, -1), 0.5, GRAY_200),
            ("INNERGRID", (0, 0), (-1, -2), 0.3, colors.HexColor("#f1f5f9")),
            ("LINEABOVE", (0, last_row_idx), (-1, last_row_idx), 1.2, BRAND),
            ("BACKGROUND", (0, last_row_idx), (-1, last_row_idx), colors.HexColor("#fef2f2")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(pax_tbl)

        if bd.get("has_estimate"):
            story.append(Spacer(1, 4))
            story.append(Paragraph(
                "<i>Tarifas de criança/bebê seguem convenção do mercado "
                "(bebê ≤2a ~10%, criança 2-11a ~75%). Confirmar valor final "
                "com a companhia antes de emitir.</i>",
                ParagraphStyle("pax-note", parent=styles["BodyText"],
                               textColor=GRAY_500, fontSize=8, leading=10),
            ))
        story.append(Spacer(1, 14))

    # ── Bloco "preferimos milhas" quando aplicável ────────────────────
    if eff["mode"] == "miles_alternative":
        savings = eff.get("savings_brl", 0)
        original = eff.get("original_cash_brl", 0)
        miles_note = (
            f"<b>Por que em milhas:</b> o mesmo bilhete em "
            f"{eff.get('carrier_program', 'milhas')} sai por <b>{eff['total_label']}</b> "
            f"({eff['miles_label']}), economizando "
            f"<b>{_format_brl(savings)}</b> frente ao cash ({_format_brl(original)})."
        )
        miles_tbl = Table(
            [[Paragraph(miles_note, s_why)]],
            colWidths=[170 * mm],
        )
        miles_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BLUE_50),
            ("LINEBEFORE", (0, 0), (0, -1), 4, colors.HexColor("#0284c7")),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(miles_tbl)
        story.append(Spacer(1, 10))

    # ── Why-box ──────────────────────────────────────────────────────
    if offer.get("category_why"):
        why_tbl = Table(
            [[Paragraph(
                f"<b>Por que esta opção:</b> {offer['category_why']}", s_why,
            )]],
            colWidths=[170 * mm],
        )
        why_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BLUE_50),
            ("LINEBEFORE", (0, 0), (0, -1), 4, colors.HexColor("#0284c7")),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(why_tbl)
        story.append(Spacer(1, 12))

    # ── Risk ─────────────────────────────────────────────────────────
    if offer.get("risk_notes"):
        risk_tbl = Table(
            [[Paragraph(
                f"<b>Aviso operacional:</b> {offer['risk_notes']}", s_risk,
            )]],
            colWidths=[170 * mm],
        )
        risk_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), AMBER_50),
            ("LINEBEFORE", (0, 0), (0, -1), 4, colors.HexColor("#f59e0b")),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(risk_tbl)
        story.append(Spacer(1, 12))

    # ── Itinerário ───────────────────────────────────────────────────
    story.append(Paragraph("Itinerário", s_section))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GRAY_200, spaceAfter=8))

    s_conn_ok = ParagraphStyle(
        "conn-ok", parent=styles["BodyText"],
        textColor=GRAY_500, fontSize=8.5, leading=11,
        leftIndent=12, fontName="Helvetica-Oblique",
    )
    s_conn_tight = ParagraphStyle(
        "conn-tight", parent=styles["BodyText"],
        textColor=AMBER_800, fontSize=9, leading=12,
        leftIndent=12, fontName="Helvetica-Bold",
    )
    s_ticket_label = ParagraphStyle(
        "ticket-label", parent=styles["BodyText"],
        textColor=BRAND_DARK, fontSize=9, leading=12,
        fontName="Helvetica-Bold",
    )

    # ── Seção "Como funciona o split" — só pra ofertas split ──────
    category = (offer.get("category") or "").lower()
    is_split = "split" in category
    if is_split:
        out_segs = (offer.get("outbound") or {}).get("segments") or []
        if len(out_segs) >= 2:
            story.append(Paragraph("Como funciona esta oferta (Split de trecho)", s_section))
            story.append(HRFlowable(width="100%", thickness=0.5, color=GRAY_200, spaceAfter=8))
            explain = (
                "Esta oferta combina <b>dois bilhetes emitidos separadamente</b>. "
                "O passageiro voa o primeiro trecho com um bilhete, faz a conexão "
                "no aeroporto intermediário, e embarca no segundo trecho com outro "
                "bilhete. Os bilhetes <b>não estão vinculados</b> — em caso de "
                "atraso ou cancelamento no primeiro voo, a segunda companhia "
                "<b>não tem obrigação de reacomodar</b> nem de aguardar."
            )
            story.append(Paragraph(explain, s_body))
            story.append(Spacer(1, 8))

            ticket_rows = []
            for i, seg in enumerate(out_segs, 1):
                carrier = seg.get("carrier") or "—"
                flight = seg.get("flight_number") or "—"
                dep = _format_dt(seg.get("departure_dt"))
                arr = _format_time(seg.get("arrival_dt"))
                ticket_rows.append([
                    Paragraph(f"<b>Bilhete {i}</b>", s_ticket_label),
                    Paragraph(
                        f"{carrier} {flight}<br/>"
                        f"{seg.get('origin')} → {seg.get('destination')}<br/>"
                        f"<font color='#6b7280'>{dep} → {arr}</font>",
                        s_body,
                    ),
                ])
            tickets_tbl = Table(ticket_rows, colWidths=[28 * mm, 150 * mm])
            tickets_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#fef2f2")),
                ("BOX", (0, 0), (-1, -1), 0.5, GRAY_200),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, GRAY_200),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(tickets_tbl)
            story.append(Spacer(1, 12))

    for label, key in (("Ida", "outbound"), ("Volta", "inbound")):
        itin_data = _build_segments(offer.get(key))
        segs_data = itin_data["segments"]
        conns = itin_data["connections"]
        if not segs_data:
            continue

        rows: List[List[Any]] = [[Paragraph(label.upper(), s_leg_header), "", ""]]
        # Intercala segmentos com linha de conexão.
        for i, seg in enumerate(segs_data):
            time_cell = f"{seg['dep_time']} → {seg['arr_time']}"
            route_cell = f"{seg['origin']} → {seg['destination']}"
            carrier = seg["carrier"]
            flight = seg.get("flight_number") or ""
            # Sigla do voo destacada: COD em negrito + número
            if flight:
                carrier_cell = f"<b>{carrier} {flight}</b>"
            else:
                carrier_cell = f"<b>{carrier}</b>"
            rows.append([
                Paragraph(time_cell, s_seg_time),
                Paragraph(route_cell, s_seg_route),
                Paragraph(carrier_cell, s_seg_carrier),
            ])
            # Linha de conexão (se houver próximo segmento)
            conn = next((c for c in conns if c["after_index"] == i), None)
            if conn:
                style = s_conn_tight if conn["severity"] == "tight" else s_conn_ok
                conn_text = f"↳ Conexão em <b>{conn['hub']}</b>: {conn['label']}"
                rows.append([Paragraph(conn_text, style), "", ""])

        leg_tbl = Table(rows, colWidths=[60 * mm, 70 * mm, 40 * mm])
        # Calcula índices das linhas de conexão pra destacar bg
        conn_row_indices = []
        row_pointer = 1
        for i in range(len(segs_data)):
            row_pointer += 1
            if any(c["after_index"] == i for c in conns):
                conn_row_indices.append((row_pointer, next(c for c in conns if c["after_index"] == i)))
                row_pointer += 1

        styles_list = [
            ("BACKGROUND", (0, 0), (-1, 0), GRAY_50),
            ("BOX", (0, 0), (-1, -1), 0.5, GRAY_200),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, GRAY_200),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("TOPPADDING", (0, 1), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
        # Span da linha de conexão (ocupa as 3 colunas) + bg
        for ridx, conn in conn_row_indices:
            styles_list.append(("SPAN", (0, ridx), (-1, ridx)))
            if conn["severity"] == "tight":
                styles_list.append(("BACKGROUND", (0, ridx), (-1, ridx), AMBER_50))
            else:
                styles_list.append(("BACKGROUND", (0, ridx), (-1, ridx), colors.HexColor("#fafafa")))
            styles_list.append(("TOPPADDING", (0, ridx), (-1, ridx), 4))
            styles_list.append(("BOTTOMPADDING", (0, ridx), (-1, ridx), 4))

        leg_tbl.setStyle(TableStyle(styles_list))
        story.append(leg_tbl)
        story.append(Spacer(1, 8))

    # ── Franquia de bagagem (3 cards) ─────────────────────────────────
    story.append(Spacer(1, 12))
    s_bag_label = ParagraphStyle(
        "bgl", parent=styles["BodyText"], textColor=GRAY_500,
        fontSize=7, fontName="Helvetica-Bold", leading=10,
    )
    s_bag_value = ParagraphStyle(
        "bgv", parent=styles["BodyText"], textColor=GRAY_900,
        fontSize=10, fontName="Helvetica-Bold", leading=13,
    )
    s_bag_sub = ParagraphStyle(
        "bgs", parent=styles["BodyText"], textColor=GRAY_500,
        fontSize=8, leading=10,
    )

    # Header pequeno azul-marinho
    bag_header = Table(
        [[Paragraph("<font color='#ffffff' size='8'><b>📋 FRANQUIA DE BAGAGEM</b></font>", s_body)]],
        colWidths=[170 * mm],
    )
    bag_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(bag_header)

    def _bag_card(label: str, value: str, sub: str, *, included: bool = True) -> Any:
        bg = colors.HexColor("#fffaf0") if included else GRAY_50
        border = colors.HexColor("#fef3c7") if included else GRAY_200
        t = Table([
            [Paragraph(label.upper(), s_bag_label)],
            [Paragraph(value, s_bag_value)],
            [Paragraph(sub, s_bag_sub)],
        ], colWidths=[None])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), bg),
            ("BOX", (0, 0), (-1, -1), 0.6, border),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        return t

    # Defaults inteligentes: econômica básica = só artigo pessoal + mão.
    # Despachada quase nunca inclusa em tarifas mais baratas.
    baggage_checked_included = bool(sr.get("baggage_checked", False))
    bag_grid = Table([[
        _bag_card("Artigo pessoal", "01 · Bolsa / Mochila", "Sob o assento da frente", included=True),
        _bag_card("Bagagem de mão", "01 · Mala Pequena (10kg)", "Até 10 kg · Compartimento superior", included=True),
        _bag_card(
            "Bagagem despachada",
            "01 · 23kg" if baggage_checked_included else "Não inclusa",
            "Inclusa na tarifa" if baggage_checked_included else "Adquira separadamente",
            included=baggage_checked_included,
        ),
    ]], colWidths=[57 * mm, 57 * mm, 56 * mm])
    bag_grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(bag_grid)
    story.append(Spacer(1, 16))

    # ── PÁGINA 2: VALOR + INCLUSO + INFORMAÇÕES ──────────────────────
    from reportlab.platypus import PageBreak
    story.append(PageBreak())

    # Header de valor (navy escuro)
    if eff["mode"] == "miles_alternative":
        valor_title_left = "VALOR TOTAL (EM MILHAS)"
        valor_principal = eff["total_label"]
        valor_sub = f"≈ {eff['miles_label']} · {eff.get('carrier_program', '')}"
    elif eff["mode"] == "miles_original":
        valor_title_left = "VALOR ESTIMADO"
        valor_principal = eff["total_label"]
        valor_sub = eff.get("miles_label", "Em milhas")
    else:
        valor_title_left = f"VALOR TOTAL — {COMPANY['name'].upper()}"
        valor_principal = total_price
        valor_sub = "Total da proposta"

    s_valor_lbl = ParagraphStyle(
        "vl", parent=styles["BodyText"], textColor=colors.HexColor("#a1b0d0"),
        fontSize=7.5, fontName="Helvetica-Bold", leading=10,
    )
    s_valor_big = ParagraphStyle(
        "vb", parent=styles["BodyText"], textColor=colors.white,
        fontSize=22, fontName="Helvetica-Bold", leading=26,
    )
    s_valor_sub = ParagraphStyle(
        "vs", parent=styles["BodyText"], textColor=colors.HexColor("#a1b0d0"),
        fontSize=8.5, leading=11,
    )

    valor_left = Table([
        [Paragraph(valor_title_left, s_valor_lbl)],
        [Paragraph(f"<b>R$</b> <b>{valor_principal.replace('R$ ', '')}</b>", s_valor_big)
            if "R$" in valor_principal else Paragraph(f"<b>{valor_principal}</b>", s_valor_big)],
        [Paragraph(valor_sub, s_valor_sub)],
    ], colWidths=[None])
    valor_right_html = (
        "<para align='right'>"
        "<font color='#a1b0d0' size='7'><b>PARCELAMENTO</b></font><br/>"
        "<font color='#ffffff' size='13'><b>CONSULTE OPÇÕES</b> 💳</font><br/>"
        "<font color='#a1b0d0' size='7'>NO CARTÃO DE CRÉDITO</font>"
        "</para>"
    )
    valor_band = Table(
        [[valor_left, Paragraph(valor_right_html, s_body)]],
        colWidths=[100 * mm, 78 * mm],
    )
    valor_band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(valor_band)
    story.append(Spacer(1, 16))

    # Header pequeno azul "O QUE ESTÁ INCLUSO"
    inc_header = Table(
        [[Paragraph("<font color='#ffffff' size='8'><b>✓  O QUE ESTÁ INCLUSO</b></font>", s_body)]],
        colWidths=[170 * mm],
    )
    inc_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(inc_header)
    story.append(Spacer(1, 6))

    # Bullets em grid 2 colunas
    trip_type_label = "Ida e volta" if is_round else "Somente Ida"
    airline_label = primary_carrier
    included_items = [
        f"Passagem aérea {trip_type_label} — {airline_label}",
        "Artigo pessoal (bolsa ou mochila)",
        "Bagagem de mão até 10 kg",
        "Todas as taxas aeroportuárias inclusas",
        "Assessoria completa durante todo o trajeto",
        "Atendimento personalizado 24 horas",
    ]
    s_inc = ParagraphStyle(
        "inc", parent=styles["BodyText"],
        textColor=GREEN_700, fontSize=9, leading=12,
    )

    def _inc_cell(text: str) -> Any:
        t = Table(
            [[Paragraph(f"<font color='#15803d'>✓</font>  {text}", s_inc)]],
            colWidths=[None],
        )
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), GREEN_50),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbf7d0")),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        return t

    pairs = []
    for i in range(0, len(included_items), 2):
        left = _inc_cell(included_items[i])
        right = _inc_cell(included_items[i + 1]) if i + 1 < len(included_items) else ""
        pairs.append([left, right])
    inc_grid = Table(pairs, colWidths=[85 * mm, 85 * mm])
    inc_grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(inc_grid)
    story.append(Spacer(1, 14))

    # Header "INFORMAÇÕES IMPORTANTES"
    inf_header = Table(
        [[Paragraph("<font color='#ffffff' size='8'><b>⚠  INFORMAÇÕES IMPORTANTES</b></font>", s_body)]],
        colWidths=[170 * mm],
    )
    inf_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(inf_header)
    story.append(Spacer(1, 6))

    s_disc = ParagraphStyle(
        "disc", parent=styles["BodyText"],
        textColor=RED_700, fontSize=9, leading=12,
    )
    fare_type = sr.get("cabin", "Econômica").title() if sr.get("cabin") else "Normal"
    disclaimer_texts = [
        "Valores sujeitos a alteração até a efetivação da compra. "
        "Recomendamos o fechamento em até 24h para garantir o preço cotado.",
        f"Cancelamentos e remarcações sujeitos às regras da {airline_label}, "
        f"conforme tarifa {fare_type}.",
        "Pagamentos via cartão de crédito podem estar sujeitos a taxas "
        "adicionais. Consulte as opções de parcelamento disponíveis.",
    ]
    for d in disclaimer_texts:
        disc_tbl = Table(
            [[Paragraph(f"<font color='#b91c1c'>⚠</font>  {d}", s_disc)]],
            colWidths=[170 * mm],
        )
        disc_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), RED_50),
            ("LINEBEFORE", (0, 0), (0, -1), 3, BRAND),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(disc_tbl)
        story.append(Spacer(1, 6))

    story.append(Spacer(1, 14))

    # Footer escuro — operado por + dados da empresa + redes
    s_foot_lbl = ParagraphStyle(
        "fl", parent=styles["BodyText"], textColor=colors.HexColor("#7a8db5"),
        fontSize=6.5, fontName="Helvetica-Bold", leading=9,
    )
    s_foot_val = ParagraphStyle(
        "fv", parent=styles["BodyText"], textColor=colors.white,
        fontSize=9, fontName="Helvetica-Bold", leading=12,
    )
    s_foot_sub = ParagraphStyle(
        "fs", parent=styles["BodyText"], textColor=colors.HexColor("#7a8db5"),
        fontSize=7, leading=10,
    )

    def _foot_col(label: str, value: str, sub: str = "") -> Any:
        rows = [[Paragraph(label.upper(), s_foot_lbl)],
                [Paragraph(value, s_foot_val)]]
        if sub:
            rows.append([Paragraph(sub, s_foot_sub)])
        t = Table(rows, colWidths=[None])
        t.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        return t

    web_ig_html = (
        f"<para align='right'>"
        f"<font color='#7a8db5' size='6.5'><b>WEB</b></font> "
        f"<font color='#ffffff' size='8'>{COMPANY['website']}</font><br/>"
        f"<font color='#7a8db5' size='6.5'><b>IG</b></font> "
        f"<font color='#ffffff' size='8'>{COMPANY['instagram']}</font>"
        "</para>"
    )
    footer = Table(
        [[
            _foot_col("Operado por", COMPANY["legal_name"],
                      f"{COMPANY['cadastur']} · CNPJ: {COMPANY['cnpj']}"),
            _foot_col("Data da cotação", issued_dt.strftime("%d %b %Y").upper()),
            Paragraph(web_ig_html, s_body),
        ]],
        colWidths=[68 * mm, 42 * mm, 68 * mm],
    )
    footer.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("LINEABOVE", (0, 0), (-1, -1), 2.5, BRAND),
    ]))
    story.append(footer)

    doc.build(story, onFirstPage=_draw_page_footer, onLaterPages=_draw_page_footer)
    return buf.getvalue()


def generate_quote_pdf(
    quote: Quote,
    user: User,
    offer: Dict[str, Any],
    *,
    notes: Optional[List[str]] = None,
    output_path: Optional[str] = None,
) -> bytes:
    """Gera PDF a partir da cotação + oferta selecionada (já sanitizada).

    Se `output_path` for fornecido, salva também no disco (útil pra cachear).
    Retorna sempre os bytes do PDF.
    """
    html = _render_html(quote, user, offer, notes=notes)
    pdf_bytes = _html_to_pdf_weasyprint(html)
    if pdf_bytes is None:
        pdf_bytes = _html_to_pdf_reportlab(quote, user, offer)

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as fh:
            fh.write(pdf_bytes)

    return pdf_bytes
