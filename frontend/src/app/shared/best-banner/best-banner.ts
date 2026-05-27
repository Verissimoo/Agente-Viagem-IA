import { CommonModule } from '@angular/common';
import { Component, Input, computed, signal } from '@angular/core';

import { UnifiedOffer } from '../../models/flight';
import { airlineName, formatBRL, formatDuration, sourceLabel } from '../helpers';

interface BestCardData {
  label: string;
  primary: string;
  equivalent?: string;
  secondary: string;
  tertiary: string;
  featured: boolean;
}

function offerKey(o: UnifiedOffer | null | undefined): string {
  if (!o) return '';
  const dep = o.outbound?.segments?.[0]?.departure_dt ?? '';
  return [o.source, o.airline, o.miles ?? '', o.price_brl ?? '', dep].join('|');
}

@Component({
  selector: 'app-best-banner',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './best-banner.html',
  styleUrl: './best-banner.scss',
})
export class BestBannerComponent {
  @Input() set bestOverall(v: UnifiedOffer | null | undefined) {
    this._bestOverall.set(v ?? null);
  }
  @Input() set bestMiles(v: UnifiedOffer | null | undefined) {
    this._bestMiles.set(v ?? null);
  }
  @Input() set bestMoney(v: UnifiedOffer | null | undefined) {
    this._bestMoney.set(v ?? null);
  }

  private _bestOverall = signal<UnifiedOffer | null>(null);
  private _bestMiles = signal<UnifiedOffer | null>(null);
  private _bestMoney = signal<UnifiedOffer | null>(null);

  /** Returns up to 3 *distinct* cards: never repeat the same offer in 2 slots. */
  cards = computed<BestCardData[]>(() => {
    const overall = this._bestOverall();
    const miles = this._bestMiles();
    const money = this._bestMoney();

    const seen = new Set<string>();
    const out: BestCardData[] = [];

    const add = (o: UnifiedOffer | null, label: string, featured: boolean) => {
      if (!o) return;
      const k = offerKey(o);
      if (!k || seen.has(k)) return;
      seen.add(k);
      out.push(this.toCard(o, label, featured));
    };

    add(overall, 'Melhor geral', true);
    add(miles, 'Melhor em milhas', false);
    add(money, 'Melhor em dinheiro', false);

    return out;
  });

  private toCard(o: UnifiedOffer, label: string, featured: boolean): BestCardData {
    const isMiles = o.miles != null;
    let primary: string;
    let equivalent: string | undefined;

    if (isMiles) {
      const taxes = o.taxes_brl ? ` + R$ ${formatBRL(o.taxes_brl)}` : '';
      primary = `${formatBRL(o.miles)} milhas${taxes}`;
      if (o.equivalent_brl) {
        equivalent = `≈ R$ ${formatBRL(o.equivalent_brl)} total`;
      }
    } else {
      primary = `R$ ${formatBRL(o.price_brl ?? o.equivalent_brl)}`;
    }

    return {
      label,
      primary,
      equivalent,
      secondary: `${airlineName(o.airline)} · ${sourceLabel(o.source)}`,
      tertiary: this.routeLineFor(o),
      featured,
    };
  }

  private routeLineFor(o: UnifiedOffer): string {
    const segs = o.outbound.segments;
    if (!segs?.length) return '';
    const route = `${segs[0].origin} → ${segs[segs.length - 1].destination}`;
    const stops = Math.max(0, segs.length - 1);
    const stopLabel = stops === 0 ? 'Direto' : `${stops} parada${stops > 1 ? 's' : ''}`;
    const dur = o.outbound.duration_min ? ` · ${formatDuration(o.outbound.duration_min)}` : '';
    return `${route} · ${stopLabel}${dur}`;
  }
}
