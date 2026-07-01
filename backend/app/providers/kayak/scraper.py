"""Kayak.com.br scraper — fonte direta sem intermediário RapidAPI.

Espelha o padrão Skiplagged: tenta httpx primeiro (provavelmente bloqueado),
cai pra Playwright headless. Como o Kayak não expõe um endpoint JSON público,
a estratégia aqui é renderizar a página de resultados e extrair os cards do
DOM via JavaScript (`page.evaluate`).

URL pública:
- One-way: https://www.kayak.com.br/flights/{O}-{D}/{YYYY-MM-DD}
- Round-trip: https://www.kayak.com.br/flights/{O}-{D}/{YYYY-MM-DD}/{YYYY-MM-DD}

Sort fixo em price ascendente (`?sort=price_a`) e moeda em BRL via URL local.
Devolve uma estrutura intermediária parseável por `parser_scrape.extract_offers`.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

KAYAK_HOST = "https://www.kayak.com.br"

DEBUG_DIR = Path("debug") / "kayak_scrape"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_TIMEOUT_MS = int(os.getenv("KAYAK_SCRAPE_TIMEOUT_MS", "45000"))
RESULTS_WAIT_MS = int(os.getenv("KAYAK_SCRAPE_WAIT_MS", "18000"))


def _build_url(origin: str, destination: str, depart: str, return_: Optional[str]) -> str:
    """Monta URL pública do Kayak.com.br pra search.

    Ex: https://www.kayak.com.br/flights/BSB-LIS/2026-06-10?sort=price_a
    """
    o = origin.upper()
    d = destination.upper()
    path = f"/flights/{o}-{d}/{depart}"
    if return_:
        path += f"/{return_}"
    return f"{KAYAK_HOST}{path}?sort=price_a"


def _debug_dump(payload: Any, origin: str, destination: str, depart: str, suffix: str) -> None:
    try:
        ts = int(time.time())
        fname = DEBUG_DIR / f"raw_{origin}_{destination}_{depart}_{suffix}_{ts}.json"
        fname.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass


# Script JS executado no contexto da página pra extrair os cards de resultado.
# Estratégia agnóstica de classe (Kayak ofusca tudo): varremos TODOS os elementos
# da página, identificamos containers que parecem "card de voo" pela presença
# simultânea de (R$ valor) + (horário HH:MM) no texto, e extraímos dali os
# campos via regex sobre o innerText. Robusto contra renomes de class names.
_EXTRACTION_JS = r"""
() => {
  const out = [];
  const seen = new Set();

  // Lista de elementos candidatos: divs/lis/articles que parecem ser CARDS
  // DE VOO REAIS. Critérios obrigatórios:
  //  1) Texto contém preço (R$ X) E horário (HH:MM)
  //  2) Tem link de booking (<a href="/book/...">) — descarta widgets
  //     de "Datas flexíveis", banners, anúncios e mini-cards adjacentes
  //  3) Texto não contém palavras-chave de widget secundário
  const all = document.querySelectorAll('div, li, article, section');
  const candidates = [];
  for (const el of all) {
    const txt = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (!txt || txt.length < 50 || txt.length > 1500) continue;
    if (!/R\$\s*[\d.,]+/.test(txt)) continue;
    if (!/\b\d{1,2}[:h]\d{2}\b/.test(txt)) continue;

    // Skip widgets/banners: "Datas flexíveis", "Anteriores", "Próximos",
    // "Filtros Inteligentes" etc. Esses contêm preços mas não são cards.
    if (/Datas flex[ií]veis|Filtros Inteligentes|Anuncio|Patrocinado|Compare com|Reserve agora/i.test(txt)) {
      continue;
    }

    // Skip se NÃO houver link de booking — cards reais sempre têm "Selecionar"
    // ou "Reservar" linkando pra /book/... ou /in?... (página de redirect OTA).
    const hasBookingLink = el.querySelector('a[href*="/book/"], a[href*="/in?"], a[href*="/r/"], button[class*="book"], [data-test*="book"]');
    if (!hasBookingLink) continue;

    candidates.push({ el, txt, len: txt.length });
  }

  // Pega só os candidatos "mais internos" — descarta containers que envolvem
  // múltiplos cards (texto muito longo, contém múltiplos preços).
  // Heurística: ordena por tamanho do texto, mantém os menores cuja contagem
  // de "R$" é exatamente 1 (idealmente) ou no máximo 2 (preço duplicado).
  const filtered = candidates
    .filter((c) => {
      const priceMatches = c.txt.match(/R\$\s*[\d.,]+/g) || [];
      return priceMatches.length >= 1 && priceMatches.length <= 3;
    })
    .sort((a, b) => a.len - b.len);

  for (const { el, txt } of filtered) {
    // Dedup: usa o primeiro padrão preço+horário como assinatura
    const sigMatch = txt.match(/R\$\s*([\d.,]+).{0,400}?(\d{1,2}[:h]\d{2})/);
    const sig = sigMatch ? sigMatch[0].slice(0, 80) : txt.slice(0, 80);
    if (seen.has(sig)) continue;

    // Filtra containers-pai: se ALGUM filho já capturado contém este texto
    // (substring), pula — significa que pegamos o pai de cards já capturados.
    let isParent = false;
    for (const prev of seen) {
      if (txt.includes(prev) && txt.length > prev.length * 1.5) {
        isParent = true;
        break;
      }
    }
    if (isParent) continue;

    // Preço — pega o MENOR (Kayak às vezes mostra preço normal + preço pra
    // outra pessoa); o que importa pro vendedor é o menor por adulto.
    const priceMatches = txt.match(/R\$\s*([\d.,]+)/g) || [];
    let priceNum = null;
    for (const pm of priceMatches) {
      const ps = pm.replace(/R\$\s*/, '').replace(/\./g, '').replace(',', '.');
      const n = parseFloat(ps);
      if (isFinite(n) && n >= 100 && (priceNum === null || n < priceNum)) {
        priceNum = n;
      }
    }
    if (priceNum === null) continue;

    // Horários — pega TODOS os HH:MM no texto. O primeiro é depart, o
    // segundo é arrival da ida. (Em RT haveria mais — capturamos só ida.)
    const timeMatches = (txt.match(/\b(\d{1,2}[:h]\d{2})\b/g) || []).map(t => t.replace('h', ':'));
    const depTime = timeMatches[0] || null;
    const arrTime = timeMatches[1] || null;
    if (!depTime || !arrTime) continue;  // sem horário, descarta

    // Duração total — o card lista DURAÇÃO TOTAL + tempo de escala. Queremos
    // a MAIOR (total da viagem); a menor seria a escala.
    const durMatches = [...txt.matchAll(/(\d+)\s*h\s*(\d+)?\s*min/gi)];
    let durationMin = null;
    for (const dm of durMatches) {
      const h = parseInt(dm[1], 10);
      const m = dm[2] ? parseInt(dm[2], 10) : 0;
      const total = h * 60 + m;
      if (durationMin === null || total > durationMin) {
        durationMin = total;
      }
    }

    // Paradas
    let stops = 0;
    if (/direto|sem escala|nonstop/i.test(txt)) {
      stops = 0;
    } else {
      const stopsMatch = txt.match(/(\d+)\s*(?:parada|escala|stop)/i);
      if (stopsMatch) stops = parseInt(stopsMatch[1], 10);
    }

    // Companhia — exige nome em lista de cias conhecidas. Sem isso, o
    // scraper pega banners/widgets cujo "alt" não é uma cia (gera "EX",
    // "Imagem", etc no parser). Mais restritivo, mas garante data quality.
    let airline = null;
    const knownCarriers = /\b(LATAM Airlines|LATAM|GOL Linhas Aéreas|GOL|Azul Linhas Aéreas|Azul|TAP Air Portugal|TAP|Iberia|Lufthansa|Air France|KLM|American Airlines|American|Delta|United Airlines|United|Avianca|Copa Airlines|Copa|British Airways|British|Emirates|Qatar Airways|Qatar|Turkish Airlines|Turkish|Aerolineas Argentinas|Aeromexico|Air Europa|JetSMART|ITA Airways|Etihad|Swiss|Iberojet|Volaris|Alaska|Frontier|Spirit|JetBlue|Singapore|Cathay|Korean|Japan Airlines|ANA|China Eastern|China Southern|Asiana|Aerolíneas Argentinas|LATAM Brasil)\b/i;
    const m = txt.match(knownCarriers);
    if (m) airline = m[1];

    // Fallback img alt — só se contiver palavra-chave de cia conhecida
    if (!airline) {
      const imgAlt = el.querySelector && el.querySelector('img[alt]:not([alt=""])');
      if (imgAlt) {
        const alt = imgAlt.getAttribute('alt') || '';
        if (alt.length > 2 && knownCarriers.test(alt) && !/logo|ícone|icon|banner|promo/i.test(alt)) {
          airline = alt;
        }
      }
    }
    if (!airline) continue;  // sem cia conhecida, descarta

    // IATAs no texto (origem/destino)
    const iataMatches = txt.match(/\b([A-Z]{3})\b/g) || [];
    const originIata = iataMatches[0] || null;
    const destIata = iataMatches[iataMatches.length - 1] || null;

    // Deeplink
    let deeplink = null;
    const linkEl = el.querySelector && el.querySelector('a[href*="/book/"], a[href*="/in?"], a[role="link"][href]');
    if (linkEl) {
      const href = linkEl.getAttribute('href');
      if (href) deeplink = href.startsWith('http') ? href : ('https://www.kayak.com.br' + href);
    }

    seen.add(sig);
    out.push({
      airline: airline,
      price_brl: priceNum,
      currency: 'BRL',
      origin_iata: originIata,
      dest_iata: destIata,
      depart_time: depTime,
      arrival_time: arrTime,
      duration_min: durationMin,
      stops: stops,
      deeplink: deeplink,
      raw_excerpt: txt.slice(0, 200),
    });
  }
  return out;
}
"""


def fetch_via_playwright(
    origin: str,
    destination: str,
    depart: str,
    return_: Optional[str] = None,
    adults: int = 1,
    wait_ms: int = RESULTS_WAIT_MS,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> Optional[dict]:
    """Abre kayak.com.br/flights/... headless e extrai os cards renderizados.

    Devolve um dict {"offers": [...], "url": ..., "scraped_at": ...} ou None
    se nada foi capturado. Não lança — adapter trata como [].
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    target_url = _build_url(origin, destination, depart, return_)
    scraped_offers: list[dict] = []
    debug_payload: dict[str, Any] = {"url": target_url}

    try:
        from backend.app.infrastructure.browser import browser_slot
        with browser_slot(), sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            )
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    locale="pt-BR",
                    viewport={"width": 1366, "height": 900},
                )
                # Esconde flag de webdriver (Kayak inspeciona navigator.webdriver)
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
                )

                page = context.new_page()
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
                except Exception as e:
                    debug_payload["goto_error"] = str(e)

                # Kayak preenche resultados incrementalmente. Como as classes
                # são ofuscadas, esperamos pela aparição do PADRÃO de texto
                # de preço ("R$ X,XX") em qualquer lugar — sinal de que os
                # cards renderizaram.
                try:
                    page.wait_for_function(
                        "() => /R\\$\\s*\\d/.test(document.body.innerText)",
                        timeout=wait_ms,
                    )
                except Exception:
                    debug_payload["price_timeout"] = True

                # Tempo extra pra polling complementar (Kayak adiciona ofertas em ondas)
                page.wait_for_timeout(min(10000, wait_ms // 2))

                try:
                    scraped_offers = page.evaluate(_EXTRACTION_JS) or []
                except Exception as e:
                    debug_payload["evaluate_error"] = str(e)

                # Screenshot debug se nada veio
                if not scraped_offers:
                    try:
                        screenshot_path = DEBUG_DIR / f"empty_{origin}_{destination}_{depart}_{int(time.time())}.png"
                        page.screenshot(path=str(screenshot_path), full_page=False)
                        debug_payload["screenshot"] = str(screenshot_path)
                    except Exception:
                        pass
            finally:
                browser.close()
    except Exception as e:
        debug_payload["fatal_error"] = str(e)
        _debug_dump(debug_payload, origin, destination, depart, "playwright_err")
        return None

    debug_payload["count"] = len(scraped_offers)
    debug_payload["offers"] = scraped_offers
    _debug_dump(debug_payload, origin, destination, depart, "playwright")

    if not scraped_offers:
        return None

    return {
        "url": target_url,
        "scraped_at": int(time.time()),
        "offers": scraped_offers,
        "trip_type": "roundtrip" if return_ else "oneway",
        "origin": origin.upper(),
        "destination": destination.upper(),
        "depart_date": depart,
        "return_date": return_,
    }


def search_kayak_scrape(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: Optional[str] = None,
    adults: int = 1,
) -> Optional[dict]:
    """Entrada pública. Mesma assinatura simplificada do `fetch_skiplagged`."""
    return fetch_via_playwright(
        origin=origin,
        destination=destination,
        depart=departure_date,
        return_=return_date,
        adults=adults,
    )


# ────────────────────────────────────────────────────────────────────────────
# Matriz "Datas flexíveis" — busca multi-data numa única call.
#
# Quando o usuário usa Cotação Inteligente com flex ±N dias, o backend ANTES
# fazia N+N+1 buscas paralelas (uma URL por data). Cada busca devolvia preços
# diferentes dos que o Kayak mostra na matriz flex no topo da página (que é
# um agregado curado). Resultado: data central batia, datas adjacentes não.
#
# Solução: pra Cotação Inteligente, raspamos a URL `-flexible-Ndays` UMA VEZ
# e extraímos a matriz inteira. Os preços passam a bater com o site exato.
# ────────────────────────────────────────────────────────────────────────────


def _matrix_url(origin: str, destination: str, center_date: str, flex_days: int) -> str:
    """URL pública do Kayak com matriz de datas flexíveis aberta.

    Kayak suporta sufixo `-flexible-Ndays` onde N é o range ± dias.
    """
    o = origin.upper()
    d = destination.upper()
    # Kayak aceita "flexible-3days" (±3 = 7 datas total). Limita a 7.
    n = max(1, min(int(flex_days), 7))
    return f"{KAYAK_HOST}/flights/{o}-{d}/{center_date}-flexible-{n}days?sort=bestflight_a"


# JS pra extrair a matriz de datas flexíveis.
#
# Formato observado no Kayak.com.br: as datas vêm TODAS primeiro (uma por
# linha), depois TODOS os preços (uma por linha). Não é intercalado.
# Ex.:
#   sex, 12 jun
#   sáb, 13 jun
#   ...
#   R$ 916
#   R$ 673
#   ...
# Estratégia: extrai todas as datas em ordem, extrai todos os preços em
# ordem, e pareia por índice.
_MATRIX_EXTRACTION_JS = r"""
() => {
  // Acha o widget "Datas flexíveis" pelo texto-âncora
  const allNodes = document.querySelectorAll('div, section, table, ul');
  let widgetRoot = null;
  for (const el of allNodes) {
    const t = (el.innerText || '').trim();
    if (!t) continue;
    if (t.startsWith('Datas flexíveis') && t.length < 2000) {
      widgetRoot = el;
      break;
    }
  }
  if (!widgetRoot) return [];

  const monthMap = {
    jan: 1, fev: 2, mar: 3, abr: 4, mai: 5, jun: 6,
    jul: 7, ago: 8, set: 9, out: 10, nov: 11, dez: 12,
  };

  const inner = widgetRoot.innerText;

  // Datas: "sex, 12 jun" ou "sáb, 13 jun" (dia da semana + dia + mês PT-BR).
  // Capturamos em ORDEM.
  const dateRegex = /(?:dom|seg|ter|qua|qui|sex|sáb|sab)[,\.\s]+(\d{1,2})\s+(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)/gi;
  const dates = [];
  let dm;
  while ((dm = dateRegex.exec(inner)) !== null) {
    const day = parseInt(dm[1], 10);
    const month = monthMap[dm[2].toLowerCase()];
    if (month) dates.push({ day, month });
  }

  // Preços: "R$ X.XXX" ou "R$ X.XXX" (non-breaking space).
  // [\s ] captura ambos.
  const priceRegex = /R\$[\s ]*([\d.,]+)/g;
  const prices = [];
  let pm;
  while ((pm = priceRegex.exec(inner)) !== null) {
    const ps = pm[1].replace(/\./g, '').replace(',', '.');
    const n = parseFloat(ps);
    if (isFinite(n) && n >= 50) prices.push(n);
  }

  // Pareamento por índice. Se tem mais preços que datas, ignora as sobras
  // (podem ser preços de outras seções dentro do widget).
  const out = [];
  const seen = new Set();
  const n = Math.min(dates.length, prices.length);
  for (let i = 0; i < n; i++) {
    const key = `${dates[i].month}-${dates[i].day}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ month: dates[i].month, day: dates[i].day, price_brl: prices[i] });
  }
  return out;
}
"""


def fetch_matrix_via_playwright(
    origin: str,
    destination: str,
    center_date: str,
    flex_days: int = 3,
    wait_ms: int = RESULTS_WAIT_MS,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> Optional[dict]:
    """Raspagem da matriz de datas flexíveis numa única call.

    Devolve {"prices_by_date": {YYYY-MM-DD: float}, "url": str, ...}
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    target_url = _matrix_url(origin, destination, center_date, flex_days)
    cells: list[dict] = []
    debug_payload: dict[str, Any] = {"url": target_url}

    try:
        from backend.app.infrastructure.browser import browser_slot
        with browser_slot(), sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            )
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    locale="pt-BR",
                    viewport={"width": 1366, "height": 900},
                )
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
                )

                page = context.new_page()
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
                except Exception as e:
                    debug_payload["goto_error"] = str(e)

                # Espera o widget de matriz carregar — texto âncora "Datas flexíveis"
                try:
                    page.wait_for_function(
                        "() => /Datas flex[ií]veis/.test(document.body.innerText) && /R\\$\\s*\\d/.test(document.body.innerText)",
                        timeout=wait_ms,
                    )
                except Exception:
                    debug_payload["widget_timeout"] = True

                # Tempo extra pra preços assentarem
                page.wait_for_timeout(min(8000, wait_ms // 2))

                try:
                    cells = page.evaluate(_MATRIX_EXTRACTION_JS) or []
                except Exception as e:
                    debug_payload["evaluate_error"] = str(e)

                if not cells:
                    try:
                        screenshot_path = DEBUG_DIR / f"matrix_empty_{origin}_{destination}_{center_date}_{int(time.time())}.png"
                        page.screenshot(path=str(screenshot_path), full_page=False)
                        debug_payload["screenshot"] = str(screenshot_path)
                    except Exception:
                        pass
            finally:
                browser.close()
    except Exception as e:
        debug_payload["fatal_error"] = str(e)
        _debug_dump(debug_payload, origin, destination, center_date, "matrix_err")
        return None

    debug_payload["cells"] = cells
    _debug_dump(debug_payload, origin, destination, center_date, "matrix")

    if not cells:
        return None

    # Resolve ano: usa o ano da `center_date`; se mês < mês central, assume
    # ano seguinte (cobre matriz que passa por virada de ano).
    try:
        from datetime import date as _date
        center_dt = _date.fromisoformat(center_date)
    except (ValueError, TypeError):
        return None

    prices_by_date: dict[str, float] = {}
    for cell in cells:
        year = center_dt.year
        if cell["month"] < center_dt.month - 6:
            year += 1
        elif cell["month"] > center_dt.month + 6:
            year -= 1
        try:
            iso = _date(year, cell["month"], cell["day"]).isoformat()
        except ValueError:
            continue
        prices_by_date[iso] = cell["price_brl"]

    return {
        "url": target_url,
        "scraped_at": int(time.time()),
        "center_date": center_date,
        "flex_days": flex_days,
        "prices_by_date": prices_by_date,
    }


def fetch_kayak_matrix(
    origin: str,
    destination: str,
    center_date: str,
    flex_days: int = 3,
) -> Optional[dict]:
    """Entrada pública pra raspagem da matriz."""
    return fetch_matrix_via_playwright(
        origin=origin,
        destination=destination,
        center_date=center_date,
        flex_days=flex_days,
    )
