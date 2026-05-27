import { Scenario, UnifiedOffer } from '../models/flight';

export function formatDuration(minutes: number | null | undefined): string {
  if (!minutes || minutes <= 0) return '—';
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m}min`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}min`;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short' });
}

export function formatBRL(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return value.toLocaleString('pt-BR', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

/** Returns a human "há Xs / há Xmin" string given an ISO timestamp. */
export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  const elapsedMs = now - d.getTime();
  if (elapsedMs < 0) return 'agora';
  const sec = Math.floor(elapsedMs / 1000);
  if (sec < 60) return `há ${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `há ${min} min`;
  const hr = Math.floor(min / 60);
  return `há ${hr}h`;
}

/** True when the offer was captured more than `staleThresholdSec` ago. */
export function isStale(iso: string | null | undefined, staleThresholdSec = 120, now: number = Date.now()): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return false;
  return (now - d.getTime()) / 1000 > staleThresholdSec;
}

const PROGRAM_LABELS: Record<string, string> = {
  LATAM: 'LATAM Pass',
  GOL: 'Smiles',
  AZUL: 'TudoAzul',
  TAP: 'Miles&Go',
  IBERIA: 'Iberia Plus',
  AVIOS: 'Avios',
  'AMERICAN AIRLINES': 'AAdvantage',
  COPA: 'ConnectMiles',
  'ASIA MILES': 'Asia Miles',
  INTERLINE: 'Interline',
  DEFAULT: 'Milhas',
};

export function programLabel(program: string | null | undefined): string {
  if (!program) return 'milhas';
  return PROGRAM_LABELS[program.toUpperCase()] ?? program;
}

const SOURCE_LABELS: Record<string, string> = {
  kayak: 'Kayak',
  economilhas: 'Economilhas',
  buscamilhas_latam: 'BuscaMilhas · LATAM Pass',
  buscamilhas_gol: 'BuscaMilhas · Smiles',
  buscamilhas_azul: 'BuscaMilhas · TudoAzul',
  buscamilhas_tap: 'BuscaMilhas · Miles&Go',
  buscamilhas_iberia: 'BuscaMilhas · Iberia Plus',
  buscamilhas_american: 'BuscaMilhas · AAdvantage',
  buscamilhas_interline: 'BuscaMilhas · Interline',
  buscamilhas_copa: 'BuscaMilhas · ConnectMiles',
  mcp_award: 'MCP Award',
  mcp_qatar: 'MCP · Qatar Privilege',
  skiplagged: 'Skiplagged',
};

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source.replace(/_/g, ' ');
}

const AIRLINE_NAMES: Record<string, string> = {
  AD: 'Azul',
  G3: 'GOL',
  LA: 'LATAM',
  TAM: 'LATAM',
  AR: 'Aerolíneas Argentinas',
  AA: 'American Airlines',
  TP: 'TAP',
  IB: 'Iberia',
  CM: 'Copa',
  QR: 'Qatar',
  BA: 'British Airways',
  AF: 'Air France',
  KL: 'KLM',
  LH: 'Lufthansa',
  UX: 'Air Europa',
  FR: 'Ryanair',
  U2: 'easyJet',
  VY: 'Vueling',
  EK: 'Emirates',
  TK: 'Turkish Airlines',
  AY: 'Finnair',
  SK: 'SAS',
  LX: 'Swiss',
  OS: 'Austrian',
};

export function airlineName(code: string): string {
  return AIRLINE_NAMES[code.toUpperCase()] ?? code;
}

/** Normalizes any airline name/code to its IATA 2-letter code when recognizable.
 * Used to group offers from different providers (Kayak may say "GOL", Economilhas
 * may say "GOL", Skiplagged returns "G3" — all collapse to G3).
 */
const NAME_TO_IATA: Record<string, string> = {
  GOL: 'G3',
  'GOL LINHAS AEREAS': 'G3',
  'GOL LINHAS AÉREAS': 'G3',
  LATAM: 'LA',
  'LATAM AIRLINES': 'LA',
  'LATAM AIRLINES BRASIL': 'LA',
  TAM: 'LA',
  AZUL: 'AD',
  'AZUL LINHAS AEREAS': 'AD',
  'AZUL LINHAS AÉREAS': 'AD',
  TAP: 'TP',
  'TAP AIR PORTUGAL': 'TP',
  IBERIA: 'IB',
  COPA: 'CM',
  'COPA AIRLINES': 'CM',
  AMERICAN: 'AA',
  'AMERICAN AIRLINES': 'AA',
  QATAR: 'QR',
  'BRITISH AIRWAYS': 'BA',
  BRITISH: 'BA',
  'AIR EUROPA': 'UX',
  RYANAIR: 'FR',
  EASYJET: 'U2',
  VUELING: 'VY',
  EMIRATES: 'EK',
  'TURKISH AIRLINES': 'TK',
  TURKISH: 'TK',
  FINNAIR: 'AY',
  SAS: 'SK',
  SWISS: 'LX',
  AUSTRIAN: 'OS',
};

export function airlineKey(name: string | null | undefined): string {
  if (!name) return '';
  const upper = name.trim().toUpperCase();
  if (NAME_TO_IATA[upper]) return NAME_TO_IATA[upper];
  // 2-letter token usually IS the IATA code
  if (upper.length === 2) return upper;
  // 3-letter: TAM → LA, BA→BA, etc. Try substring match.
  for (const k of Object.keys(NAME_TO_IATA)) {
    if (upper.includes(k)) return NAME_TO_IATA[k];
  }
  // Fallback: first 2 chars
  return upper.slice(0, 2);
}

/** Builds a deep-link to search the same route+date directly on the airline's
 * official site, so the seller can verify the fare before closing the sale.
 * Returns null when we have no good URL pattern for that carrier.
 */
export function carrierBookingUrl(
  carrierName: string,
  origin: string,
  destination: string,
  dateISO: string,
  returnDateISO: string | null = null,
  adults: number = 1,
): string | null {
  const code = airlineKey(carrierName);
  const o = origin.toUpperCase();
  const d = destination.toUpperCase();
  const dt = dateISO; // YYYY-MM-DD
  const ret = returnDateISO || '';
  const a = String(Math.max(1, adults));

  // ISO datetime "neutro" usado pelos sites Latam/Azul/etc para abrir
  // a busca pré-filtrada no dia certo (timezone irrelevante).
  const dtUtc = `${dt}T15%3A00%3A00.000Z`;
  const retUtc = ret ? `${ret}T15%3A00%3A00.000Z` : '';

  switch (code) {
    case 'LA': {
      // LATAM migrou em 2024 para latamairlines.com com formato totalmente novo.
      const trip = ret ? 'RT' : 'OW';
      let url =
        `https://www.latamairlines.com/br/pt/oferta-voos?` +
        `origin=${o}&destination=${d}&outbound=${dtUtc}` +
        `&adt=${a}&chd=0&inf=0&trip=${trip}&cabin=Economy&redemption=false&sort=RECOMMENDED`;
      if (ret) url += `&inbound=${retUtc}`;
      return url;
    }
    case 'G3': {
      // GOL: a "smiles search" não tem deeplink público; o site público faz pré-filtro via parâmetros.
      const trip = ret ? 'RT' : 'OW';
      return (
        `https://www.voegol.com.br/pt/passagens-aereas?` +
        `trip=${trip}&origin=${o}&destination=${d}` +
        `&departureDate=${dt}${ret ? `&returnDate=${ret}` : ''}` +
        `&adults=${a}&kids=0&babies=0&currencyCode=BRL`
      );
    }
    case 'AD': {
      // Azul aceita formato dd-mm-yyyy.
      const [yy, mm, dd] = dt.split('-');
      const ddmmyyyy = `${dd}-${mm}-${yy}`;
      const retDdmmyyyy = ret ? ret.split('-').reverse().join('-') : '';
      return (
        `https://viajemais.voeazul.com.br/Search.aspx?` +
        `culture=pt-BR&currency=BRL` +
        `&adults=${a}&children=0&infants=0` +
        `&origin1=${o}&destination1=${d}&date1=${ddmmyyyy}` +
        (ret ? `&origin2=${d}&destination2=${o}&date2=${retDdmmyyyy}&trip=2` : '&trip=1')
      );
    }
    case 'TP':
      return (
        `https://www.flytap.com/pt-br/book?` +
        `origin=${o}&destination=${d}&departureDate=${dt}` +
        (ret ? `&returnDate=${ret}` : '') +
        `&adults=${a}`
      );
    case 'AA':
      return (
        `https://www.aa.com/booking/find-flights?tripType=${ret ? 'roundTrip' : 'oneWay'}&` +
        `from%5B%5D=${o}&to%5B%5D=${d}&departureDate%5B%5D=${dt}` +
        (ret ? `&returnDate=${ret}` : '') +
        `&adult=${a}`
      );
    case 'IB':
      return (
        `https://www.iberia.com/br/buy-flight-tickets/?` +
        `originLocationCode=${o}&destinationLocationCode=${d}&` +
        `departureDate=${dt}` +
        (ret ? `&returnDate=${ret}` : '') +
        `&adults=${a}`
      );
    case 'BA':
      return `https://www.britishairways.com/travel/book/public/pt_br?origin=${o}&destination=${d}&departureDate=${dt}`;
    case 'QR':
      return `https://www.qatarairways.com/pt-br/homepage.html?from=${o}&to=${d}&depart=${dt}&adults=${a}`;
    case 'CM':
      return `https://www.copaair.com/pt-br/?origin=${o}&destination=${d}&depart=${dt}&adults=${a}`;
    case 'KL':
      return `https://www.klm.com.br/search/?` +
        `origin=${o}&destination=${d}&departureDate=${dt}` +
        (ret ? `&returnDate=${ret}` : '') + `&adults=${a}&type=${ret ? 'return' : 'oneway'}`;
    case 'AF':
      return `https://www.airfrance.com.br/search/offers?` +
        `bookingFlow=LEISURE&origin=${o}&destination=${d}&outboundDate=${dt}` +
        (ret ? `&inboundDate=${ret}` : '') + `&pax=${a}&cabin=ECONOMY`;
    case 'LX':
      return `https://www.swiss.com/br/pt/book/outbound/?` +
        `flights=${o}-${d}-${dt}&adults=${a}&class=economy`;
    case 'LH':
      return `https://www.lufthansa.com/br/pt/flight-search?` +
        `from=${o}&to=${d}&departure=${dt}` +
        (ret ? `&return=${ret}` : '') + `&adults=${a}&trip=${ret ? 'roundtrip' : 'oneway'}`;
    case 'UX':
      return `https://www.aireuropa.com/br/pt/aea/home.html?origin=${o}&destination=${d}&date=${dt}&adults=${a}`;
    case 'FR':
      return `https://www.ryanair.com/br/pt/voos-baratos?` +
        `originIata=${o}&destinationIata=${d}&dateOut=${dt}` +
        (ret ? `&dateIn=${ret}` : '') + `&adt=${a}`;
    case 'U2':
      return `https://www.easyjet.com/pt/comprar-passagem/${o.toLowerCase()}/${d.toLowerCase()}?` +
        `dep=${dt}` + (ret ? `&ret=${ret}` : '') + `&num=${a}`;
    case 'VY':
      return `https://www.vueling.com/pt-br/escolha-de-voos?origin=${o}&destination=${d}&departureDate=${dt}&adults=${a}`;
    case 'EK':
      return `https://www.emirates.com/br/portuguese/?from=${o}&to=${d}&depart=${dt}&adults=${a}`;
    case 'TK':
      return `https://www.turkishairlines.com/pt-br/flights/booking/?from=${o}&to=${d}&dep=${dt}&adt=${a}`;
    default:
      // Fallback: Google Flights as a generic verification tool.
      return (
        `https://www.google.com/travel/flights?q=` +
        encodeURIComponent(`Flights from ${o} to ${d} on ${dt}`)
      );
  }
}

export function carrierBookingLabel(carrierName: string): string {
  const code = airlineKey(carrierName);
  const map: Record<string, string> = {
    LA: 'Validar na LATAM',
    G3: 'Validar na GOL',
    AD: 'Validar na Azul',
    TP: 'Validar na TAP',
    AA: 'Validar na American',
    IB: 'Validar na Iberia',
    BA: 'Validar na British',
    QR: 'Validar na Qatar',
    CM: 'Validar na Copa',
    KL: 'Validar na KLM',
    AF: 'Validar na Air France',
    LX: 'Validar na Swiss',
    LH: 'Validar na Lufthansa',
    UX: 'Validar na Air Europa',
    FR: 'Validar na Ryanair',
    U2: 'Validar na easyJet',
    VY: 'Validar na Vueling',
    EK: 'Validar na Emirates',
    TK: 'Validar na Turkish',
  };
  return map[code] ?? 'Validar (Google Flights)';
}

export const SCENARIO_ORDER: Scenario[] = [
  'cash_direct',
  'miles_direct',
  'hidden_city',
  'split_cash',
  'split_miles',
];

export const SCENARIO_META: Record<Scenario, { label: string; color: string; description: string }> = {
  cash_direct: {
    label: 'Cash direto',
    color: '#0d47a1',
    description: 'Voo padrão pagando em dinheiro',
  },
  miles_direct: {
    label: 'Milhas',
    color: '#6a1b9a',
    description: 'Resgate direto em milhas + taxas',
  },
  hidden_city: {
    label: 'Hidden City',
    color: '#ef6c00',
    description: 'Bilhete com destino além — você desembarca na conexão',
  },
  split_cash: {
    label: 'Split (cash)',
    color: '#2e7d32',
    description: 'Dois bilhetes em dinheiro mais baratos que um direto',
  },
  split_miles: {
    label: 'Split (milhas)',
    color: '#558b2f',
    description: 'Combinação de pernas em milhas + cash',
  },
};

export function scenarioOf(offer: UnifiedOffer): Scenario {
  if (offer.scenario) return offer.scenario;
  if (offer.miles) return 'miles_direct';
  return 'cash_direct';
}

export function stopsLabel(segments: number): string {
  const s = Math.max(0, segments - 1);
  if (s === 0) return 'Direto';
  return `${s} parada${s > 1 ? 's' : ''}`;
}
