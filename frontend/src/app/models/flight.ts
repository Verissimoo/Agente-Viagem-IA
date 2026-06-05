/**
 * Modelos TypeScript que espelham os DTOs em
 * backend/app/api/v1/schemas/. Mantidos manualmente alinhados; em
 * fases futuras dá pra gerar via openapi-typescript.
 */

export type Scenario =
  | 'cash_direct'
  | 'miles_direct'
  | 'hidden_city'
  | 'split_cash'
  | 'split_miles';

export type TripType = 'oneway' | 'roundtrip';
export type CabinClass = 'economy' | 'business' | 'first';

export interface Segment {
  origin: string;
  destination: string;
  departure_dt: string;
  arrival_dt: string;
  carrier: string;
  flight_number?: string | null;
}

export interface Itinerary {
  segments: Segment[];
  duration_min?: number | null;
}

export interface UnifiedOffer {
  source: string;
  airline: string;
  trip_type: TripType;
  outbound: Itinerary;
  inbound?: Itinerary | null;
  stops_out?: number | null;
  stops_in?: number | null;
  price_brl?: number | null;
  price_amount?: number | null;
  price_currency?: string | null;
  miles?: number | null;
  miles_program?: string | null;
  taxes_brl?: number | null;
  equivalent_brl?: number | null;
  deeplink?: string | null;
  scenario?: Scenario | null;
  layover_city?: string | null;
  risk_notes?: string | null;
  miles_equivalent?: number | null;
  miles_equivalent_program?: string | null;
  captured_at?: string | null;
}

export interface SearchRequestDTO {
  origin: string;
  destination: string;
  date_start: string;          // ISO yyyy-MM-dd
  date_return?: string | null;
  adults?: number;
  cabin?: CabinClass;
  direct_only?: boolean;
  baggage_checked?: boolean;
  flex_mode?: 'none' | 'plusminus' | 'range';
  flex_days?: number;
  date_end?: string | null;
  flex_return?: boolean;
  companhias?: string[] | null;
  top_n?: number;
  include_summary?: boolean;
  force_refresh?: boolean;
  use_fixtures?: boolean;
}

export interface SearchResponseDTO {
  request_id: string;
  best_overall?: UnifiedOffer | null;
  best_money?: UnifiedOffer | null;
  best_miles?: UnifiedOffer | null;
  ranked_offers: UnifiedOffer[];
  money_offers: UnifiedOffer[];
  miles_offers: UnifiedOffer[];
  scenarios: Partial<Record<Scenario, UnifiedOffer[]>>;
  best_depart_date?: string | null;
  best_depart_date_equivalent_brl?: number | null;
  best_depart_date_source?: string | null;
  date_best_map?: Record<string, number>;
  offers_by_depart_date?: Record<string, number>;
  justification: string[];
  direct_filter_warning?: string | null;
  summary?: string | null;
}

export interface HealthResponseDTO {
  status: string;
  version: string;
  adapters: string[];
}

export interface SplitRequestDTO {
  origin: string;
  destination: string;
  date: string;
  adults?: number;
  return_date?: string | null;
  hub?: string;
}

export interface KayakLegDTO {
  id: string;
  airline: string;
  airlines: string[];
  airlines_iata: string[];
  origin: string;
  destination: string;
  departure_dt?: string | null;
  arrival_dt?: string | null;
  duration_min: number;
  stops: number;
  price_brl: number;
}

export interface MilesMatchOptionDTO {
  program: string;
  miles: number;
  miles_brl_equivalent: number;
  taxes_brl: number;
  total_real_cost_brl: number;
  flight_number: string;
  carrier: string;
  departure_dt?: string | null;
  arrival_dt?: string | null;
  is_exact_match: boolean;
  is_in_window: boolean;
  layover_minutes: number;
}

export interface MilesMatchRequestDTO {
  leg: Omit<KayakLegDTO, 'id'>;
  leg_type: 'domestic' | 'international';
  other_leg_dt: string;
  other_leg_direction: 'before_intl' | 'after_intl';
  with_baggage?: boolean;
  adults?: number;
  provider?: 'buscamilhas' | 'economilhas';
}

export interface MilesMatchResponseDTO {
  leg_type: string;
  target_carrier: string;
  programs_searched: string[];
  options: MilesMatchOptionDTO[];
  has_exact_match: boolean;
  no_results_reason: string | null;
  notes: string[];
}

export interface ProgramRecommendationDTO {
  program: string;
  label: string;
  cost_per_mile_brl: number;
  miles_equivalent: number | null;
  covers_carriers: string[];
}

export interface SmartQuoteRequestDTO {
  origin: string;
  destination: string;
  date: string;
  adults?: number;
  estimated_price_brl?: number | null;
  carriers_seen?: string[];
}

export interface SmartQuoteResponseDTO {
  origin: string;
  destination: string;
  date: string;
  skiplagged_reference_program: string;
  programs: ProgramRecommendationDTO[];
}

// ── Smart Quote (2-phase intelligent flow) ──
export interface ExploreRequestDTO {
  origin: string;
  destination: string;
  date_start: string;
  adults?: number;
  cabin?: CabinClass;
  flex_days?: number;
}

export interface CarrierStatDTO {
  iata: string;
  name: string;
  min_price_brl: number;
  offer_count: number;
}

export interface DayQuoteDTO {
  date: string;
  min_price_brl: number | null;
  offer_count: number;
  carriers: CarrierStatDTO[];
}

export interface ExploreResponseDTO {
  origin: string;
  destination: string;
  central_date: string;
  days: DayQuoteDTO[];
  best_date: string | null;
  best_price_brl: number | null;
  best_carrier_iata: string | null;
  requested_date: string;
  requested_date_price_brl: number | null;
  savings_brl: number;
  is_already_best: boolean;
  stability: 'stable' | 'savings' | 'unknown';
  stability_message: string | null;
}

export interface ProgramOnCarrierDTO {
  program: string;
  label: string;
  cost_per_mile_brl: number;
  own_carrier: boolean;
  award_partner: boolean;
}

export interface BestOfferOnDateDTO {
  carrier_iata: string;
  carrier_name: string;
  flight_number: string | null;
  departure_time: string | null;
  arrival_time: string | null;
  stops: number | null;
  duration_min: number | null;
  price_market_brl: number;
  price_with_markup_brl: number;
  markup_pct: number;
  programs_emitting: ProgramOnCarrierDTO[];
}

export interface QuoteForDateRequestDTO {
  origin: string;
  destination: string;
  date: string;
  return_date?: string | null;
  adults?: number;
  cabin?: CabinClass;
  baggage_checked?: boolean;
  include_skiplagged?: boolean;
  include_buscamilhas?: boolean;
  include_economilhas?: boolean;
  include_kayak?: boolean;
}

export interface TableRowDTO {
  id: string;
  offer_index: number;
  leg: 'IDA' | 'VOLTA';
  carrier_iata: string;
  companhia_label: string;
  source_label: string;
  scenario: Scenario | null;
  risk_notes: string | null;
  layover_official: string | null;
  date: string;
  miles: number | null;
  taxes_brl: number | null;
  real_cost_brl: number | null;
  price_brl: number | null;
  price_with_markup_brl: number | null;
  price_with_baggage_brl: number | null;
  // Bagagem despachada (23kg), por trecho/passageiro:
  // 'included' | 'addable' | 'not_allowed' (hidden city) | 'unknown' (internacional sem dado)
  baggage_status: string | null;
  baggage_note: string | null;
  baggage_extra_brl: number | null;
  duration_min: number | null;
  duration_str: string;
  stops: number;
  departure_time: string | null;
  arrival_time: string | null;
  layover_city: string;
  is_validated: boolean;
  validation_sources: string[];
  hidden_city_miles: HiddenCityMilesQuoteDTO | null;
}

export interface HiddenCityMilesAlternativeDTO {
  source: string;
  program_label: string;
  miles: number;
  taxes_brl: number;
  real_cost_brl: number;
  flight_number: string | null;
  departure_time: string | null;
  arrival_time: string | null;
}

export type HiddenCityRecommendation =
  | 'cash_cheaper'
  | 'miles_cheaper'
  | 'similar'
  | 'direct_better'
  | 'unknown';

export interface DirectFlightCheckDTO {
  origin: string;
  passenger_destination: string;
  direct_min_price_brl: number | null;
  direct_carrier_iata: string | null;
  found_any: boolean;
  savings_vs_hidden_brl: number | null;
  is_hidden_worth_it: boolean;
}

export interface HiddenCityMilesQuoteDTO {
  official_origin: string;
  official_destination: string;
  passenger_destination: string;
  carrier_iata: string;
  carrier_label: string;
  departure_dt: string | null;
  alternatives: HiddenCityMilesAlternativeDTO[];
  has_validated: boolean;
  cash_reference_brl: number | null;
  cheapest_miles_real_cost_brl: number | null;
  savings_brl: number | null;
  recommendation: HiddenCityRecommendation;
  direct_flight: DirectFlightCheckDTO | null;
}

// ── Validação Split em milhas (cada perna do Skiplagged validada na cia) ──
export interface SplitLegInputDTO {
  origin: string;
  destination: string;
  carrier_iata: string;
  departure_dt: string;
  arrival_dt?: string | null;
  flight_number?: string | null;
}

export interface SplitLegValidationDTO {
  origin: string;
  destination: string;
  carrier_iata: string;
  carrier_label: string;
  departure_dt: string;
  flight_number: string | null;
  found_in_miles: boolean;
  found_exact_flight: boolean;
  alternatives: HiddenCityMilesAlternativeDTO[];
  cheapest_miles_real_cost_brl: number | null;
  note: string | null;
}

export type SplitMilesRecommendation =
  | 'miles_cheaper'
  | 'cash_cheaper'
  | 'similar'
  | 'incomplete'
  | 'unknown';

export interface SplitMilesValidationRequestDTO {
  legs: SplitLegInputDTO[];
  cash_reference_brl?: number | null;
  adults?: number;
  cabin?: string;
}

export interface SplitMilesValidationResponseDTO {
  legs: SplitLegValidationDTO[];
  all_found_in_miles: boolean;
  total_cheapest_miles_brl: number | null;
  cash_reference_brl: number | null;
  savings_brl: number | null;
  recommendation: SplitMilesRecommendation;
  summary_note: string | null;
}

export interface CarrierBucketDTO {
  code: string;
  label: string;
  rows: TableRowDTO[];
  best: TableRowDTO | null;
  has_results: boolean;
}

export interface VerdictCardDTO {
  kind: 'overall' | 'miles' | 'money';
  label: string;
  row: TableRowDTO | null;
  description: string;
}

export interface QuoteForDateResponseDTO {
  origin: string;
  destination: string;
  date: string;
  return_date: string | null;
  miles_offers: UnifiedOffer[];
  cash_offers: UnifiedOffer[];
  best_offer_on_date: BestOfferOnDateDTO | null;

  // Cotação Completa
  flat_offers: UnifiedOffer[];
  buckets: Record<string, CarrierBucketDTO>;
  bucket_order: string[];
  airline_ranking: CarrierBucketDTO[];
  verdict: VerdictCardDTO[];
  summary: string;
  comparison_note: string | null;
}

export interface QuoteForDateRequestDTOExtended extends QuoteForDateRequestDTO {
  return_date?: string | null;
  include_kayak?: boolean;
}

// ── Flight Validation (BuscaMilhas-backed) ──
export interface ValidateFlightRequestDTO {
  carrier: string;
  origin: string;
  destination: string;
  departure_dt: string;
  adults?: number;
  quoted_price_brl?: number | null;
  quoted_miles?: number | null;
}

export interface MatchedFlightDTO {
  flight_number?: string | null;
  carrier?: string | null;
  departure_dt?: string | null;
  arrival_dt?: string | null;
  miles: number;
  taxes_brl: number;
}

export type ValidateFlightStatus =
  | 'found_with_match'
  | 'found_no_match'
  | 'no_offers'
  | 'unsupported_carrier'
  | 'error';

export interface ValidateFlightResponseDTO {
  status: ValidateFlightStatus;
  message: string;
  carrier: string;
  program?: string | null;
  queried_date: string;
  matches: MatchedFlightDTO[];
  nearby: MatchedFlightDTO[];
  cheapest_miles?: number | null;
  cheapest_total_brl?: number | null;
  price_comparison?: string | null;
}

export interface SplitResponseDTO {
  origin: string;
  destination: string;
  date: string;
  route_type: 'br_to_intl' | 'intl_to_br' | 'br_domestic' | 'not_applicable';
  hub: string;
  leg_to_hub: KayakLegDTO[];
  leg_from_hub: KayakLegDTO[];
  direct: KayakLegDTO | null;
  not_applicable_reason: string | null;
  notes: string[];
}

// ── Fase 2: encaixe doméstico ──
export interface FitOfferDTO extends KayakLegDTO {
  layover_minutes: number;
  layover_status: 'ok' | 'tight' | 'long' | 'invalid';
}

export interface SplitFitRequestDTO {
  intl_offer: Omit<KayakLegDTO, 'id'>;
  other_endpoint: string;
  intl_direction: 'from_gru' | 'to_gru';
  adults?: number;
  with_baggage?: boolean;
}

export interface SplitFitResponseDTO {
  search_date: string;
  search_date_offset: 'same_day' | 'day_before' | 'day_after';
  target_window_start: string | null;
  target_window_end: string | null;
  compatible_offers: FitOfferDTO[];
  incompatible_offers: FitOfferDTO[];
  no_results: boolean;
  with_baggage: boolean;
  notes: string[];
}

// ── Fase 3: busca direta de milhas hub → destino ──
export interface SplitMilesSearchRequestDTO {
  origin: string;
  destination: string;
  date: string;
  adults?: number;
  return_date?: string | null;
}

export interface SplitMilesOfferDTO {
  id: string;
  source: string;
  program: string | null;
  carrier: string;
  carrier_name: string;
  flight_number: string | null;
  origin: string;
  destination: string;
  departure_dt: string | null;
  arrival_dt: string | null;
  duration_min: number;
  stops: number;
  miles: number;
  taxes_brl: number;
  equivalent_brl: number;
  deeplink: string | null;
}

export interface SplitMilesSearchResponseDTO {
  origin: string;
  destination: string;
  date: string;
  offers: SplitMilesOfferDTO[];
  programs_seen: string[];
  carriers_seen: string[];
  notes: string[];
}

export interface ParseIntentRequestDTO {
  text: string;
}

export interface RateTier {
  max_miles: number | null;
  rate: number;
}

export interface RatesResponseDTO {
  programs: Record<string, RateTier[]>;
  international_fallback_rate: number;
  skiplagged_estimation_program: string;
}

export interface ParseIntentResponseDTO {
  origin_iata?: string | null;
  destination_iata?: string | null;
  origin_city?: string | null;
  destination_city?: string | null;
  date_start?: string | null;
  date_return?: string | null;
  trip_type?: TripType;
  cabin?: CabinClass;
  adults?: number;
  direct_only?: boolean;
  flex_mode?: 'none' | 'plusminus' | 'range';
  flex_days?: number | null;
  confidence?: number;
  notes?: string | null;
}
