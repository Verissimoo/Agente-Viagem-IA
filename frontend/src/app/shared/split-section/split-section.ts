import { CommonModule } from '@angular/common';
import {
  Component,
  Input,
  OnChanges,
  SimpleChanges,
  computed,
  inject,
  signal,
} from '@angular/core';

import { ApiService } from '../../core/api.service';
import {
  FitOfferDTO,
  KayakLegDTO,
  MilesMatchResponseDTO,
  SplitFitResponseDTO,
  SplitMilesOfferDTO,
  SplitMilesSearchResponseDTO,
  SplitResponseDTO,
  ValidateFlightResponseDTO,
} from '../../models/flight';
import {
  airlineKey,
  airlineName,
  carrierBookingLabel,
  carrierBookingUrl,
  formatBRL,
  formatDuration,
  formatTime,
  programLabel,
} from '../helpers';

interface MilesPanelState {
  loading: boolean;
  error: string | null;
  result: MilesMatchResponseDTO | null;
}

interface ValidationState {
  loading: boolean;
  error: string | null;
  result: ValidateFlightResponseDTO | null;
}

// ── Fase 2: estado do encaixe doméstico por voo internacional ──
interface FitPanelState {
  loading: boolean;
  error: string | null;
  result: SplitFitResponseDTO | null;
  showIncompatible: boolean;
}

// ── Fase 3: combinação intl + doméstica selecionada + cotações em milhas ──
interface CombinationMilesState {
  loading: boolean;
  error: string | null;
  domesticResult: MilesMatchResponseDTO | null;
  intlResult: MilesMatchResponseDTO | null;
}

@Component({
  selector: 'app-split-section',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './split-section.html',
  styleUrl: './split-section.scss',
})
export class SplitSectionComponent implements OnChanges {
  @Input({ required: true }) origin!: string;
  @Input({ required: true }) destination!: string;
  @Input({ required: true }) date!: string;
  @Input() adults = 1;
  @Input() hub = 'GRU';
  /** Quando true, dispara automaticamente o load assim que o componente
   * recebe rotas válidas. Usado pelo smart-explore (vendedor já clicou
   * em "Quebrar trecho nesta data" — não faz sentido pedir outro clique). */
  @Input() autoLoad = false;

  /** Hubs principais do Brasil em chips — atalho pro vendedor. Dropdown
   * adicional permite escolher qualquer aeroporto. */
  readonly HUB_CHIPS = [
    { code: 'GRU', name: 'GRU · São Paulo', recommended: true },
    { code: 'GIG', name: 'GIG · Rio de Janeiro' },
    { code: 'BSB', name: 'BSB · Brasília' },
    { code: 'CNF', name: 'CNF · Belo Horizonte' },
  ];
  readonly HUB_FULL = [
    ...this.HUB_CHIPS,
    { code: 'REC', name: 'REC · Recife' },
    { code: 'SSA', name: 'SSA · Salvador' },
    { code: 'FOR', name: 'FOR · Fortaleza' },
    { code: 'POA', name: 'POA · Porto Alegre' },
    { code: 'CWB', name: 'CWB · Curitiba' },
  ];

  setHub(code: string): void {
    if (this.hub === code) return;
    this.hub = code;
    // Invalida fits abertos (resultado anterior estava com outro hub).
    this.fitPanels.set({});
    this.selectedCombinations.set({});
    this.combinationMiles.set({});
    // Recarrega a análise com hub novo se já há resultado.
    if (this.result()) {
      this.load();
    }
  }

  setBaggage(v: boolean): void {
    if (this.withBaggage() === v) return;
    this.withBaggage.set(v);
    // Janela mudou — invalida fits e cotações de combinação.
    this.fitPanels.set({});
  }

  private api = inject(ApiService);

  loading = signal(false);
  error = signal<string | null>(null);
  result = signal<SplitResponseDTO | null>(null);

  // ── Fase 3: busca direta de milhas hub → destino (fluxo primário) ──
  milesSearchLoading = signal(false);
  milesSearchError = signal<string | null>(null);
  milesSearchResult = signal<SplitMilesSearchResponseDTO | null>(null);

  // Miles-match: keyed by leg id; each leg can have its own quote panel state.
  private milesPanels = signal<Record<string, MilesPanelState>>({});
  // Validation panel: keyed by leg id (BuscaMilhas-backed availability check).
  private validations = signal<Record<string, ValidationState>>({});

  // Fase 2 — encaixe doméstico por id de voo internacional.
  fitPanels = signal<Record<string, FitPanelState>>({});
  // Fase 2 — combinação selecionada: intl_leg_id → domestic FitOfferDTO.
  selectedCombinations = signal<Record<string, FitOfferDTO>>({});
  // Bagagem pra recalcular janelas via /split/fit (cache invalida ao mudar).
  withBaggage = signal(false);

  // Fase 3 — cotação em milhas da combinação selecionada (intl_leg_id → estado).
  combinationMiles = signal<Record<string, CombinationMilesState>>({});

  // Fase 3 — encaixe doméstico ancorado em uma OFERTA DE MILHAS (não em voo
  // Kayak). Keyed pelo id da oferta de milhas; reusa o mesmo FitPanelState.
  milesOfferFitPanels = signal<Record<string, FitPanelState>>({});
  // Doméstico selecionado para cada oferta de milhas (miles_offer_id → fit).
  milesOfferSelectedDomestic = signal<Record<string, FitOfferDTO>>({});

  ngOnChanges(changes: SimpleChanges): void {
    const routeChanged = changes['origin'] || changes['destination'] || changes['date'];
    const hubChanged = changes['hub'] && !changes['hub'].firstChange;

    if (routeChanged || hubChanged) {
      this.result.set(null);
      this.error.set(null);
      this.milesPanels.set({});
      this.validations.set({});
      this.milesSearchResult.set(null);
      this.milesSearchError.set(null);
      this.milesOfferFitPanels.set({});
    }

    // Auto-fetch: vendedor já confirmou intenção no smart-explore.
    // Também refaz fetch automaticamente se o hub mudar e já tínhamos resultado.
    if (this.autoLoad && this.origin && this.destination && this.date) {
      if (routeChanged || hubChanged) {
        this.load();
      }
    }
  }

  load(): void {
    if (!this.origin || !this.destination || !this.date) return;
    this.loading.set(true);
    this.error.set(null);
    this.api
      .split({
        origin: this.origin,
        destination: this.destination,
        date: this.date,
        adults: this.adults,
        hub: this.hub,
      })
      .subscribe({
        next: (r) => {
          this.result.set(r);
          this.loading.set(false);
        },
        error: (e) => {
          this.error.set(this.extractError(e));
          this.loading.set(false);
        },
      });

    // Fase 3 — em paralelo, busca milhas reais hub → destino (fluxo principal).
    // Não bloqueia o /split; usuário vê milhas chegando assim que estão prontas.
    this.loadMilesSearch();
  }

  /** Dispara /split/miles-search usando hub atual como origem. Sem esse
   * resultado o vendedor ficaria preso ao fluxo "achar voo Kayak específico
   * em milhas" — esta busca já devolve disponibilidade real. */
  loadMilesSearch(): void {
    if (!this.hub || !this.destination || !this.date) return;
    this.milesSearchLoading.set(true);
    this.milesSearchError.set(null);
    this.milesSearchResult.set(null);
    this.api
      .splitMilesSearch({
        origin: this.hub,
        destination: this.destination,
        date: this.date,
        adults: this.adults,
      })
      .subscribe({
        next: (r) => {
          this.milesSearchResult.set(r);
          this.milesSearchLoading.set(false);
        },
        error: (e) => {
          this.milesSearchError.set(this.extractError(e));
          this.milesSearchLoading.set(false);
        },
      });
  }

  bestCombinedPrice(r: SplitResponseDTO): number | null {
    const a = r.leg_to_hub[0]?.price_brl ?? 0;
    const b = r.leg_from_hub[0]?.price_brl ?? 0;
    if (!a || !b) return null;
    return a + b;
  }

  savings(r: SplitResponseDTO): number | null {
    if (!r.direct) return null;
    const combined = this.bestCombinedPrice(r);
    if (combined === null) return null;
    return r.direct.price_brl - combined;
  }

  panelFor(legId: string): MilesPanelState | null {
    return this.milesPanels()[legId] ?? null;
  }

  isPanelOpen(legId: string): boolean {
    return legId in this.milesPanels();
  }

  togglePanel(leg: KayakLegDTO, legType: 'domestic' | 'international'): void {
    const id = leg.id;
    const existing = this.milesPanels()[id];
    if (existing) {
      const { [id]: _, ...rest } = this.milesPanels();
      this.milesPanels.set(rest);
      return;
    }
    // Otherwise: open + fetch
    this.fetchMilesFor(leg, legType);
  }

  private fetchMilesFor(leg: KayakLegDTO, legType: 'domestic' | 'international'): void {
    const r = this.result();
    if (!r) return;

    // Pair leg's departure datetime is the OTHER leg in the split combo.
    const otherLeg = legType === 'domestic' ? r.leg_from_hub[0] : r.leg_to_hub[0];
    if (!otherLeg?.departure_dt) {
      this.milesPanels.set({
        ...this.milesPanels(),
        [leg.id]: {
          loading: false,
          error: 'Sem perna pareada disponível para calcular janela de conexão',
          result: null,
        },
      });
      return;
    }

    // Direction: if I'm the domestic leg, am I before or after the intl?
    // route_type tells us:
    //   br_to_intl  → leg_to_hub is domestic, departs BEFORE intl (from_hub)
    //   intl_to_br  → leg_to_hub is intl,     leg_from_hub is domestic AFTER
    const direction: 'before_intl' | 'after_intl' =
      r.route_type === 'br_to_intl'
        ? legType === 'domestic'
          ? 'before_intl'
          : 'after_intl'
        : legType === 'domestic'
          ? 'after_intl'
          : 'before_intl';

    this.milesPanels.set({
      ...this.milesPanels(),
      [leg.id]: { loading: true, error: null, result: null },
    });

    this.api
      .milesMatch({
        leg: {
          airline: leg.airline,
          airlines: leg.airlines,
          airlines_iata: leg.airlines_iata,
          origin: leg.origin,
          destination: leg.destination,
          departure_dt: leg.departure_dt ?? null,
          arrival_dt: leg.arrival_dt ?? null,
          duration_min: leg.duration_min,
          stops: leg.stops,
          price_brl: leg.price_brl,
        },
        leg_type: legType,
        other_leg_dt: otherLeg.departure_dt,
        other_leg_direction: direction,
        with_baggage: false,
        adults: this.adults,
      })
      .subscribe({
        next: (res) => {
          this.milesPanels.set({
            ...this.milesPanels(),
            [leg.id]: { loading: false, error: null, result: res },
          });
        },
        error: (e) => {
          const detail = (e?.error as { detail?: string })?.detail;
          this.milesPanels.set({
            ...this.milesPanels(),
            [leg.id]: {
              loading: false,
              error: detail ?? e?.message ?? 'Falha ao cotar milhas',
              result: null,
            },
          });
        },
      });
  }

  /** Auxiliary deeplink — opens the carrier's own booking page. */
  validationFor(leg: KayakLegDTO): string | null {
    if (!leg.departure_dt) return null;
    const dateISO = leg.departure_dt.slice(0, 10);
    return carrierBookingUrl(leg.airline, leg.origin, leg.destination, dateISO);
  }

  /** Link do Google Flights pra ROTA COMPLETA (origem real → destino real),
   * útil pro vendedor comparar com o preço direto. Não usa a perna isolada. */
  fullRouteGoogleFlights(): string {
    const q = encodeURIComponent(
      `Flights from ${this.origin} to ${this.destination} on ${this.date}`,
    );
    return `https://www.google.com/travel/flights?q=${q}`;
  }

  validationLabelFor(leg: KayakLegDTO): string {
    return carrierBookingLabel(leg.airline);
  }

  /** Calls /validate-flight to confirm this leg via BuscaMilhas. */
  runValidation(leg: KayakLegDTO): void {
    if (!leg.departure_dt) return;
    const id = leg.id;
    // Normalize to IATA — leg.airlines_iata pode ter codes corretos, mas se
    // vazio caímos no leg.airline que é nome textual.
    const rawCarrier = leg.airlines_iata?.[0] || leg.airline || '';
    const carrier = airlineKey(rawCarrier);

    if (!carrier || carrier.length < 2) {
      this.validations.set({
        ...this.validations(),
        [id]: {
          loading: false,
          error: `Companhia "${rawCarrier}" não reconhecida — sem validação.`,
          result: null,
        },
      });
      return;
    }

    this.validations.set({
      ...this.validations(),
      [id]: { loading: true, error: null, result: null },
    });

    this.api
      .validateFlight({
        carrier,
        origin: leg.origin,
        destination: leg.destination,
        departure_dt: leg.departure_dt,
        quoted_price_brl: leg.price_brl,
      })
      .subscribe({
        next: (resp) => {
          this.validations.set({
            ...this.validations(),
            [id]: { loading: false, error: null, result: resp },
          });
        },
        error: (err) => {
          this.validations.set({
            ...this.validations(),
            [id]: {
              loading: false,
              error: this.extractError(err),
              result: null,
            },
          });
        },
      });
  }

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

  validationStateFor(legId: string): ValidationState | null {
    return this.validations()[legId] ?? null;
  }

  validationBadgeFor(legId: string): { label: string; kind: 'ok' | 'warn' | 'err' } | null {
    const r = this.validations()[legId]?.result;
    if (!r) return null;
    if (r.status === 'found_with_match')    return { label: '✓ Voo confirmado',    kind: 'ok'   };
    if (r.status === 'found_no_match')      return { label: '⚠ Sem match exato',    kind: 'warn' };
    if (r.status === 'no_offers')           return { label: '⚠ Sem oferta milhas',  kind: 'warn' };
    if (r.status === 'unsupported_carrier') return { label: 'ⓘ Sem como validar',   kind: 'warn' };
    return { label: '⚠ Erro na validação',  kind: 'err'  };
  }

  // ─────────────────────────────────────────────────────────────────
  // FASE 2 — Encaixe doméstico para um voo internacional escolhido
  // ─────────────────────────────────────────────────────────────────

  /** Identifica qual coluna é a perna INTERNACIONAL na resposta /split.
   * br_to_intl → leg_from_hub (GRU→destino é o intl)
   * intl_to_br → leg_to_hub   (origem→GRU é o intl)
   * br_domestic → não há perna internacional; encaixe não se aplica. */
  intlColumn(r: SplitResponseDTO): 'leg_to_hub' | 'leg_from_hub' | null {
    if (r.route_type === 'br_to_intl') return 'leg_from_hub';
    if (r.route_type === 'intl_to_br') return 'leg_to_hub';
    return null;
  }

  /** intl_direction usado no /split/fit: from_gru se o intl sai de GRU,
   * to_gru se chega em GRU. */
  intlDirection(r: SplitResponseDTO): 'from_gru' | 'to_gru' {
    return r.route_type === 'br_to_intl' ? 'from_gru' : 'to_gru';
  }

  /** Aeroporto da OUTRA ponta (origem em br_to_intl, destino em intl_to_br). */
  otherEndpoint(r: SplitResponseDTO): string {
    return r.route_type === 'br_to_intl' ? r.origin : r.destination;
  }

  fitStateFor(legId: string): FitPanelState | null {
    return this.fitPanels()[legId] ?? null;
  }

  /** Abre/fecha o painel de encaixe doméstico para um voo internacional.
   * Dispara /split/fit na primeira abertura; reusa o cache no toggle. */
  toggleFit(intlLeg: KayakLegDTO, r: SplitResponseDTO): void {
    const id = intlLeg.id;
    const existing = this.fitPanels()[id];
    if (existing && existing.result) {
      // Já carregado — só fecha (remove do mapa)
      const { [id]: _, ...rest } = this.fitPanels();
      this.fitPanels.set(rest);
      return;
    }

    this.fitPanels.set({
      ...this.fitPanels(),
      [id]: { loading: true, error: null, result: null, showIncompatible: false },
    });

    const dir = this.intlDirection(r);
    const other = this.otherEndpoint(r);

    this.api
      .splitFit({
        intl_offer: {
          airline: intlLeg.airline,
          airlines: intlLeg.airlines,
          airlines_iata: intlLeg.airlines_iata,
          origin: intlLeg.origin,
          destination: intlLeg.destination,
          departure_dt: intlLeg.departure_dt,
          arrival_dt: intlLeg.arrival_dt,
          duration_min: intlLeg.duration_min,
          stops: intlLeg.stops,
          price_brl: intlLeg.price_brl,
        },
        other_endpoint: other,
        intl_direction: dir,
        adults: this.adults,
        with_baggage: this.withBaggage(),
      })
      .subscribe({
        next: (resp) => {
          this.fitPanels.set({
            ...this.fitPanels(),
            [id]: { loading: false, error: null, result: resp, showIncompatible: false },
          });
        },
        error: (err) => {
          this.fitPanels.set({
            ...this.fitPanels(),
            [id]: {
              loading: false,
              error: this.extractError(err),
              result: null,
              showIncompatible: false,
            },
          });
        },
      });
  }

  toggleIncompatible(intlLegId: string): void {
    const cur = this.fitPanels()[intlLegId];
    if (!cur) return;
    this.fitPanels.set({
      ...this.fitPanels(),
      [intlLegId]: { ...cur, showIncompatible: !cur.showIncompatible },
    });
  }

  /** Vendedor escolheu um voo doméstico para encaixar com este intl.
   * Cria/atualiza a combinação e invalida cotação em milhas anterior. */
  selectDomesticFit(intlLegId: string, dom: FitOfferDTO): void {
    this.selectedCombinations.set({
      ...this.selectedCombinations(),
      [intlLegId]: dom,
    });
    // Combinação mudou → invalida cotação em milhas anterior dessa combinação
    const { [intlLegId]: _, ...restMiles } = this.combinationMiles();
    this.combinationMiles.set(restMiles);
  }

  isDomesticSelected(intlLegId: string, domId: string): boolean {
    return this.selectedCombinations()[intlLegId]?.id === domId;
  }

  selectedDomesticFor(intlLegId: string): FitOfferDTO | null {
    return this.selectedCombinations()[intlLegId] ?? null;
  }

  /** Toggle de bagagem — invalida fits abertos (janela muda) e
   * refaz quando o vendedor reabrir. */
  toggleBaggage(): void {
    this.withBaggage.update((v) => !v);
    // Invalida todos os fits porque a janela mudou (min_connection diferente)
    this.fitPanels.set({});
  }

  /** Lista de combinações no formato pra renderização — uma entrada por intl
   * leg id que tem um doméstico selecionado. Cada uma carrega a perna intl,
   * o domestic escolhido e o estado atual de cotação em milhas. */
  selectedCombinationsList(r: SplitResponseDTO): Array<{
    intlLeg: KayakLegDTO;
    domesticLeg: FitOfferDTO;
    milesState: CombinationMilesState | null;
  }> {
    const col = this.intlColumn(r);
    if (!col) return [];
    const intlList = r[col] || [];
    const sel = this.selectedCombinations();
    const milesMap = this.combinationMiles();
    const out: Array<{
      intlLeg: KayakLegDTO;
      domesticLeg: FitOfferDTO;
      milesState: CombinationMilesState | null;
    }> = [];
    for (const intlLeg of intlList) {
      const dom = sel[intlLeg.id];
      if (!dom) continue;
      out.push({
        intlLeg,
        domesticLeg: dom,
        milesState: milesMap[intlLeg.id] ?? null,
      });
    }
    return out;
  }

  /** Soma cash de uma combinação (intl + dom selecionado). */
  combinationCashTotal(intl: KayakLegDTO, dom: FitOfferDTO): number {
    return (intl.price_brl || 0) + (dom.price_brl || 0);
  }

  combinationSavingsVsDirect(intl: KayakLegDTO, dom: FitOfferDTO, r: SplitResponseDTO): number | null {
    if (!r.direct?.price_brl) return null;
    return r.direct.price_brl - this.combinationCashTotal(intl, dom);
  }

  // ─────────────────────────────────────────────────────────────────
  // FASE 3 — Cotar combinação em milhas (doméstica + internacional)
  // ─────────────────────────────────────────────────────────────────

  /** Para uma combinação selecionada, dispara /miles-match em paralelo para
   * a perna doméstica e a internacional, usando a direção correta de cada. */
  runCombinationMiles(intlLeg: KayakLegDTO, domesticLeg: FitOfferDTO, r: SplitResponseDTO): void {
    const id = intlLeg.id;
    this.combinationMiles.set({
      ...this.combinationMiles(),
      [id]: { loading: true, error: null, domesticResult: null, intlResult: null },
    });

    const dir = this.intlDirection(r);
    // br_to_intl: doméstica é ANTES do intl (before_intl); intl é DEPOIS da doméstica (after_intl invertido)
    // intl_to_br: intl chega ANTES da doméstica sair (before_intl semanticamente para a doméstica = "depois do intl")
    const domDir = dir === 'from_gru' ? 'before_intl' : 'after_intl';
    const intlDir = dir === 'from_gru' ? 'after_intl' : 'before_intl';

    const domDt = domesticLeg.departure_dt || '';
    const intlDt = intlLeg.departure_dt || '';

    const legPayload = (l: KayakLegDTO | FitOfferDTO) => ({
      airline: l.airline,
      airlines: l.airlines,
      airlines_iata: l.airlines_iata,
      origin: l.origin,
      destination: l.destination,
      departure_dt: l.departure_dt ?? null,
      arrival_dt: l.arrival_dt ?? null,
      duration_min: l.duration_min,
      stops: l.stops,
      price_brl: l.price_brl,
    });

    // Disparar as 2 cotações em paralelo
    const updateState = (patch: Partial<CombinationMilesState>) => {
      const cur = this.combinationMiles()[id] || {
        loading: true, error: null, domesticResult: null, intlResult: null,
      };
      this.combinationMiles.set({
        ...this.combinationMiles(),
        [id]: { ...cur, ...patch },
      });
    };

    let pending = 2;
    const settle = () => {
      pending -= 1;
      if (pending <= 0) updateState({ loading: false });
    };

    this.api
      .milesMatch({
        leg: legPayload(domesticLeg),
        leg_type: 'domestic',
        other_leg_dt: intlDt,
        other_leg_direction: domDir,
        with_baggage: this.withBaggage(),
        adults: this.adults,
      })
      .subscribe({
        next: (resp) => { updateState({ domesticResult: resp }); settle(); },
        error: (err) => { updateState({ error: this.extractError(err) }); settle(); },
      });

    this.api
      .milesMatch({
        leg: legPayload(intlLeg),
        leg_type: 'international',
        other_leg_dt: domDt,
        other_leg_direction: intlDir,
        with_baggage: this.withBaggage(),
        adults: this.adults,
      })
      .subscribe({
        next: (resp) => { updateState({ intlResult: resp }); settle(); },
        error: (err) => { updateState({ error: this.extractError(err) }); settle(); },
      });
  }

  /** Custo total da combinação ótima em milhas (menor opção doméstica +
   * menor opção internacional, validadas e na janela). Retorna null se
   * não houver opção válida em algum leg. */
  combinationMilesTotal(milesState: CombinationMilesState | null): number | null {
    if (!milesState?.domesticResult || !milesState?.intlResult) return null;
    const domBest = (milesState.domesticResult.options || [])
      .filter((o) => o.is_in_window || o.is_exact_match)
      .sort((a, b) => a.total_real_cost_brl - b.total_real_cost_brl)[0];
    const intlBest = (milesState.intlResult.options || [])
      .filter((o) => o.is_in_window || o.is_exact_match)
      .sort((a, b) => a.total_real_cost_brl - b.total_real_cost_brl)[0];
    if (!domBest || !intlBest) return null;
    return domBest.total_real_cost_brl + intlBest.total_real_cost_brl;
  }

  // Helpers de UI para os badges de layover (verde/amarelo/cinza/vermelho)
  layoverBadgeClass(status: string): string {
    switch (status) {
      case 'ok':      return 'badge-ok';
      case 'tight':   return 'badge-tight';
      case 'long':    return 'badge-long';
      case 'invalid': return 'badge-invalid';
      default:        return '';
    }
  }

  // ─────────────────────────────────────────────────────────────────
  // FASE 3 — Fluxo primário: oferta de milhas + encaixe doméstico
  // ─────────────────────────────────────────────────────────────────

  /** Direção do encaixe pra uma oferta de milhas: se a rota é br_to_intl
   * (Brasil → exterior) o intl SAI do hub (from_gru); se é intl_to_br o
   * intl CHEGA no hub (to_gru). Quando não há /split carregado ainda,
   * usa heurística: hub doméstico BR → destino exterior = from_gru. */
  milesOfferIntlDirection(): 'from_gru' | 'to_gru' {
    const r = this.result();
    if (r) return this.intlDirection(r);
    return 'from_gru';
  }

  /** Aeroporto da OUTRA ponta usado pelo /split/fit. Se /split já carregou
   * tira do route_type; senão usa origin (no fluxo primário a viagem
   * começa no origin do usuário e passa pelo hub). */
  milesOfferOtherEndpoint(): string {
    const r = this.result();
    if (r) return this.otherEndpoint(r);
    return this.origin;
  }

  milesOfferFitState(offerId: string): FitPanelState | null {
    return this.milesOfferFitPanels()[offerId] ?? null;
  }

  /** Abre/fecha encaixe doméstico ancorado numa oferta de milhas.
   * Converte a oferta em IntlLegInput e chama /split/fit. */
  toggleFitForMilesOffer(offer: SplitMilesOfferDTO): void {
    const id = offer.id;
    const existing = this.milesOfferFitPanels()[id];
    if (existing && existing.result) {
      const { [id]: _, ...rest } = this.milesOfferFitPanels();
      this.milesOfferFitPanels.set(rest);
      return;
    }

    this.milesOfferFitPanels.set({
      ...this.milesOfferFitPanels(),
      [id]: { loading: true, error: null, result: null, showIncompatible: false },
    });

    this.api
      .splitFit({
        intl_offer: {
          airline: offer.carrier_name || offer.carrier,
          airlines: offer.carrier_name ? [offer.carrier_name] : [],
          airlines_iata: offer.carrier ? [offer.carrier] : [],
          origin: offer.origin,
          destination: offer.destination,
          departure_dt: offer.departure_dt,
          arrival_dt: offer.arrival_dt,
          duration_min: offer.duration_min,
          stops: offer.stops,
          price_brl: offer.equivalent_brl,
        },
        other_endpoint: this.milesOfferOtherEndpoint(),
        intl_direction: this.milesOfferIntlDirection(),
        adults: this.adults,
        with_baggage: this.withBaggage(),
      })
      .subscribe({
        next: (resp) => {
          this.milesOfferFitPanels.set({
            ...this.milesOfferFitPanels(),
            [id]: { loading: false, error: null, result: resp, showIncompatible: false },
          });
        },
        error: (err) => {
          this.milesOfferFitPanels.set({
            ...this.milesOfferFitPanels(),
            [id]: {
              loading: false,
              error: this.extractError(err),
              result: null,
              showIncompatible: false,
            },
          });
        },
      });
  }

  toggleMilesOfferIncompatible(offerId: string): void {
    const cur = this.milesOfferFitPanels()[offerId];
    if (!cur) return;
    this.milesOfferFitPanels.set({
      ...this.milesOfferFitPanels(),
      [offerId]: { ...cur, showIncompatible: !cur.showIncompatible },
    });
  }

  selectDomesticForMilesOffer(offerId: string, dom: FitOfferDTO): void {
    this.milesOfferSelectedDomestic.set({
      ...this.milesOfferSelectedDomestic(),
      [offerId]: dom,
    });
  }

  isDomesticSelectedForMilesOffer(offerId: string, domId: string): boolean {
    return this.milesOfferSelectedDomestic()[offerId]?.id === domId;
  }

  selectedDomesticForMilesOffer(offerId: string): FitOfferDTO | null {
    return this.milesOfferSelectedDomestic()[offerId] ?? null;
  }

  /** Total final da combinação: custo em milhas (já convertido em BRL pelo
   * pipeline) + cash da perna doméstica. */
  milesOfferCombinationTotal(offer: SplitMilesOfferDTO, dom: FitOfferDTO): number {
    return (offer.equivalent_brl || 0) + (dom.price_brl || 0);
  }

  formatLayover(minutes: number): string {
    if (!minutes || minutes <= 0) return '—';
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    if (h === 0) return `${m}min`;
    if (m === 0) return `${h}h`;
    return `${h}h${m.toString().padStart(2, '0')}min`;
  }

  airlineName = airlineName;
  formatBRL = formatBRL;
  formatDuration = formatDuration;
  formatTime = formatTime;
  programLabel = programLabel;
}
