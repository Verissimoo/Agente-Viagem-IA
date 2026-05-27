import { CommonModule } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/api.service';
import { SettingsService } from '../../core/settings.service';
import { ThemeService } from '../../core/theme.service';
import {
  ExploreResponseDTO,
  ParseIntentResponseDTO,
  QuoteForDateResponseDTO,
  Scenario,
  SearchRequestDTO,
  SearchResponseDTO,
  UnifiedOffer,
} from '../../models/flight';
import { BestBannerComponent } from '../../shared/best-banner/best-banner';
import { FlightCardComponent } from '../../shared/flight-card/flight-card';
import { PriceCalendarComponent } from '../../shared/price-calendar/price-calendar';
import { ProviderComparisonComponent } from '../../shared/provider-comparison/provider-comparison';
import { RatesEditorComponent } from '../../shared/rates-editor/rates-editor';
import { SmartExploreSectionComponent } from '../../shared/smart-explore/smart-explore';
import { SmartQuoteComponent } from '../../shared/smart-quote/smart-quote';
import { SplitSectionComponent } from '../../shared/split-section/split-section';
import { SCENARIO_META, SCENARIO_ORDER, airlineKey, airlineName } from '../../shared/helpers';

type SortMode = 'price' | 'duration';
type TabKey = 'all' | Scenario | 'source';

interface TabDef {
  key: string;
  label: string;
  count: number;
  offers: UnifiedOffer[];
}

@Component({
  selector: 'app-search-page',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    FlightCardComponent,
    BestBannerComponent,
    PriceCalendarComponent,
    ProviderComparisonComponent,
    RatesEditorComponent,
    SmartExploreSectionComponent,
    SmartQuoteComponent,
    SplitSectionComponent,
  ],
  templateUrl: './search-page.html',
  styleUrl: './search-page.scss',
})
export class SearchPageComponent {
  private api = inject(ApiService);
  readonly theme = inject(ThemeService);
  readonly settings = inject(SettingsService);

  chatInput = signal('');
  parsedIntent = signal<ParseIntentResponseDTO | null>(null);
  parsing = signal(false);
  ratesDrawerOpen = signal(false);
  smartMode = signal(false);

  form: SearchRequestDTO = {
    origin: 'GRU',
    destination: 'SSA',
    date_start: this.tomorrow(),
    adults: 1,
    direct_only: false,
    flex_mode: 'none',
    flex_days: 0,
    top_n: 20,
  };
  formExpanded = signal(false);

  loading = signal(false);
  error = signal<string | null>(null);
  response = signal<SearchResponseDTO | null>(null);
  sortMode = signal<SortMode>('price');
  activeTab = signal<string>('all');

  // ── Smart Quote (2-phase) state ──
  exploreLoading = signal(false);
  exploreError = signal<string | null>(null);
  exploreResult = signal<ExploreResponseDTO | null>(null);

  quoteForDateLoading = signal(false);
  quoteForDateError = signal<string | null>(null);
  quoteForDateResult = signal<QuoteForDateResponseDTO | null>(null);
  smartSelectedDate = signal<string | null>(null);
  smartQuoteStale = signal(false);

  /** Split solicitado dentro do fluxo Smart: vendedor clicou em "Quebrar trecho
   * nesta data" no smart-explore. Quando setado, mostramos o app-split-section
   * abaixo, parametrizado com a data escolhida. */
  smartSplitRequest = signal<{ date: string; hub: string; baggage: boolean } | null>(null);

  scenariosForRender = computed(() => {
    const resp = this.response();
    if (!resp) return [];
    const sortFn = (a: UnifiedOffer, b: UnifiedOffer) => {
      if (this.sortMode() === 'duration') {
        return (a.outbound.duration_min ?? Infinity) - (b.outbound.duration_min ?? Infinity);
      }
      return (a.equivalent_brl ?? Infinity) - (b.equivalent_brl ?? Infinity);
    };
    return SCENARIO_ORDER.filter((k) => (resp.scenarios?.[k]?.length ?? 0) > 0).map((k) => ({
      key: k,
      meta: SCENARIO_META[k],
      offers: [...(resp.scenarios[k] ?? [])].sort(sortFn),
    }));
  });

  totalOffers = computed(() => {
    const resp = this.response();
    if (!resp) return 0;
    return Object.values(resp.scenarios ?? {}).reduce((acc, list) => acc + (list?.length ?? 0), 0);
  });

  /** True when the user asked for a round-trip search but there are hidden-city
   * results — the user should be warned that hidden city only works one-way. */
  hiddenCityRoundtripWarning = computed<boolean>(() => {
    const resp = this.response();
    if (!resp) return false;
    const wantsReturn = !!this.form.date_return;
    const hasHidden = (resp.scenarios?.hidden_city?.length ?? 0) > 0;
    return wantsReturn && hasHidden;
  });

  /** True when user asked for direct flights and we have multi-adult — flags
   * extra risk on hidden city PNRs. */
  multiPaxHiddenWarning = computed<boolean>(() => {
    const resp = this.response();
    if (!resp) return false;
    const adults = this.form.adults ?? 1;
    const hasHidden = (resp.scenarios?.hidden_city?.length ?? 0) > 0;
    return adults > 1 && hasHidden;
  });

  /** Cheapest cash price seen in the response — used by SmartQuote to compute
   * "miles equivalent" client-side. */
  cheapestCashPrice = computed<number | null>(() => {
    const resp = this.response();
    if (!resp) return null;
    const cands = (resp.money_offers ?? [])
      .map((o) => o.price_brl ?? o.equivalent_brl)
      .filter((v): v is number => v != null && v > 0);
    return cands.length ? Math.min(...cands) : null;
  });

  /** Unique IATA carriers seen in money + miles offers — fed to SmartQuote. */
  carriersSeen = computed<string[]>(() => {
    const resp = this.response();
    if (!resp) return [];
    const all = [...(resp.money_offers ?? []), ...(resp.miles_offers ?? [])];
    const codes = new Set<string>();
    for (const o of all) {
      for (const s of o.outbound?.segments ?? []) {
        if (s.carrier) codes.add(s.carrier.toUpperCase());
      }
    }
    return [...codes];
  });

  /** Tabs organizadas por COMPANHIA aérea — espelha a UX legado em que o
   * vendedor pensa "quanto custa via LATAM? e via GOL?". Tabs auxiliares
   * para "Internacional" (qualquer cia não-BR) e "Ranking Geral". */
  airlineTabs = computed<TabDef[]>(() => {
    const resp = this.response();
    if (!resp) return [];
    const all: UnifiedOffer[] = [
      ...(resp.money_offers ?? []),
      ...(resp.miles_offers ?? []),
    ];

    const sortFn = (a: UnifiedOffer, b: UnifiedOffer) => {
      if (this.sortMode() === 'duration') {
        return (a.outbound.duration_min ?? Infinity) - (b.outbound.duration_min ?? Infinity);
      }
      return (a.equivalent_brl ?? Infinity) - (b.equivalent_brl ?? Infinity);
    };

    const buckets = new Map<string, UnifiedOffer[]>();
    for (const o of all) {
      const code = this.primaryCarrierKey(o);
      const bucket = this.bucketForCarrier(code);
      if (!buckets.has(bucket)) buckets.set(bucket, []);
      buckets.get(bucket)!.push(o);
    }

    const order = ['LA', 'G3', 'AD', 'INTL'];
    const tabs: TabDef[] = [
      { key: 'all', label: 'Todas', count: all.length, offers: [...all].sort(sortFn).slice(0, 60) },
    ];
    for (const k of order) {
      const list = buckets.get(k);
      if (list?.length) {
        tabs.push({
          key: k,
          label: this.labelForBucket(k),
          count: list.length,
          offers: list.sort(sortFn),
        });
      }
    }
    // Ranking Geral — top-N rankeado, independente da cia.
    if (resp.ranked_offers?.length) {
      tabs.push({
        key: 'ranking',
        label: 'Ranking Geral',
        count: resp.ranked_offers.length,
        offers: resp.ranked_offers,
      });
    }
    return tabs;
  });

  /** Cartões "Ranking por companhia" mostrados no veredito (3 cards: LATAM/GOL/AZUL). */
  airlineRanking = computed<{ code: string; name: string; best: UnifiedOffer }[]>(() => {
    const resp = this.response();
    if (!resp) return [];
    const all = [...(resp.money_offers ?? []), ...(resp.miles_offers ?? [])];
    const byKey = new Map<string, UnifiedOffer[]>();
    for (const o of all) {
      const code = this.primaryCarrierKey(o);
      if (!['LA', 'G3', 'AD'].includes(code)) continue;
      if (!byKey.has(code)) byKey.set(code, []);
      byKey.get(code)!.push(o);
    }
    const out: { code: string; name: string; best: UnifiedOffer }[] = [];
    for (const code of ['LA', 'G3', 'AD']) {
      const list = byKey.get(code);
      if (!list?.length) continue;
      const best = list.reduce((a, b) =>
        (a.equivalent_brl ?? Infinity) <= (b.equivalent_brl ?? Infinity) ? a : b,
      );
      out.push({ code, name: airlineName(code), best });
    }
    out.sort((a, b) => (a.best.equivalent_brl ?? Infinity) - (b.best.equivalent_brl ?? Infinity));
    return out;
  });

  currentTabOffers = computed<UnifiedOffer[]>(() => {
    const tab = this.airlineTabs().find((t) => t.key === this.activeTab());
    return tab?.offers ?? [];
  });

  /** Retorna o IATA principal do voo — usa o primeiro segmento, normalizado
   * via airlineKey (que converte "GOL Linhas Aéreas" → "G3" etc.). */
  private primaryCarrierKey(o: UnifiedOffer): string {
    const seg = o.outbound?.segments?.[0];
    return airlineKey(seg?.carrier || o.airline || '');
  }

  /** Bucket para a tab: BR doméstico vai pra própria tab, resto = "INTL". */
  private bucketForCarrier(code: string): string {
    if (['LA', 'G3', 'AD'].includes(code)) return code;
    return 'INTL';
  }

  private labelForBucket(key: string): string {
    if (key === 'LA') return 'LATAM';
    if (key === 'G3') return 'GOL';
    if (key === 'AD') return 'AZUL';
    if (key === 'INTL') return 'Internacional';
    return key;
  }

  onChatSubmit(): void {
    const text = this.chatInput().trim();
    if (!text) {
      this.error.set('Digite uma busca em texto livre ou abra o formulário manual.');
      return;
    }
    this.parsing.set(true);
    this.error.set(null);

    this.api.parseIntent(text).subscribe({
      next: (intent) => {
        this.parsedIntent.set(intent);
        this.applyIntentToForm(intent);
        this.parsing.set(false);
        // Auto-fire search if we got the essential fields.
        if (this.form.origin && this.form.destination && this.form.date_start) {
          this.runSearch();
        } else {
          this.error.set('A IA não conseguiu extrair origem/destino/data. Ajuste manualmente.');
          this.formExpanded.set(true);
        }
      },
      error: (err) => {
        this.error.set(this.extractError(err));
        this.parsing.set(false);
      },
    });
  }

  onManualSubmit(): void {
    if (!this.form.origin || !this.form.destination || !this.form.date_start) {
      this.error.set('Preencha origem, destino e data.');
      return;
    }
    this.runSearch();
  }

  /** Decide entre busca normal e Cotação Inteligente (Phase 1) com base
   * no toggle. No legado, o toggle ativo torna a busca normal indisponível —
   * só roda a exploração de datas até o vendedor escolher uma. */
  private runSearch(): void {
    if (this.smartMode()) {
      this.runSmartExplore();
    } else {
      this.runNormalSearch();
    }
  }

  private runNormalSearch(): void {
    this.loading.set(true);
    this.error.set(null);
    this.response.set(null);
    this.activeTab.set('all');
    // Smart state limpo quando voltamos pra busca normal.
    this.clearSmartState();

    this.api.search(this.cleanPayload()).subscribe({
      next: (resp) => {
        this.response.set(resp);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(this.extractError(err));
        this.loading.set(false);
      },
    });
  }

  /** Phase 1 da Cotação Inteligente: Kayak em ±N dias.
   * Não dispara a busca de milhas/Skiplagged — espera o vendedor escolher
   * uma data e clicar em "Buscar milhas para esta data". */
  runSmartExplore(): void {
    if (!this.form.origin || !this.form.destination || !this.form.date_start) {
      this.error.set('Preencha origem, destino e data.');
      return;
    }
    // Limpa estado anterior — busca normal e smart são mutuamente exclusivos.
    this.response.set(null);
    this.error.set(null);
    this.exploreError.set(null);
    this.exploreResult.set(null);
    this.quoteForDateResult.set(null);
    this.quoteForDateError.set(null);
    this.smartSelectedDate.set(null);
    this.smartQuoteStale.set(false);
    this.exploreLoading.set(true);

    this.api
      .smartExplore({
        origin: this.form.origin,
        destination: this.form.destination,
        date_start: this.form.date_start,
        adults: this.form.adults || 1,
        flex_days: this.form.flex_days || 4,
      })
      .subscribe({
        next: (resp) => {
          this.exploreResult.set(resp);
          this.exploreLoading.set(false);
          // Pré-seleciona a data central (a que o usuário pediu) — o
          // vendedor decide explicitamente se prefere outra antes de cotar.
          this.smartSelectedDate.set(resp.requested_date);
        },
        error: (err) => {
          this.exploreError.set(this.extractError(err));
          this.exploreLoading.set(false);
        },
      });
  }

  /** Usuário clicou em uma data no calendário (barra ou botão).
   * Se já tinha uma cotação completa, marca como stale; senão fica
   * aguardando o CTA "Buscar milhas para esta data". */
  onSmartDatePicked(iso: string): void {
    const previous = this.smartSelectedDate();
    this.smartSelectedDate.set(iso);
    if (previous !== iso && this.quoteForDateResult()) {
      this.smartQuoteStale.set(true);
    }
    // Split mostrado fica preso à data; ao trocar, descarta.
    if (previous !== iso) {
      this.smartSplitRequest.set(null);
    }
  }

  /** Phase 2: dispara cotação completa (Kayak + BuscaMilhas + Economilhas +
   * Skiplagged) para a data escolhida. Quando o form tem data de volta,
   * roda em modo roundtrip — backend constroi ofertas com ida + volta
   * possivelmente em programas diferentes (multi-trechos). */
  runSmartQuoteForDate(iso: string): void {
    if (!this.form.origin || !this.form.destination) return;
    this.quoteForDateLoading.set(true);
    this.quoteForDateError.set(null);
    this.quoteForDateResult.set(null);

    this.api
      .quoteForDate({
        origin: this.form.origin,
        destination: this.form.destination,
        date: iso,
        return_date: this.form.date_return || null,
        adults: this.form.adults || 1,
        include_kayak: true,
      })
      .subscribe({
        next: (resp) => {
          this.quoteForDateResult.set(resp);
          this.quoteForDateLoading.set(false);
          this.smartQuoteStale.set(false);
        },
        error: (err) => {
          this.quoteForDateError.set(this.extractError(err));
          this.quoteForDateLoading.set(false);
        },
      });
  }

  /** Recebe (runSplit) do smart-explore — vendedor clicou em "Quebrar trecho
   * nesta data". Hub e bagagem agora são controlados DENTRO do split-section. */
  onSmartSplitRequested(payload: { date: string }): void {
    this.smartSplitRequest.set({ date: payload.date, hub: 'GRU', baggage: false });
  }

  private clearSmartState(): void {
    this.exploreResult.set(null);
    this.exploreError.set(null);
    this.exploreLoading.set(false);
    this.quoteForDateResult.set(null);
    this.quoteForDateError.set(null);
    this.quoteForDateLoading.set(false);
    this.smartSelectedDate.set(null);
    this.smartQuoteStale.set(false);
    this.smartSplitRequest.set(null);
  }

  private applyIntentToForm(intent: ParseIntentResponseDTO): void {
    if (intent.origin_iata) this.form.origin = intent.origin_iata;
    if (intent.destination_iata) this.form.destination = intent.destination_iata;
    if (intent.date_start) this.form.date_start = intent.date_start;
    if (intent.date_return) this.form.date_return = intent.date_return;
    if (intent.adults && intent.adults > 0) this.form.adults = intent.adults;
    if (intent.cabin) this.form.cabin = intent.cabin;
    if (intent.direct_only) this.form.direct_only = intent.direct_only;
  }

  private cleanPayload(): SearchRequestDTO {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(this.form)) {
      if (v === '' || v === undefined || v === null) continue;
      out[k] = v;
    }
    // Aplica preferências persistidas (fontes, fixtures, refresh sempre).
    const companhias = this.settings.companhiasPayload();
    if (companhias.length) out['companhias'] = companhias;
    if (this.settings.useFixtures()) out['use_fixtures'] = true;
    if (this.settings.forceRefreshDefault()) out['force_refresh'] = true;
    return out as unknown as SearchRequestDTO;
  }

  private extractError(err: unknown): string {
    const e = err as { error?: unknown; message?: string };
    const detail = (e?.error as { detail?: unknown })?.detail;

    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => {
          const loc = Array.isArray(d?.loc) ? d.loc.slice(1).join('.') : '';
          const msg = d?.msg ?? 'inválido';
          return loc ? `${loc}: ${msg}` : msg;
        })
        .join(' · ');
    }
    if (e?.message) return e.message;
    return 'Falha ao buscar.';
  }

  toggleForm(): void {
    this.formExpanded.update((v) => !v);
  }

  swapOriginDestination(): void {
    const o = this.form.origin;
    this.form.origin = this.form.destination;
    this.form.destination = o;
  }

  /** Triggered when user clicks a date in the price calendar. */
  onCalendarDateSelected(iso: string): void {
    this.form.date_start = iso;
    // Drop the return date if it's now earlier than the new departure.
    if (this.form.date_return && this.form.date_return < iso) {
      this.form.date_return = undefined;
    }
    this.runSearch();
  }

  onFlexChange(days: number): void {
    const v = Math.max(0, Math.min(7, Math.floor(days || 0)));
    this.form.flex_days = v;
    this.form.flex_mode = v > 0 ? 'plusminus' : 'none';
  }

  /** Toggle Cotação Inteligente — quando muda, limpamos o estado da outra
   * via para evitar UI mostrando dados das duas buscas simultaneamente. */
  onSmartToggle(enabled: boolean): void {
    this.smartMode.set(enabled);
    if (enabled) {
      this.response.set(null);
      // Garantir um flex_days mínimo razoável quando smart é ativado pela
      // primeira vez (legado usa 4 como default).
      if (!this.form.flex_days || this.form.flex_days < 1) {
        this.form.flex_days = 4;
        this.form.flex_mode = 'plusminus';
      }
    } else {
      this.clearSmartState();
    }
  }

  /** Forces a cache-bypass refresh of the current search.
   * Critical when the seller is about to close a sale — fares are volatile. */
  refreshPrices(): void {
    if (!this.response()) return;
    const payload = { ...this.cleanPayload(), force_refresh: true } as SearchRequestDTO;
    this.loading.set(true);
    this.error.set(null);
    this.api.search(payload).subscribe({
      next: (resp) => {
        this.response.set(resp);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(this.extractError(err));
        this.loading.set(false);
      },
    });
  }

  private tomorrow(): string {
    const d = new Date();
    d.setDate(d.getDate() + 7);
    return d.toISOString().slice(0, 10);
  }
}
