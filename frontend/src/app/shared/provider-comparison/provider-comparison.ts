import { CommonModule } from '@angular/common';
import { Component, Input, computed, signal } from '@angular/core';

import { SearchResponseDTO, UnifiedOffer } from '../../models/flight';
import { airlineKey, airlineName, formatBRL } from '../helpers';

/** A single program row showing the best price across Economilhas, BuscaMilhas
 * and Kayak (cash baseline). */
interface ComparisonRow {
  airline: string;        // "GOL", "LATAM", "AZUL", ...
  airlineLabel: string;   // "GOL", "LATAM", "Azul"
  econ: PriceCell | null;
  bm:   PriceCell | null;
  cash: PriceCell | null;
  bestSource: 'econ' | 'bm' | 'cash' | null;
}

interface PriceCell {
  miles?: number | null;
  taxes?: number | null;
  equivalentBRL: number;
  cashBRL?: number | null;
}

// Order by IATA code (since we normalize via airlineKey).
const AIRLINE_ORDER = ['G3', 'LA', 'AD', 'TP', 'AA', 'IB', 'CM'];

@Component({
  selector: 'app-provider-comparison',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './provider-comparison.html',
  styleUrl: './provider-comparison.scss',
})
export class ProviderComparisonComponent {
  @Input() set response(v: SearchResponseDTO | null) {
    this._resp.set(v);
  }
  private _resp = signal<SearchResponseDTO | null>(null);

  expanded = signal(false);

  rows = computed<ComparisonRow[]>(() => {
    const resp = this._resp();
    if (!resp) return [];

    const allOffers: UnifiedOffer[] = [
      ...(resp.money_offers ?? []),
      ...(resp.miles_offers ?? []),
    ];
    if (!allOffers.length) return [];

    // Cash baseline per airline IATA = min cash for that carrier.
    // Skiplagged offers count as cash too — that's why a hidden-city LATAM
    // ticket shows up under LATAM here. We use the FIRST segment's carrier
    // as the operating airline for cash offers (Kayak/Skiplagged).
    const cashByKey: Record<string, number> = {};
    for (const o of allOffers) {
      if (o.price_brl == null || o.miles != null) continue;
      const carrierName =
        o.outbound?.segments?.[0]?.carrier || o.airline || '';
      const k = airlineKey(carrierName);
      if (!k) continue;
      if (cashByKey[k] == null || o.price_brl < cashByKey[k]) {
        cashByKey[k] = o.price_brl;
      }
    }

    // Group miles offers by source + normalized airline key.
    const econ: Record<string, UnifiedOffer> = {};
    const bm:   Record<string, UnifiedOffer> = {};

    for (const o of allOffers) {
      if (o.miles == null) continue;
      const k = airlineKey(o.airline);
      if (!k) continue;
      const target = o.source === 'economilhas'
        ? econ
        : (o.source.startsWith('buscamilhas') ? bm : null);
      if (!target) continue;
      const current = target[k];
      if (!current || (o.equivalent_brl ?? Infinity) < (current.equivalent_brl ?? Infinity)) {
        target[k] = o;
      }
    }

    // Only show airlines that have at least one miles offer (eco or bm).
    // A row with cash-only would be redundant (cash already appears in the main grid).
    const milesKeys = new Set<string>([
      ...Object.keys(econ),
      ...Object.keys(bm),
    ]);

    const rows: ComparisonRow[] = [];
    for (const key of milesKeys) {
      const econOffer = econ[key];
      const bmOffer = bm[key];
      const cashPrice = cashByKey[key] ?? null;

      const econCell: PriceCell | null = econOffer
        ? {
            miles: econOffer.miles,
            taxes: econOffer.taxes_brl,
            equivalentBRL: econOffer.equivalent_brl ?? 0,
          }
        : null;
      const bmCell: PriceCell | null = bmOffer
        ? {
            miles: bmOffer.miles,
            taxes: bmOffer.taxes_brl,
            equivalentBRL: bmOffer.equivalent_brl ?? 0,
          }
        : null;
      const cashCell: PriceCell | null = cashPrice != null
        ? { equivalentBRL: cashPrice, cashBRL: cashPrice }
        : null;

      const cells: Array<['econ' | 'bm' | 'cash', number]> = [];
      if (econCell) cells.push(['econ', econCell.equivalentBRL]);
      if (bmCell)   cells.push(['bm',   bmCell.equivalentBRL]);
      if (cashCell) cells.push(['cash', cashCell.equivalentBRL]);

      let bestSource: ComparisonRow['bestSource'] = null;
      if (cells.length) {
        cells.sort((a, b) => a[1] - b[1]);
        bestSource = cells[0][0];
      }

      rows.push({
        airline: key,
        airlineLabel: airlineName(key),
        econ: econCell,
        bm: bmCell,
        cash: cashCell,
        bestSource,
      });
    }

    // Sort rows: airlines in defined IATA order, then alphabetical.
    rows.sort((a, b) => {
      const ai = AIRLINE_ORDER.indexOf(a.airline);
      const bi = AIRLINE_ORDER.indexOf(b.airline);
      if (ai >= 0 && bi >= 0) return ai - bi;
      if (ai >= 0) return -1;
      if (bi >= 0) return 1;
      return a.airlineLabel.localeCompare(b.airlineLabel);
    });

    return rows;
  });

  formatBRL = formatBRL;

  toggle(): void {
    this.expanded.update((v) => !v);
  }
}
