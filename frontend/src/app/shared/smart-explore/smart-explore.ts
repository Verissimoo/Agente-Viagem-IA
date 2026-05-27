import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  ExploreResponseDTO,
  QuoteForDateResponseDTO,
  UnifiedOffer,
} from '../../models/flight';
import { QuoteCompleteComponent } from '../quote-complete/quote-complete';
import { airlineKey, airlineName, formatBRL, formatDuration } from '../helpers';

interface MilesAirlineGroup {
  code: string;
  name: string;
  offers: UnifiedOffer[];
}

/**
 * Smart Quote — componente de visualização controlado pelo parent.
 *
 * Etapa 1 (calendário Kayak) e Etapa 2 (cotação completa milhas+Skiplagged)
 * são renderizadas aqui, mas TODA a orquestração HTTP fica no
 * SearchPageComponent. Isso é o que permite acoplar o toggle "Cotação
 * Inteligente" ao botão BUSCAR principal sem duplicar estado.
 */
@Component({
  selector: 'app-smart-explore',
  standalone: true,
  imports: [CommonModule, FormsModule, QuoteCompleteComponent],
  templateUrl: './smart-explore.html',
  styleUrl: './smart-explore.scss',
})
export class SmartExploreSectionComponent {
  @Input() result: ExploreResponseDTO | null = null;
  @Input() loading = false;
  @Input() error: string | null = null;

  @Input() quoteResult: QuoteForDateResponseDTO | null = null;
  @Input() quoting = false;
  @Input() quoteError: string | null = null;
  /** True quando o vendedor mudou a data depois de uma cotação Phase 2 anterior. */
  @Input() quoteStale = false;
  @Input() selectedDate: string | null = null;
  /** Sinaliza se o usuário já clicou em "Quebrar trecho nesta data" (parent
   * controla via smartSplitRequest). Quando false, escondemos os controles
   * de Hub/Bagagem porque eles só fazem sentido depois da quebra. */
  @Input() splitOpened = false;

  @Output() selectDate = new EventEmitter<string>();
  @Output() runQuote = new EventEmitter<string>();
  @Output() retryExplore = new EventEmitter<void>();
  /** Dispara a quebra de trecho — hub e bagagem ficam dentro do split-section
   * para que o vendedor configure SÓ depois de decidir quebrar. */
  @Output() runSplit = new EventEmitter<{ date: string }>();

  onSplitForDate(): void {
    if (!this.selectedDate) return;
    this.runSplit.emit({ date: this.selectedDate });
  }

  /** Células do gráfico com altura normalizada para o range observado. */
  get cells() {
    const r = this.result;
    if (!r) return [];
    const prices = r.days.map((d) => d.min_price_brl).filter((p): p is number => p != null && p > 0);
    if (!prices.length) {
      return r.days.map((d) => ({
        ...d,
        barPct: 0,
        label: this.formatLabel(d.date),
        shortLabel: this.formatShort(d.date),
      }));
    }
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const range = Math.max(1, max - min);

    // Escala proporcional ao preço: mais caro = barra MAIOR (vermelha quando
    // for "sua data") e mais barato = barra menor (verde quando for "melhor
    // dia"). Piso 30% mantém legibilidade do menor; teto 100% no mais caro.
    return r.days.map((d) => ({
      ...d,
      label: this.formatLabel(d.date),
      shortLabel: this.formatShort(d.date),
      barPct: d.min_price_brl ? Math.max(30, ((d.min_price_brl - min) / range) * 70 + 30) : 0,
    }));
  }

  /** Memo: agrupar por cia é puro, mas o template chama em ngFor — sem
   * memo, Angular dispara em cada CD, devolve array novo, e o ngFor recria
   * os flight-cards do zero (resetando o signal `expanded` do card). */
  private _cachedQuote: QuoteForDateResponseDTO | null = null;
  private _cachedGroups: MilesAirlineGroup[] = [];

  /** Agrupa as ofertas de milhas da Phase 2 por companhia operadora.
   * Espelha a UX legado: "LATAM (X) | GOL (Y) | AZUL (Z) | Internacional (W)".
   * Cada grupo é ordenado por equivalent_brl ascending. */
  milesByAirline(): MilesAirlineGroup[] {
    const q = this.quoteResult;
    // Identity check — quoteResult só muda quando o parent seta novo valor.
    if (q === this._cachedQuote) return this._cachedGroups;
    this._cachedQuote = q ?? null;
    this._cachedGroups = this.computeMilesGroups(q);
    return this._cachedGroups;
  }

  private computeMilesGroups(q: QuoteForDateResponseDTO | null): MilesAirlineGroup[] {
    if (!q?.miles_offers?.length) return [];
    const buckets = new Map<string, UnifiedOffer[]>();
    for (const o of q.miles_offers) {
      const seg = o.outbound?.segments?.[0];
      const code = airlineKey(seg?.carrier || o.airline || '') || 'OTHER';
      const bucket = ['LA', 'G3', 'AD'].includes(code) ? code : 'INTL';
      if (!buckets.has(bucket)) buckets.set(bucket, []);
      buckets.get(bucket)!.push(o);
    }
    const order = ['LA', 'G3', 'AD', 'INTL'];
    const out: MilesAirlineGroup[] = [];
    for (const code of order) {
      const offers = buckets.get(code);
      if (!offers?.length) continue;
      offers.sort((a, b) =>
        (a.equivalent_brl ?? Infinity) - (b.equivalent_brl ?? Infinity),
      );
      out.push({
        code,
        name: code === 'INTL' ? 'Internacional' : airlineName(code),
        offers,
      });
    }
    return out;
  }

  /** Memo do cash_offers top-N — mesmo problema do milesByAirline: chamar
   * slice() inline no template gera novo array a cada CD. */
  private _cachedCashQuote: QuoteForDateResponseDTO | null = null;
  private _cachedCashTopN: UnifiedOffer[] = [];

  cashOffersTopN(q: QuoteForDateResponseDTO): UnifiedOffer[] {
    if (q === this._cachedCashQuote) return this._cachedCashTopN;
    this._cachedCashQuote = q;
    this._cachedCashTopN = (q.cash_offers ?? []).slice(0, 6);
    return this._cachedCashTopN;
  }

  /** trackBy estável — o offer object referência permanece a mesma entre CDs
   * desde o cache em _cachedGroups. Usar a referência direta é o ideal. */
  trackOffer(_i: number, offer: UnifiedOffer): UnifiedOffer {
    return offer;
  }
  trackGroup(_i: number, group: MilesAirlineGroup): string {
    return group.code;
  }

  /** Carriers operando no dia selecionado (para o cabeçalho da Phase 2). */
  carriersForSelected(): string[] {
    const sel = this.selectedDate;
    const r = this.result;
    if (!sel || !r) return [];
    const day = r.days.find((d) => d.date === sel);
    return day?.carriers.map((c) => airlineName(c.iata)) ?? [];
  }

  onPickDate(iso: string): void {
    this.selectDate.emit(iso);
  }

  onQuoteForDate(): void {
    if (this.selectedDate) {
      this.runQuote.emit(this.selectedDate);
    }
  }

  formatPrice(v: number | null | undefined): string {
    if (v == null) return '—';
    return formatBRL(v);
  }

  formatLongDate(iso: string | null | undefined): string {
    if (!iso) return '—';
    const d = new Date(iso + 'T12:00:00');
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'long', year: 'numeric' });
  }

  private formatLabel(iso: string): string {
    const d = new Date(iso + 'T12:00:00');
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', weekday: 'short' });
  }

  private formatShort(iso: string): string {
    const d = new Date(iso + 'T12:00:00');
    if (isNaN(d.getTime())) return iso.slice(5);
    return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
  }

  airlineName = airlineName;
  formatDuration = formatDuration;
}
