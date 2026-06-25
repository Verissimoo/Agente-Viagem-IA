// Cliente HTTP do backend FastAPI. Mantém uma só fonte de verdade pro base URL,
// inclui o Bearer token em todas as chamadas, e centraliza a deserialização.

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

// Misconfiguração clássica de produção: o build do Next.js não recebeu
// NEXT_PUBLIC_API_URL (var é inlinada em build-time), então o bundle caiu no
// fallback localhost enquanto a página roda num host remoto. Todo fetch morre
// com TypeError ("Failed to fetch") e a UI só mostrava "Falha criando conversa".
function apiMisconfigHint(): string {
  if (typeof window === "undefined") return "";
  const host = window.location.hostname;
  const remote = host !== "localhost" && host !== "127.0.0.1";
  if (remote && BASE.includes("localhost")) {
    return ` — a aplicação está apontando para ${BASE}. Configure NEXT_PUBLIC_API_URL no build de produção.`;
  }
  return ` (servidor: ${BASE})`;
}

export class ApiError extends Error {
  status: number;
  payload: unknown;
  constructor(status: number, message: string, payload: unknown) {
    super(message);
    this.status = status;
    this.payload = payload;
  }
}

export interface Thread {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  archived: boolean;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface HubLeg {
  label?: string;                    // "Trecho nacional" / "Trecho internacional"
  airline?: string | null;
  kind?: "miles" | "cash";
  miles?: number | null;
  taxes_brl?: number | null;
  program?: string | null;           // programa de milhas desta perna
  equivalent_brl?: number | null;
  cash_cheaper?: {                   // o mesmo trecho sai mais barato em dinheiro
    cash_brl?: number | null;
    savings_brl?: number | null;     // quanto mais barato que em milhas
    airline?: string | null;
  } | null;
}

export interface Offer {
  offer_id: string;
  airline?: string;
  category?: string;
  category_why?: string;
  price_brl?: number | null;
  miles?: number | null;
  miles_program?: string | null;
  taxes_brl?: number | null;
  equivalent_brl?: number | null;   // Total estimado em BRL (milhas convertidas + taxas)
  risk_notes?: string | null;
  outbound?: { segments: Segment[]; duration_min?: number | null };
  inbound?: { segments: Segment[]; duration_min?: number | null } | null;
  // Hidden city: IATA onde o passageiro desembarca de fato
  passenger_disembark_at?: string | null;
  discarded_segments_count?: number | null;
  // Alternativa em milhas pra hidden city ou split (cross-reference / busca suplementar)
  miles_alternative?: {
    airline?: string;
    miles?: number | null;
    taxes_brl?: number | null;
    equivalent_brl?: number | null;
    offer_id?: string;
    validated?: boolean;          // true se veio de busca suplementar direta na cia
    exact_route_match?: boolean;  // true se rota física bate (escala no destino real)
    to_destination?: string | null;  // hidden city: award DIRETO até o destino real (ex.: SSA)
    is_split?: boolean;           // true se é validação de split (tem breakdown)
    split_breakdown?: Array<{
      origin: string;
      destination: string;
      dep_date: string;
      airline?: string | null;
      miles: number;
      taxes_brl: number;
      equivalent_brl?: number | null;
    }>;
  } | null;
  // Ida-e-volta montado como DOIS bilhetes só-ida (hidden city é one-way):
  // breakdown por perna que somou pro total do card.
  roundtrip_legs?: {
    ida?: { airline?: string | null; miles?: number | null; taxes_brl?: number | null; equivalent_brl?: number | null; hidden_city?: boolean; date?: string | null };
    volta?: { airline?: string | null; miles?: number | null; taxes_brl?: number | null; equivalent_brl?: number | null; hidden_city?: boolean; date?: string | null };
  } | null;
  // Quebra de trecho (hub split): cada perna tem PROGRAMA de milhas próprio —
  // mostradas separadas (não somar milhas). `cash_cheaper` sinaliza que o
  // mesmo trecho sai mais barato em dinheiro → procurar emissão melhor.
  split_legs?: {
    domestic?: HubLeg;
    international?: HubLeg;
  } | null;
  // Hidden city: o MESMO BILHETE OFICIAL em milhas (ex.: BSB→FOR passando por
  // SSA), como referência. Quase sempre bem mais caro que o award direto.
  miles_same_ticket?: {
    airline?: string;
    miles?: number | null;
    taxes_brl?: number | null;
    equivalent_brl?: number | null;
    ticket_destination?: string | null;  // destino oficial do bilhete (ex.: FOR)
    via_hub?: string | null;              // escala onde o passageiro desce (ex.: SSA)
    validated?: boolean;
    exact_route_match?: boolean;
  } | null;
  // Otimização de datas via Kayak (só pra splits quando há flex de data)
  kayak_date_optimization?: {
    validated: boolean;
    kayak_optimized: boolean;
    breakdown: Array<{
      origin: string;
      destination: string;
      base_date: string;
      best_date: string;
      airline?: string | null;
      price_brl: number;
      moved_days: number;        // dias deslocados da base
    }>;
    total_price_brl: number;
    original_price_brl?: number | null;
    savings_brl?: number | null;
    flex_days_used: number;
  } | null;
}

export interface Segment {
  origin: string;
  destination: string;
  departure_dt: string;
  arrival_dt: string;
  carrier: string;
  flight_number?: string | null;
  // Hidden city: true se o passageiro voa esse segmento, false se descarta
  used?: boolean;
  discarded?: boolean;
}

export interface Session {
  user_id: string;
  email: string;
  display_name?: string | null;
  store_name?: string | null;
  access_token: string;
}

async function request<T>(
  path: string,
  init: RequestInit & { token?: string | null } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (init.token) headers.set("Authorization", `Bearer ${init.token}`);

  let resp: Response;
  try {
    resp = await fetch(`${BASE}${path}`, { ...init, headers });
  } catch (err) {
    // Falha de rede (servidor inacessível, CORS, mixed-content, DNS): fetch
    // rejeita com TypeError. Vira ApiError com status 0 e mensagem acionável.
    throw new ApiError(0, `Sem conexão com o servidor${apiMisconfigHint()}`, err);
  }
  const text = await resp.text();
  let body: unknown = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }

  if (!resp.ok) {
    const detail =
      typeof body === "object" && body && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : resp.statusText;
    throw new ApiError(resp.status, detail, body);
  }
  return body as T;
}

// ─── Auth ───────────────────────────────────────────────────────────
export const auth = {
  login: (email: string, password: string) =>
    request<Session>("/chat/auth/login", {
      method: "POST", body: JSON.stringify({ email, password }),
    }),

  register: (payload: {
    email: string;
    password: string;
    display_name?: string;
    store_name?: string;
  }) =>
    request<Session>("/chat/auth/register", {
      method: "POST", body: JSON.stringify(payload),
    }),

  me: (token: string) =>
    request<Session>("/chat/auth/me", { token }),

  // Resposta sempre genérica (não revela se o e-mail existe).
  forgotPassword: (email: string) =>
    request<{ message: string }>("/chat/auth/forgot-password", {
      method: "POST", body: JSON.stringify({ email }),
    }),

  // Troca a senha via token do e-mail; devolve sessão já autenticada.
  resetPassword: (token: string, password: string) =>
    request<Session>("/chat/auth/reset-password", {
      method: "POST", body: JSON.stringify({ token, password }),
    }),

  // Reset SIMPLES (sem e-mail): troca a senha pelo e-mail e já autentica.
  // Interino até o SMTP entrar.
  resetPasswordSimple: (email: string, password: string) =>
    request<Session>("/chat/auth/reset-password-simple", {
      method: "POST", body: JSON.stringify({ email, password }),
    }),
};

// ─── Threads ────────────────────────────────────────────────────────
export const threads = {
  list: (token: string) =>
    request<{ threads: Thread[] }>("/chat/threads", { token }),

  create: (token: string, title?: string) =>
    request<Thread>("/chat/threads", {
      method: "POST", token, body: JSON.stringify({ title }),
    }),

  messages: (token: string, threadId: string) =>
    request<{ messages: Message[] }>(`/chat/threads/${threadId}`, { token }),

  // Apaga a thread (cascateia mensagens e cotações). Operação destrutiva.
  remove: (token: string, threadId: string) =>
    request<{ ok: boolean }>(`/chat/threads/${threadId}`, {
      method: "DELETE", token,
    }),

  // Interrompe a cotação em andamento da thread (botão "Interromper").
  cancel: (token: string, threadId: string) =>
    request<{ ok: boolean }>(`/chat/threads/${threadId}/cancel`, {
      method: "POST", token,
    }),

  send: (token: string, threadId: string, content: string) =>
    request<{
      thread_id: string;
      user_message: Message;
      assistant_message: Message;
    }>(`/chat/threads/${threadId}/messages`, {
      method: "POST", token, body: JSON.stringify({ content }),
    }),

  // Streaming via SSE — yielda updates de status durante a execução do grafo.
  // Handlers:
  //   onUserMessage: mensagem persistida do usuário (pra UI confirmar envio)
  //   onStatus: novo status ("Buscando opções em nossas fontes")
  //   onAssistant: resposta final
  //   onDone: encerramento (sucesso ou erro)
  sendStream: async (
    token: string,
    threadId: string,
    content: string,
    handlers: {
      onUserMessage?: (m: Message) => void;
      onStatus?: (text: string, node: string | null) => void;
      onAssistant?: (m: Message) => void;
      onDone?: (info: { error?: boolean }) => void;
      onError?: (err: Error) => void;
    },
  ): Promise<void> => {
    const url = `${BASE}/chat/threads/${threadId}/messages/stream`;
    let errored = false;

    // Garante que onDone SEMPRE roda, mesmo se a conexão morrer.
    // Frontend depende disso pra desligar o `sending` state.
    const finish = () => handlers.onDone?.({ error: errored });

    let resp: Response;
    try {
      resp = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          Accept: "text/event-stream",
        },
        body: JSON.stringify({ content }),
      });
    } catch (err) {
      errored = true;
      handlers.onError?.(err as Error);
      finish();
      return;
    }
    if (!resp.ok) {
      errored = true;
      const text = await resp.text();
      let msg = resp.statusText;
      try { msg = JSON.parse(text).detail || msg; } catch { /* ignore */ }
      handlers.onError?.(new ApiError(resp.status, msg, text));
      finish();
      return;
    }
    if (!resp.body) {
      errored = true;
      handlers.onError?.(new Error("Stream sem body"));
      finish();
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let doneEmittedByServer = false;

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const evt = parseSseEvent(raw);
          if (!evt) continue;
          if (evt.event === "done") doneEmittedByServer = true;
          dispatchEvent(evt, handlers);
        }
      }
    } catch (err) {
      errored = true;
      handlers.onError?.(err as Error);
    } finally {
      // Se servidor encerrou sem mandar `done`, sintetizamos um aqui.
      if (!doneEmittedByServer) finish();
    }
  },
};

function parseSseEvent(raw: string): { event: string; data: unknown } | null {
  let event = "message";
  let dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

function dispatchEvent(
  evt: { event: string; data: unknown },
  handlers: Parameters<typeof threads.sendStream>[3],
) {
  switch (evt.event) {
    case "user_message":
      handlers.onUserMessage?.(evt.data as Message);
      break;
    case "status": {
      const d = evt.data as { text: string; node: string | null };
      handlers.onStatus?.(d.text, d.node);
      break;
    }
    case "message":
      handlers.onAssistant?.(evt.data as Message);
      break;
    case "done":
      handlers.onDone?.((evt.data as { error?: boolean }) || {});
      break;
  }
}

// ─── Quotes ─────────────────────────────────────────────────────────
// ─── Settings: tabela de milhas ─────────────────────────────────
export interface RateTier {
  max_miles: number | null;
  rate: number;
}

export interface ProgramRates {
  program: string;
  tiers: RateTier[];
}

export interface RatesPayload {
  programs: ProgramRates[];
  international_fallback_rate: number;
  skiplagged_estimation_program: string;
}

export const settings = {
  getRates: (token: string) =>
    request<RatesPayload>("/chat/settings/rates", { token }),

  updateRates: (token: string, payload: RatesPayload) =>
    request<RatesPayload>("/chat/settings/rates", {
      method: "PUT", token, body: JSON.stringify(payload),
    }),
};

// ─── Diagnóstico: health-check de programas de milhas ───────────────
export type ProgramHealthStatus = "ok" | "empty" | "error" | "timeout";

export interface ProgramHealth {
  program: string;
  label: string;
  source_type: string;
  status: ProgramHealthStatus;
  offers_count: number;
  latency_ms: number;
  route: string;                 // "GRU→MIA"
  error_kind?: string | null;
  error_detail?: string | null;
  checked_at: string;
}

export interface MilesHealthcheck {
  results: ProgramHealth[];
  ran_at: string;
  total_ms: number;
  ok_count: number;
  empty_count: number;
  error_count: number;
}

export const diagnostics = {
  milesHealthcheck: (token: string, programs?: string[]) =>
    request<MilesHealthcheck>("/chat/diagnostics/miles-healthcheck", {
      method: "POST", token, body: JSON.stringify({ programs: programs ?? null }),
    }),
};


export const quotes = {
  approve: (token: string, threadId: string, offerId: string, clientName?: string) =>
    request<{ id: string; status: string }>("/chat/quotes/approve", {
      method: "POST", token,
      body: JSON.stringify({
        thread_id: threadId,
        offer_id: offerId,
        client_name: clientName?.trim() || undefined,
      }),
    }),

  list: (token: string) =>
    request<{ quotes: { id: string; status: string; created_at: string }[] }>(
      "/chat/quotes", { token },
    ),

  pdfUrl: (token: string, quoteId: string) => {
    // Caller deve incluir o token como query OU usar fetch direto;
    // browsers não passam Bearer header em <a download>. Em produção,
    // implementar download via fetch + blob URL (ver chat.tsx).
    return `${BASE}/chat/quotes/${quoteId}/pdf`;
  },

  downloadPdf: async (token: string, quoteId: string) => {
    const resp = await fetch(`${BASE}/chat/quotes/${quoteId}/pdf`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) throw new ApiError(resp.status, "Falha ao baixar PDF", null);
    return await resp.blob();
  },
};


// ─── Validação interna (sistema vs. manual) + bug reports ───────────
export interface QuoteValidation {
  id: string;
  thread_id: string;
  message_id?: string | null;
  offer_id?: string | null;
  kind: "validated" | "corrected";
  system_offer: Record<string, unknown>;
  found_airline?: string | null;
  found_program?: string | null;
  emission_method?: string | null;
  found_value_brl?: number | null;
  found_miles?: number | null;
  observations?: string | null;
  created_at: string;
}

export interface ValidationStats {
  total: number;
  validated_count: number;
  corrected_count: number;
  accuracy_pct: number;
  avg_delta_brl?: number | null;
  by_method: Record<string, number>;
  by_airline: Record<string, number>;
}

export interface BugReport {
  id: string;
  thread_id: string;
  description: string;
  context: Record<string, unknown>;
  status: string;
  created_at: string;
}

export interface CreateValidationPayload {
  thread_id: string;
  message_id?: string | null;
  offer_id?: string | null;
  kind: "validated" | "corrected";
  system_offer: Record<string, unknown>;
  found_airline?: string;
  found_program?: string;
  emission_method?: string;
  found_value_brl?: number;
  found_miles?: number;
  observations?: string;
}

export const validations = {
  create: (token: string, payload: CreateValidationPayload) =>
    request<QuoteValidation>("/chat/validations", {
      method: "POST", token, body: JSON.stringify(payload),
    }),
  byThread: (token: string, threadId: string) =>
    request<QuoteValidation[]>(`/chat/threads/${threadId}/validations`, { token }),
  list: (token: string, kind?: "validated" | "corrected") =>
    request<QuoteValidation[]>(
      `/chat/validations${kind ? `?kind=${kind}` : ""}`, { token }),
  stats: (token: string) =>
    request<ValidationStats>("/chat/validations/stats", { token }),
  downloadCsv: async (token: string) => {
    const resp = await fetch(`${BASE}/chat/validations/export`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) throw new ApiError(resp.status, "Falha ao exportar CSV", null);
    return await resp.blob();
  },
};

// ─── Ranking feedback ("cotação ideal") ─────────────────────────────
export interface RankingFeedback {
  id: string;
  thread_id: string;
  message_id?: string | null;
  ideal_offer_id: string;
  created_at: string;
}

export const ranking = {
  // Marca, entre as ofertas de um turno (message_id), qual é a IDEAL.
  markIdeal: (token: string, threadId: string, messageId: string, offerId: string) =>
    request<RankingFeedback>("/chat/ranking/ideal", {
      method: "POST", token,
      body: JSON.stringify({ thread_id: threadId, message_id: messageId, offer_id: offerId }),
    }),
  byThread: (token: string, threadId: string) =>
    request<RankingFeedback[]>(`/chat/threads/${threadId}/ranking`, { token }),
};

export const bugReports = {
  create: (token: string, threadId: string, description: string,
           context?: Record<string, unknown>) =>
    request<BugReport>("/chat/bug-reports", {
      method: "POST", token,
      body: JSON.stringify({ thread_id: threadId, description, context: context ?? {} }),
    }),
  list: (token: string) => request<BugReport[]>("/chat/bug-reports", { token }),
};
