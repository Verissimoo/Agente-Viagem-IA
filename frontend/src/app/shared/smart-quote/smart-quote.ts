import { CommonModule } from '@angular/common';
import {
  Component,
  Input,
  OnChanges,
  SimpleChanges,
  inject,
  signal,
} from '@angular/core';

import { ApiService } from '../../core/api.service';
import { SmartQuoteResponseDTO } from '../../models/flight';
import { airlineName, formatBRL } from '../helpers';

@Component({
  selector: 'app-smart-quote',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './smart-quote.html',
  styleUrl: './smart-quote.scss',
})
export class SmartQuoteComponent implements OnChanges {
  @Input({ required: true }) origin!: string;
  @Input({ required: true }) destination!: string;
  @Input({ required: true }) date!: string;
  @Input() estimatedPriceBrl: number | null = null;
  @Input() carriersSeen: string[] = [];

  private api = inject(ApiService);

  loading = signal(false);
  error = signal<string | null>(null);
  result = signal<SmartQuoteResponseDTO | null>(null);

  ngOnChanges(changes: SimpleChanges): void {
    // Refetch when route changes; price-only changes don't need a re-fetch.
    if (changes['origin'] || changes['destination'] || changes['date']) {
      this.fetch();
    } else if (changes['estimatedPriceBrl'] && this.result()) {
      // Just recalc miles_equivalent client-side
      this.recomputeMilesEquivalent();
    }
  }

  private fetch(): void {
    if (!this.origin || !this.destination || !this.date) return;
    this.loading.set(true);
    this.error.set(null);
    this.api
      .smartQuote({
        origin: this.origin,
        destination: this.destination,
        date: this.date,
        carriers_seen: this.carriersSeen,
        estimated_price_brl: this.estimatedPriceBrl ?? null,
      })
      .subscribe({
        next: (r) => {
          this.result.set(r);
          this.loading.set(false);
        },
        error: (e) => {
          this.error.set(e?.message ?? 'Falha ao recomendar programas');
          this.loading.set(false);
        },
      });
  }

  private recomputeMilesEquivalent(): void {
    const r = this.result();
    if (!r || !this.estimatedPriceBrl) return;
    const updated = {
      ...r,
      programs: r.programs.map((p) => ({
        ...p,
        miles_equivalent:
          p.cost_per_mile_brl > 0
            ? Math.round(this.estimatedPriceBrl! / p.cost_per_mile_brl)
            : null,
      })),
    };
    this.result.set(updated);
  }

  formatBRL = formatBRL;
  airlineName = airlineName;
}
