import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';

import { formatBRL } from '../helpers';

interface DayCell {
  iso: string;
  label: string;        // "qua, 15 mai"
  shortLabel: string;   // "15/05"
  price: number | null;
  isBest: boolean;
  isRequested: boolean;
  barPct: number;       // 0-100, height of the vertical bar
}

@Component({
  selector: 'app-price-calendar',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './price-calendar.html',
  styleUrl: './price-calendar.scss',
})
export class PriceCalendarComponent {
  @Input({ required: true }) priceMap!: Record<string, number>;
  @Input() requestedDate: string | null = null;
  @Input() bestDate: string | null = null;
  @Output() select = new EventEmitter<string>();

  get cells(): DayCell[] {
    const entries = Object.entries(this.priceMap || {});
    if (!entries.length) return [];
    const prices = entries.map(([, p]) => p).filter((p) => p > 0);
    if (!prices.length) return [];
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const range = Math.max(1, max - min);

    return entries
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([iso, price]) => ({
        iso,
        label: this.formatLabel(iso),
        shortLabel: this.formatShort(iso),
        price,
        isBest: iso === this.bestDate,
        isRequested: iso === this.requestedDate,
        // Cheaper = taller bar (so user reads it as "more value").
        barPct: price ? Math.max(8, 100 - ((price - min) / range) * 85) : 0,
      }));
  }

  get stats(): { avg: number; min: number; max: number } | null {
    const prices = Object.values(this.priceMap || {}).filter((p) => p > 0);
    if (!prices.length) return null;
    const sum = prices.reduce((a, b) => a + b, 0);
    return {
      avg: Math.round(sum / prices.length),
      min: Math.min(...prices),
      max: Math.max(...prices),
    };
  }

  formatPrice(v: number | null): string {
    if (v === null) return '—';
    return formatBRL(v);
  }

  onClick(iso: string): void {
    this.select.emit(iso);
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
}
