import { CommonModule } from '@angular/common';
import { Component, Input, computed, inject, signal } from '@angular/core';

import { ApiService } from '../../core/api.service';
import { UnifiedOffer, ValidateFlightResponseDTO } from '../../models/flight';
import {
  airlineKey,
  airlineName,
  carrierBookingLabel,
  carrierBookingUrl,
  formatBRL,
  formatDuration,
  formatTime,
  isStale,
  programLabel,
  scenarioOf,
  SCENARIO_META,
  sourceLabel,
  stopsLabel,
  timeAgo,
} from '../helpers';

@Component({
  selector: 'app-flight-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './flight-card.html',
  styleUrl: './flight-card.scss',
})
export class FlightCardComponent {
  @Input({ required: true }) offer!: UnifiedOffer;
  /** When true, shows the "Validate on airline" deeplink. Set to true for
   * highlighted offers (best, hidden city, miles cheapest). Auto-enabled for
   * hidden_city regardless. */
  @Input() showValidation = false;

  private api = inject(ApiService);

  expanded = signal(false);
  validating = signal(false);
  validationResult = signal<ValidateFlightResponseDTO | null>(null);
  validationError = signal<string | null>(null);

  toggle(): void {
    this.expanded.update((v) => !v);
  }

  get scenario() {
    return scenarioOf(this.offer);
  }
  get scenarioMeta() {
    return SCENARIO_META[this.scenario];
  }
  get sourceLabel(): string {
    return sourceLabel(this.offer.source);
  }
  get totalDuration(): string {
    return formatDuration(this.offer.outbound.duration_min);
  }
  get stopsLabel(): string {
    return stopsLabel(this.offer.outbound.segments.length);
  }
  get firstSegment() {
    return this.offer.outbound.segments[0];
  }
  get lastSegment() {
    return this.offer.outbound.segments[this.offer.outbound.segments.length - 1];
  }
  get depTime(): string {
    return formatTime(this.firstSegment.departure_dt);
  }
  get arrTime(): string {
    return formatTime(this.lastSegment.arrival_dt);
  }
  get airlinesDisplay(): string {
    const codes = [...new Set(this.offer.outbound.segments.map((s) => s.carrier))];
    return codes.map((c) => airlineName(c)).join(' · ');
  }
  get priceBRL(): string {
    return formatBRL(this.offer.price_brl ?? this.offer.equivalent_brl);
  }
  get miles(): string {
    return formatBRL(this.offer.miles);
  }
  get taxes(): string {
    return formatBRL(this.offer.taxes_brl);
  }
  get isHidden(): boolean {
    return this.scenario === 'hidden_city';
  }
  get isMiles(): boolean {
    return this.offer.miles != null;
  }
  get hasMilesEstimate(): boolean {
    return !this.isMiles && (this.offer.miles_equivalent ?? 0) > 0;
  }
  get milesEstimateLabel(): string {
    const m = this.offer.miles_equivalent ?? 0;
    const prog = programLabel(this.offer.miles_equivalent_program);
    return `≈ ${formatBRL(m)} ${prog}`;
  }
  get freshnessLabel(): string {
    return timeAgo(this.offer.captured_at);
  }
  get isStale(): boolean {
    return isStale(this.offer.captured_at, 120);
  }
  get equivalentBRL(): string {
    return formatBRL(this.offer.equivalent_brl);
  }
  get hasEquivalentBRL(): boolean {
    return (this.offer.equivalent_brl ?? 0) > 0;
  }
  get milesProgramLabel(): string | null {
    return this.offer.miles_program ?? null;
  }

  /** Validation deeplink for the seller to verify on the airline's own site.
   *
   * For hidden_city, abrir BSB→TPA no site da GOL é inútil porque a GOL não
   * vende essa rota — ela só opera BSB→GRU. Aqui usamos o trecho contíguo
   * operado pela primeira cia para que o vendedor consiga validar o segmento
   * de fato. Para cash/miles padrão, usa a rota completa.
   */
  get validationUrl(): string | null {
    const segs = this.offer.outbound?.segments ?? [];
    if (!segs.length) return null;
    const operatingCarrier = segs[0].carrier;
    const origin = segs[0].origin;

    let destination: string;
    if (this.isHidden) {
      let lastSameCarrier = segs[0];
      for (const s of segs) {
        if (s.carrier !== operatingCarrier) break;
        lastSameCarrier = s;
      }
      destination = lastSameCarrier.destination;
    } else {
      destination = segs[segs.length - 1].destination;
    }

    const dateISO = (segs[0].departure_dt ?? '').slice(0, 10);
    if (!operatingCarrier || !origin || !destination || !dateISO) return null;
    return carrierBookingUrl(operatingCarrier, origin, destination, dateISO);
  }

  get validationLabel(): string {
    const segs = this.offer.outbound?.segments ?? [];
    const baseLabel = carrierBookingLabel(segs[0]?.carrier ?? '');
    if (!this.isHidden) return baseLabel;
    // Hidden city: rotulo aponta que estamos validando só a perna da cia
    const seg = segs[0];
    return `${baseLabel} (${seg?.origin}→${this.hiddenLegDestination()})`;
  }

  /** Destino da perna operada pela primeira cia — usado pra deixar claro
   * no botão de validação que vamos checar só esse trecho. */
  private hiddenLegDestination(): string {
    const segs = this.offer.outbound?.segments ?? [];
    if (!segs.length) return '';
    const carrier = segs[0].carrier;
    let last = segs[0];
    for (const s of segs) {
      if (s.carrier !== carrier) break;
      last = s;
    }
    return last.destination;
  }

  /** Link Google Flights da rota OFICIAL — útil quando o deeplink do
   * provedor não funciona ou quando o vendedor quer um benchmark
   * neutro de tarifa. */
  get googleFlightsUrl(): string {
    const segs = this.offer.outbound?.segments ?? [];
    if (!segs.length) return '';
    const origin = segs[0].origin;
    const destination = segs[segs.length - 1].destination;
    const dateISO = (segs[0].departure_dt ?? '').slice(0, 10);
    if (!origin || !destination || !dateISO) return '';
    const q = encodeURIComponent(`Flights from ${origin} to ${destination} on ${dateISO}`);
    return `https://www.google.com/travel/flights?q=${q}`;
  }

  get shouldShowValidation(): boolean {
    // Hidden city ALWAYS gets a verify button (Skiplagged ≠ ticket inventory).
    if (this.isHidden) return true;
    return this.showValidation;
  }

  /** True quando é uma oferta BuscaMilhas em parceiro (não G3/LA/AD).
   * /validate-flight só cobre programa BR próprio — não temos como confirmar
   * via BuscaMilhas se um voo KLM/AF/LX existe. Esconder o "Validar voo"
   * evita resultados confusos; deixa só "Validar na CIA" + Google Flights. */
  get isPartnerAward(): boolean {
    const src = (this.offer.source || '').toLowerCase();
    if (!src.startsWith('buscamilhas')) return false;
    const segs = this.offer.outbound?.segments ?? [];
    const code = segs[0]?.carrier?.toUpperCase() ?? '';
    return code !== '' && !['G3', 'LA', 'AD'].includes(code);
  }

  /** Validar voo (BuscaMilhas-backed) só faz sentido para G3/LA/AD. */
  get canRunBuscamilhasValidation(): boolean {
    return this.shouldShowValidation && !this.isPartnerAward;
  }

  /** Hits /validate-flight using BuscaMilhas to check this exact flight is real. */
  runValidation(): void {
    const segs = this.offer.outbound?.segments ?? [];
    if (!segs.length) return;
    const seg = segs[0];
    if (!seg.carrier || !seg.origin || !seg.departure_dt) return;

    // Backend exige IATA 2 chars; BuscaMilhas devolve nome ("LATAM AIRLINES (TAM)").
    // Normalizar via airlineKey antes de evitar 422 Pydantic.
    const carrierIata = airlineKey(seg.carrier);
    if (!carrierIata || carrierIata.length < 2) {
      this.validationError.set(`Companhia "${seg.carrier}" não reconhecida — não temos como validar.`);
      return;
    }

    // For hidden city, query only the carrier's contiguous leg (same logic as validationUrl).
    let destination: string;
    if (this.isHidden) {
      let lastSameCarrier = segs[0];
      for (const s of segs) {
        if (s.carrier !== seg.carrier) break;
        lastSameCarrier = s;
      }
      destination = lastSameCarrier.destination;
    } else {
      destination = segs[segs.length - 1].destination;
    }

    this.validating.set(true);
    this.validationError.set(null);
    this.validationResult.set(null);

    this.api
      .validateFlight({
        carrier: carrierIata,
        origin: seg.origin,
        destination,
        departure_dt: seg.departure_dt,
        quoted_price_brl: this.offer.price_brl ?? null,
        quoted_miles: this.offer.miles ?? null,
      })
      .subscribe({
        next: (resp) => {
          this.validationResult.set(resp);
          this.validating.set(false);
        },
        error: (err) => {
          this.validationError.set(this.extractError(err));
          this.validating.set(false);
        },
      });
  }

  /** Extrai mensagem legível de qualquer erro HTTP — inclui o caso do Pydantic
   * que devolve `detail` como array de objetos. */
  private extractError(err: unknown): string {
    const e = err as { error?: unknown; message?: string };
    const detail = (e?.error as { detail?: unknown })?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => {
          if (typeof d === 'string') return d;
          const loc = Array.isArray(d?.loc) ? d.loc.slice(1).join('.') : '';
          const msg = d?.msg ?? 'inválido';
          return loc ? `${loc}: ${msg}` : msg;
        })
        .join(' · ');
    }
    if (e?.message) return e.message;
    return 'Falha ao validar.';
  }

  get validationBadge(): { label: string; kind: 'ok' | 'warn' | 'err' } | null {
    const r = this.validationResult();
    if (!r) return null;
    if (r.status === 'found_with_match')   return { label: '✓ Voo confirmado',     kind: 'ok' };
    if (r.status === 'found_no_match')     return { label: '⚠ Sem match exato',     kind: 'warn' };
    if (r.status === 'no_offers')          return { label: '⚠ Sem oferta milhas',   kind: 'warn' };
    if (r.status === 'unsupported_carrier')return { label: 'ⓘ Sem como validar',    kind: 'warn' };
    return { label: '⚠ Erro na validação', kind: 'err' };
  }

  formatBRL = formatBRL;

  layoverDuration(idx: number): string {
    const segs = this.offer.outbound.segments;
    if (idx >= segs.length - 1) return '';
    const arr = new Date(segs[idx].arrival_dt).getTime();
    const dep = new Date(segs[idx + 1].departure_dt).getTime();
    const mins = Math.round((dep - arr) / 60000);
    return formatDuration(mins);
  }

  formatTime = formatTime;
  airlineName = airlineName;
}
