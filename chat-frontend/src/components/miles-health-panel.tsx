"use client";

import { useState } from "react";
import { Loader2, RefreshCw, PlayCircle } from "lucide-react";
import { ApiError, diagnostics, type MilesHealthcheck, type ProgramHealth, type ProgramHealthStatus } from "@/lib/api";

const STATUS: Record<ProgramHealthStatus, { dot: string; chip: string; label: string; short: string }> = {
  ok: {
    dot: "bg-emerald-500",
    chip: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/25",
    label: "Funcionando", short: "no ar",
  },
  empty: {
    dot: "bg-amber-500",
    chip: "bg-amber-50 text-amber-700 ring-amber-600/20 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/25",
    label: "Respondeu sem tarifa", short: "sem tarifa",
  },
  error: {
    dot: "bg-red-500",
    chip: "bg-red-50 text-red-700 ring-red-600/20 dark:bg-red-500/15 dark:text-red-300 dark:ring-red-500/25",
    label: "Com erro", short: "erro",
  },
  timeout: {
    dot: "bg-zinc-400",
    chip: "bg-zinc-100 text-zinc-600 ring-zinc-500/20 dark:bg-zinc-500/15 dark:text-zinc-300 dark:ring-zinc-500/25",
    label: "Tempo esgotado", short: "timeout",
  },
};

const PROVIDER_NAME: Record<string, string> = {
  LATAM: "BuscaMilhas", GOL: "BuscaMilhas", AZUL: "BuscaMilhas", TAP: "BuscaMilhas",
  IBERIA: "BuscaMilhas", AMERICAN: "BuscaMilhas", COPA: "BuscaMilhas", INTERLINE: "BuscaMilhas",
  ECONOMILHAS: "Economilhas", SEATS_AERO: "seats.aero", AWARDTOOL: "AwardTool",
  MCP_AWARD: "MCP Award", QATAR: "MCP Qatar", KAYAK: "Kayak", SKIPLAGGED: "Skiplagged",
};

const AIRLINE_COVERAGE: { airline: string; keys: string[] }[] = [
  { airline: "LATAM Pass",            keys: ["LATAM", "ECONOMILHAS"] },
  { airline: "Smiles (GOL)",          keys: ["GOL", "ECONOMILHAS", "AWARDTOOL"] },
  { airline: "TudoAzul (Azul)",       keys: ["AZUL", "ECONOMILHAS"] },
  { airline: "TAP Miles&Go",          keys: ["TAP", "AWARDTOOL"] },
  { airline: "Iberia Plus",           keys: ["IBERIA", "ECONOMILHAS", "AWARDTOOL", "SEATS_AERO"] },
  { airline: "AAdvantage (American)", keys: ["AMERICAN", "AWARDTOOL"] },
  { airline: "Copa ConnectMiles",     keys: ["COPA", "SEATS_AERO"] },
  { airline: "Qatar Privilege Club",  keys: ["QATAR", "AWARDTOOL", "SEATS_AERO"] },
  { airline: "Aeroplan (Air Canada)", keys: ["AWARDTOOL", "SEATS_AERO"] },
  { airline: "Flying Blue (AF/KLM)",  keys: ["AWARDTOOL", "SEATS_AERO"] },
  { airline: "LifeMiles (Avianca)",   keys: ["AWARDTOOL", "SEATS_AERO"] },
  { airline: "British Airways Avios", keys: ["ECONOMILHAS", "AWARDTOOL", "SEATS_AERO"] },
  { airline: "Finnair Plus",          keys: ["AWARDTOOL", "SEATS_AERO"] },
  { airline: "Alaska / Atmos",        keys: ["AWARDTOOL", "SEATS_AERO"] },
  { airline: "Emirates Skywards",     keys: ["AWARDTOOL"] },
  { airline: "Turkish Miles&Smiles",  keys: ["AWARDTOOL"] },
  { airline: "Virgin Atlantic",       keys: ["AWARDTOOL"] },
  { airline: "United MileagePlus",    keys: ["AWARDTOOL"] },
  { airline: "Delta SkyMiles",        keys: ["AWARDTOOL"] },
];

const EXTRA_KEYS = ["KAYAK", "SKIPLAGGED", "MCP_AWARD"];

function StatusChip({ status, offers }: { status: ProgramHealthStatus; offers: number }) {
  const s = STATUS[status];
  const text = status === "ok" && offers > 0 ? `${offers} oferta${offers === 1 ? "" : "s"}` : s.short;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${s.chip}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {text}
    </span>
  );
}

export default function MilesHealthPanel({ token }: { token: string }) {
  const [data, setData] = useState<MilesHealthcheck | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(programs?: string[]) {
    setLoading(true);
    setError(null);
    try {
      setData(await diagnostics.milesHealthcheck(token, programs));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falha ao testar os programas.");
    } finally {
      setLoading(false);
    }
  }

  const byKey: Record<string, ProgramHealth> = {};
  for (const r of data?.results ?? []) byKey[r.program] = r;
  const failed = (data?.results ?? []).filter((r) => r.status === "error" || r.status === "timeout").map((r) => r.program);
  const extras = EXTRA_KEYS.map((k) => byKey[k]).filter(Boolean) as ProgramHealth[];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-gray-900 dark:text-zinc-50">Status por companhia</h1>
        <p className="text-sm text-gray-500 dark:text-zinc-400 mt-1">
          Cada companhia mostra os provedores que a validam e se estão respondendo agora.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2.5">
        <button
          onClick={() => run()}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white bg-gradient-to-br from-brand-500 to-brand-700 hover:from-brand-600 hover:to-brand-700 shadow-sm shadow-brand-900/20 disabled:opacity-60 active:scale-[0.98] transition-all"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <PlayCircle size={15} />}
          {loading ? "Testando…" : data ? "Retestar tudo" : "Testar agora"}
        </button>
        {data && failed.length > 0 && (
          <button
            onClick={() => run(failed)}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-xl px-3.5 py-2.5 text-sm font-medium text-red-700 dark:text-red-300 ring-1 ring-red-200 dark:ring-red-500/30 hover:bg-red-50 dark:hover:bg-red-500/10 disabled:opacity-60 transition-colors"
          >
            <RefreshCw size={14} /> Só os que falharam ({failed.length})
          </button>
        )}
      </div>

      {loading && !data && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-28 rounded-2xl bg-gray-100 dark:bg-zinc-900 ring-1 ring-black/5 dark:ring-white/5 animate-pulse" />
          ))}
        </div>
      )}

      {error && (
        <div className="text-sm rounded-xl px-3.5 py-2.5 bg-red-50 dark:bg-red-500/10 ring-1 ring-red-200 dark:ring-red-500/25 text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {data && (
        <div className="space-y-5 anim-fade-in">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300 font-medium">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> {data.ok_count} no ar
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300 font-medium">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-500" /> {data.empty_count} sem tarifa
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 bg-red-50 text-red-700 dark:bg-red-500/15 dark:text-red-300 font-medium">
              <span className="h-1.5 w-1.5 rounded-full bg-red-500" /> {data.error_count} com erro
            </span>
            <span className="text-gray-400 dark:text-zinc-500 ml-auto">testado às {fmtTime(data.ran_at)}</span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 stagger">
            {AIRLINE_COVERAGE.map(({ airline, keys }) => (
              <AirlineCard key={airline} airline={airline} keys={keys} byKey={byKey} />
            ))}
          </div>

          {extras.length > 0 && (
            <div>
              <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400 dark:text-zinc-500 mb-2">Outras fontes</h2>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {extras.map((r) => (
                  <div key={r.program} className="rounded-2xl bg-white dark:bg-zinc-900 ring-1 ring-black/5 dark:ring-white/10 px-4 py-3 flex items-center justify-between gap-2">
                    <span className="font-medium text-sm text-gray-900 dark:text-zinc-100 truncate">{r.label}</span>
                    <StatusChip status={r.status} offers={r.offers_count} />
                  </div>
                ))}
              </div>
            </div>
          )}

          <p className="text-[11px] leading-relaxed text-gray-400 dark:text-zinc-500">
            O status indica se cada provedor está no ar agora (trecho-teste, hoje+30) — não é cotação real.
            Verde = funcionando · Âmbar = respondeu sem tarifa · Vermelho = com erro.
          </p>
        </div>
      )}
    </div>
  );
}

function AirlineCard({ airline, keys, byKey }: {
  airline: string; keys: string[]; byKey: Record<string, ProgramHealth>;
}) {
  const rows = keys.map((k) => ({ key: k, name: PROVIDER_NAME[k] ?? k, r: byKey[k] }));
  const headDot = rows.some((x) => x.r?.status === "ok") ? "bg-emerald-500"
    : rows.some((x) => x.r?.status === "empty") ? "bg-amber-500"
    : rows.some((x) => x.r?.status === "error") ? "bg-red-500" : "bg-zinc-400";

  return (
    <div className="rounded-2xl bg-white dark:bg-zinc-900 ring-1 ring-black/5 dark:ring-white/10 px-4 py-3.5 anim-fade-in-up hover:ring-brand-200 dark:hover:ring-brand-600/30 transition-all">
      <div className="flex items-center gap-2 pb-2.5 mb-2.5 border-b border-black/5 dark:border-white/5">
        <span className={`shrink-0 h-2 w-2 rounded-full ${headDot}`} />
        <span className="font-semibold text-[15px] text-gray-900 dark:text-zinc-50 truncate">{airline}</span>
        <span className="ml-auto text-[11px] text-gray-400 dark:text-zinc-500">{keys.length} prov.</span>
      </div>
      <div className="space-y-1.5">
        {rows.map(({ key, name, r }) => (
          <div key={key} className="flex items-center justify-between gap-2">
            <span className="text-[13px] text-gray-600 dark:text-zinc-300 truncate">{name}</span>
            {r ? <StatusChip status={r.status} offers={r.offers_count} />
               : <span className="text-[11px] text-gray-300 dark:text-zinc-600">—</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

function fmtTime(iso: string): string {
  try { return new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }); }
  catch { return "—"; }
}
