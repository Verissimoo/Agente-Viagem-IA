import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';
import {
  ExploreRequestDTO,
  ExploreResponseDTO,
  HealthResponseDTO,
  HiddenCityMilesQuoteDTO,
  SplitFitRequestDTO,
  SplitFitResponseDTO,
  SplitMilesSearchRequestDTO,
  SplitMilesSearchResponseDTO,
  SplitMilesValidationRequestDTO,
  SplitMilesValidationResponseDTO,
  MilesMatchRequestDTO,
  MilesMatchResponseDTO,
  ParseIntentRequestDTO,
  ParseIntentResponseDTO,
  QuoteForDateRequestDTO,
  QuoteForDateResponseDTO,
  RatesResponseDTO,
  SearchRequestDTO,
  SearchResponseDTO,
  SmartQuoteRequestDTO,
  SmartQuoteResponseDTO,
  SplitRequestDTO,
  SplitResponseDTO,
  ValidateFlightRequestDTO,
  ValidateFlightResponseDTO,
} from '../models/flight';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);
  private baseUrl = environment.apiBaseUrl;

  health(): Observable<HealthResponseDTO> {
    return this.http.get<HealthResponseDTO>(`${this.baseUrl}/health`);
  }

  parseIntent(text: string): Observable<ParseIntentResponseDTO> {
    const body: ParseIntentRequestDTO = { text };
    return this.http.post<ParseIntentResponseDTO>(`${this.baseUrl}/parse-intent`, body);
  }

  search(payload: SearchRequestDTO): Observable<SearchResponseDTO> {
    return this.http.post<SearchResponseDTO>(`${this.baseUrl}/search`, payload);
  }

  getRates(): Observable<RatesResponseDTO> {
    return this.http.get<RatesResponseDTO>(`${this.baseUrl}/rates`);
  }

  updateRates(payload: RatesResponseDTO): Observable<RatesResponseDTO> {
    return this.http.put<RatesResponseDTO>(`${this.baseUrl}/rates`, payload);
  }

  split(payload: SplitRequestDTO): Observable<SplitResponseDTO> {
    return this.http.post<SplitResponseDTO>(`${this.baseUrl}/split`, payload);
  }

  /** Fase 2 — encaixe doméstico: para um voo internacional escolhido, busca
   * voos domésticos compatíveis na janela de conexão. */
  splitFit(payload: SplitFitRequestDTO): Observable<SplitFitResponseDTO> {
    return this.http.post<SplitFitResponseDTO>(`${this.baseUrl}/split/fit`, payload);
  }

  /** Fase 3 — busca direta de milhas hub → destino (sem âncora em voo Kayak).
   * Devolve as ofertas reais de programas (Smiles/LATAM Pass/etc) pra UI usar
   * como ponto de partida ao invés de tentar achar um voo Kayak específico. */
  splitMilesSearch(payload: SplitMilesSearchRequestDTO): Observable<SplitMilesSearchResponseDTO> {
    return this.http.post<SplitMilesSearchResponseDTO>(`${this.baseUrl}/split/miles-search`, payload);
  }

  milesMatch(payload: MilesMatchRequestDTO): Observable<MilesMatchResponseDTO> {
    return this.http.post<MilesMatchResponseDTO>(`${this.baseUrl}/miles-match`, payload);
  }

  smartQuote(payload: SmartQuoteRequestDTO): Observable<SmartQuoteResponseDTO> {
    return this.http.post<SmartQuoteResponseDTO>(`${this.baseUrl}/smart-quote`, payload);
  }

  smartExplore(payload: ExploreRequestDTO): Observable<ExploreResponseDTO> {
    return this.http.post<ExploreResponseDTO>(`${this.baseUrl}/smart-quote/explore`, payload);
  }

  quoteForDate(payload: QuoteForDateRequestDTO): Observable<QuoteForDateResponseDTO> {
    return this.http.post<QuoteForDateResponseDTO>(`${this.baseUrl}/smart-quote/quote-for-date`, payload);
  }

  /** Cota milhas no itinerário oficial de uma oferta Skiplagged hidden city.
   * Sob demanda: chamado quando o vendedor abre uma linha Skiplagged que
   * não veio com cotação eager-loaded. */
  hiddenCityMiles(payload: {
    origin: string;
    destination: string;
    passenger_destination: string;
    carrier_iata: string;
    date: string;
    departure_time?: string | null;
    adults?: number;
    cash_reference_brl?: number | null;
  }): Observable<HiddenCityMilesQuoteDTO | null> {
    return this.http.post<HiddenCityMilesQuoteDTO | null>(
      `${this.baseUrl}/smart-quote/hidden-city-miles`,
      payload,
    );
  }

  /** Valida cada perna de um Skiplagged Split em milhas (sem encaixar hub).
   * Apenas confirma se os voos do split existem nos programas de milhas
   * da cia operadora — frontend mostra ✓/⚠ por perna. */
  splitMilesValidation(payload: SplitMilesValidationRequestDTO): Observable<SplitMilesValidationResponseDTO> {
    return this.http.post<SplitMilesValidationResponseDTO>(
      `${this.baseUrl}/smart-quote/split-miles-validation`,
      payload,
    );
  }

  validateFlight(payload: ValidateFlightRequestDTO): Observable<ValidateFlightResponseDTO> {
    return this.http.post<ValidateFlightResponseDTO>(`${this.baseUrl}/validate-flight`, payload);
  }
}
