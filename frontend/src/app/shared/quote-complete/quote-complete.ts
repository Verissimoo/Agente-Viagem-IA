import { CommonModule } from '@angular/common';
import { Component, Input, OnChanges, SimpleChanges, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/api.service';
import {
  CarrierBucketDTO,
  HiddenCityMilesQuoteDTO,
  QuoteForDateResponseDTO,
  SplitLegInputDTO,
  SplitMilesValidationResponseDTO,
  TableRowDTO,
  UnifiedOffer,
  VerdictCardDTO,
} from '../../models/flight';
import { airlineName, formatBRL, formatDuration, formatTime, sourceLabel } from '../helpers';

/**
 * Cotação Completa — espelha o legado:
 *   • Cabeçalho "Cotação Completa para X de Y"
 *   • Abas Veredito · Kayak · LATAM · GOL · AZUL · Internacional · Ranking Geral
 *   • Cada aba: tabela tipo planilha (13 colunas)
 *   • Veredito: 3 cards (Melhor achado/Milhas/Dinheiro) + Ranking por Cia + Summary
 *   • Itinerário Detalhado: dropdown por ID + segmento a segmento
 *
 * Recebe a QuoteForDateResponseDTO já estruturada pelo backend.
 */
@Component({
  selector: 'app-quote-complete',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './quote-complete.html',
  styleUrl: './quote-complete.scss',
})
export class QuoteCompleteComponent implements OnChanges {
  @Input({ required: true }) result!: QuoteForDateResponseDTO;

  private api = inject(ApiService);

  activeTab = signal<string>('VERDICT');
  selectedOfferIndex = signal<number>(0);
  /** Quando true, esconde linhas de milhas que NÃO apareceram no Economilhas
   * (fonte de verdade). Default off para o vendedor ver tudo; ligado vira
   * modo "100% confiável" filtrando apenas o que o Economilhas confirmou. */
  onlyValidated = signal(false);

  /** Tabs montadas a partir do bucket_order + tab Veredito no início. */
  tabs = computed<{ key: string; label: string; count: number; hasResults: boolean }[]>(() => {
    const r = this.result;
    if (!r) return [];
    const out: { key: string; label: string; count: number; hasResults: boolean }[] = [
      { key: 'VERDICT', label: '✨ O Veredito PcD', count: 0, hasResults: true },
    ];
    for (const code of r.bucket_order || []) {
      const b = r.buckets?.[code];
      if (!b) continue;
      const label =
        code === 'ALL'        ? '📊 Ranking Geral' :
        code === 'KAYAK'      ? '💵 Dinheiro (Kayak)' :
        code === 'LATAM'      ? '🟥 LATAM (milhas)' :
        code === 'GOL'        ? '🟧 GOL (milhas)' :
        code === 'AZUL'       ? '🟦 AZUL (milhas)' :
        code === 'INTL'       ? '🌎 Internacional (milhas)' :
        code === 'SKIPLAGGED' ? '🛬 Skiplagged (Hidden City)' :
        b.label;
      out.push({ key: code, label, count: b.rows.length, hasResults: b.has_results });
    }
    return out;
  });

  /** Rows da aba ativa filtradas pelo toggle "Somente validados".
   * Cash (Kayak/Skiplagged) sempre passa (não tem cross-validate aplicável). */
  allFilteredRows = computed<TableRowDTO[]>(() => {
    const bucket = this.currentBucket();
    if (!bucket) return [];
    if (!this.onlyValidated()) return bucket.rows;
    return bucket.rows.filter((r) => r.is_validated || r.miles == null);
  });

  // ── Paginação da planilha ──
  readonly PAGE_SIZE = 10;
  currentPage = signal(1);

  filteredRows = computed<TableRowDTO[]>(() => {
    const rows = this.allFilteredRows();
    const page = this.currentPage();
    return rows.slice(0, page * this.PAGE_SIZE);
  });

  totalPages = computed(() => {
    const n = this.allFilteredRows().length;
    return Math.max(1, Math.ceil(n / this.PAGE_SIZE));
  });

  hasMoreRows = computed(() => this.filteredRows().length < this.allFilteredRows().length);

  loadMoreRows(): void {
    this.currentPage.update((p) => p + 1);
  }

  resetPagination(): void {
    this.currentPage.set(1);
  }

  currentBucket = computed<CarrierBucketDTO | null>(() => {
    const tab = this.activeTab();
    if (tab === 'VERDICT') return null;
    return this.result?.buckets?.[tab] ?? null;
  });

  /** Oferta inteira referenciada pelo dropdown do itinerário. */
  selectedOffer = computed<UnifiedOffer | null>(() => {
    const idx = this.selectedOfferIndex();
    return this.result?.flat_offers?.[idx] ?? null;
  });

  /** Cotação em milhas para o itinerário oficial de um hidden city —
   * existe só pra rows de Skiplagged. Pesco na row IDA do bucket ALL
   * que tenha o mesmo offer_index da selecionada. Pode vir eager
   * (pré-carregado no /quote-for-date) ou lazy (carregado sob demanda
   * quando o vendedor abre a oferta). */
  hiddenCityMilesLoading = signal(false);
  hiddenCityMilesLazyCache = signal<Record<number, HiddenCityMilesQuoteDTO | null>>({});

  // ── Validação Split em milhas (Skiplagged split_cash) ──
  splitMilesLoading = signal(false);
  splitMilesLazyCache = signal<Record<number, SplitMilesValidationResponseDTO | null>>({});

  selectedSplitMilesValidation = computed<SplitMilesValidationResponseDTO | null>(() => {
    const idx = this.selectedOfferIndex();
    return this.splitMilesLazyCache()[idx] ?? null;
  });

  selectedHiddenCityMiles = computed<HiddenCityMilesQuoteDTO | null>(() => {
    const idx = this.selectedOfferIndex();
    const allRows = this.result?.buckets?.['ALL']?.rows ?? [];
    const row = allRows.find((r) => r.offer_index === idx && r.leg === 'IDA');
    // 1. Cota eager preenchida no backend (top N)
    if (row?.hidden_city_miles) return row.hidden_city_miles;
    // 2. Lazy buscado sob demanda
    const cached = this.hiddenCityMilesLazyCache()[idx];
    return cached ?? null;
  });

  /** Dispara fetch lazy quando o vendedor abre uma row Skiplagged que não
   * veio com cotação pré-carregada. Usa cache pra evitar re-fetch. */
  private _hcmLazyEffect = effect(() => {
    const idx = this.selectedOfferIndex();
    const offer = this.selectedOffer();
    if (!offer || offer.scenario !== 'hidden_city') return;

    const rows = this.result?.buckets?.['ALL']?.rows ?? [];
    const row = rows.find((r) => r.offer_index === idx && r.leg === 'IDA');
    // Já tem eager → não precisa lazy
    if (row?.hidden_city_miles) return;
    // Já está no cache (carregado ou marcado como vazio) → não busca de novo
    if (idx in this.hiddenCityMilesLazyCache()) return;

    // Extrai dados pra montar o request
    const segs = offer.outbound?.segments ?? [];
    if (!segs.length) return;
    const seg0 = segs[0];
    const segLast = segs[segs.length - 1];
    const carrier = (seg0.carrier || '').toUpperCase();
    const dateISO = (seg0.departure_dt || '').slice(0, 10);
    if (!carrier || !dateISO) return;

    const depTime = seg0.departure_dt ? seg0.departure_dt.slice(11, 16) : null;
    // Cash de referência = price_brl da row Skiplagged (preço hidden city)
    const cashRef = row?.price_brl ?? row?.real_cost_brl ?? null;
    this.hiddenCityMilesLoading.set(true);
    this.api.hiddenCityMiles({
      origin: seg0.origin,
      destination: segLast.destination,
      passenger_destination: offer.layover_city || segLast.destination,
      carrier_iata: carrier,
      date: dateISO,
      departure_time: depTime,
      adults: 1,
      cash_reference_brl: cashRef,
    }).subscribe({
      next: (resp) => {
        this.hiddenCityMilesLazyCache.update((m) => ({ ...m, [idx]: resp }));
        this.hiddenCityMilesLoading.set(false);
      },
      error: () => {
        this.hiddenCityMilesLazyCache.update((m) => ({ ...m, [idx]: null }));
        this.hiddenCityMilesLoading.set(false);
      },
    });
  });

  /** Dispara fetch lazy quando o vendedor seleciona uma linha Split do Skiplagged.
   * Cota cada perna em milhas (cia operadora + Economilhas) — só validação,
   * sem encaixe de hub. Cache evita re-fetch ao reselecionar. */
  private _splitLazyEffect = effect(() => {
    const idx = this.selectedOfferIndex();
    const offer = this.selectedOffer();
    if (!offer) return;
    if (offer.scenario !== 'split_cash' && offer.scenario !== 'split_miles') return;

    // Já buscado (resultado ou null em cache) → não refetcha
    if (idx in this.splitMilesLazyCache()) return;

    const segs = offer.outbound?.segments ?? [];
    if (!segs.length) return;

    // Cada segmento vira uma perna a validar. Em Split do Skiplagged cada
    // segmento é um voo independente (PNRs separados) — exatamente o que
    // queremos validar perna a perna.
    const legs: SplitLegInputDTO[] = segs
      .filter((s) => s.origin && s.destination && s.carrier && s.departure_dt)
      .map((s) => ({
        origin: s.origin,
        destination: s.destination,
        carrier_iata: (s.carrier || '').toUpperCase(),
        departure_dt: s.departure_dt!,
        arrival_dt: s.arrival_dt ?? null,
        flight_number: s.flight_number ?? null,
      }));
    if (!legs.length) return;

    const rows = this.result?.buckets?.['ALL']?.rows ?? [];
    const row = rows.find((r) => r.offer_index === idx && r.leg === 'IDA');
    const cashRef = row?.price_brl ?? row?.real_cost_brl ?? null;

    this.splitMilesLoading.set(true);
    this.api
      .splitMilesValidation({
        legs,
        cash_reference_brl: cashRef,
        adults: 1,
      })
      .subscribe({
        next: (resp) => {
          this.splitMilesLazyCache.update((m) => ({ ...m, [idx]: resp }));
          this.splitMilesLoading.set(false);
        },
        error: () => {
          this.splitMilesLazyCache.update((m) => ({ ...m, [idx]: null }));
          this.splitMilesLoading.set(false);
        },
      });
  });

  /** Lista flat de ofertas com label "$N - companhia | preço" para o select. */
  offerOptions = computed<{ index: number; label: string }[]>(() => {
    const flat = this.result?.flat_offers ?? [];
    return flat.map((o, i) => {
      const seg = o.outbound?.segments?.[0];
      const carrier = seg?.carrier || o.airline || '?';
      const name = airlineName(carrier);
      const price = o.miles
        ? `${formatBRL(o.miles)} mi + R$ ${formatBRL(o.taxes_brl ?? 0)}`
        : `R$ ${formatBRL(o.price_brl ?? o.equivalent_brl)}`;
      return { index: i, label: `$${i + 1} — ${name} | ${price}` };
    });
  });

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['result']) {
      this.activeTab.set('VERDICT');
      this.selectedOfferIndex.set(0);
      this.hiddenCityMilesLazyCache.set({});
      this.splitMilesLazyCache.set({});
      this.currentPage.set(1);
      this.hiddenCityMilesLoading.set(false);
      this.splitMilesLoading.set(false);
    }
  }

  setTab(t: string): void {
    this.activeTab.set(t);
    this.currentPage.set(1);
  }

  selectOffer(idx: number | string): void {
    this.selectedOfferIndex.set(Number(idx));
  }

  /** Carrega o estilo do badge da tabela (verde se é a melhor da aba). */
  isBestRow(row: TableRowDTO, bucket: CarrierBucketDTO | null): boolean {
    return !!bucket?.best && bucket.best.id === row.id && bucket.best.leg === row.leg;
  }

  trackTab = (_: number, t: { key: string }) => t.key;
  trackRow = (_: number, r: TableRowDTO) => r.id + r.leg;
  trackVerdict = (_: number, v: VerdictCardDTO) => v.kind;
  trackBucket = (_: number, b: CarrierBucketDTO) => b.code;
  trackOption = (_: number, o: { index: number }) => o.index;

  formatBRL = formatBRL;
  formatTime = formatTime;
  formatDuration = formatDuration;
  airlineName = airlineName;
  sourceLabel = sourceLabel;

  /** Data formatada no cabeçalho ("1 de junho de 2026"). */
  longDate(iso: string): string {
    const d = new Date(iso + 'T12:00:00');
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('pt-BR', { day: 'numeric', month: 'long', year: 'numeric' });
  }
}
