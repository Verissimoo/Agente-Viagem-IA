"use client";

import { useState } from "react";
import { Loader2, RefreshCw, PlayCircle } from "lucide-react";
import { ApiError, diagnostics, type MilesHealthcheck, type ProgramHealth, type ProgramHealthStatus } from "@/lib/api";

const STATUS: Record<ProgramHealthStatus, { dot: string; label: string; emoji: string }> = {
  ok:      { dot: "bg-emerald-500", label: "Funcionando",            emoji: "✅" },
  empty:   { dot: "bg-amber-500",   label: "Respondeu sem tarifa",   emoji: "⚠️" },
  error:   { dot: "bg-red-500",     label: "Com erro",               emoji: "❌" },
  timeout: { dot: "bg-zinc-400",    label: "Tempo esgotado",         emoji: "⏱️" },
};

// Nome do PROVIDER (fonte) por chave do health-check — o que aparece embaixo da cia.
const PROVIDER_NAME: Record<string, string> = {
  LATAM: "BuscaMilhas", GOL: "BuscaMilhas", AZUL: "BuscaMilhas", TAP: "BuscaMilhas",
  IBERIA: "BuscaMilhas", AMERICAN: "BuscaMilhas", COPA: "BuscaMilhas", INTERLINE: "BuscaMilhas",
  ECONOMILHAS: "Economilhas", SEATS_AERO: "seats.aero", AWARDTOOL: "AwardTool",
  MCP_AWARD: "MCP Award", QATAR: "MCP Qatar", KAYAK: "Kayak", SKIPLAGGED: "Skiplagged",
};

// Cobertura: companhia → providers (chaves do health-check) que a validam.
// Conforme entram providers, é só somar a chave aqui.
const AIRLINE_COVERAGE: { airline: string; keys: string[] }[] = [
  { airline: "LATAM Pass",            keys: ["LATAM", "ECONOMILHAS"] },
  { airline: "Smiles (GOL)",          keys: ["GOL", "ECONOMILHAS", "AWARDTOOL"] },
  { airline: "TudoAzul (Azul)",       keys: ["AZUL", "ECONOMILHAS"] },
  { airline: "TAP Miles&Go",          keys: ["TAP", "AWARDTOOL"] },
  { airline: "Iberia Plus",           keys: ["IBERIA", "ECONOMILHAS", "AWARDTOOL", "SEATS_AERO"] },
  { airline: "AAdvantage (American)", keys: ["AMERICAN", "AWARDTOOL"] },
  { airline: "Copa ConnectMiles",     keys: ["COPA", "AWARDTOOL", "SEATS_AERO"] },
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

// Fontes que não são uma cia específica (mostradas à parte).
const EXTRA_KEYS = ["KAYAK", "SKIPLAGGED", "MCP_AWARD"];

function fmtLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}
function fmtTime(iso: string): string {
  try { return new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }); }
  catch { return "—"; }
}

export default function MilesHealthPanel({ token }: { token: string }) {
  const [data, setData] = useState<MilesHealthcheck | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(programs?: string[]) {
    setLoading(true);
    setError(null);
    try {
      const res = await diagnostics.milesHealthcheck(token, programs);
      setData(res);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falha ao testar os programas.");
    } finally {
      setLoading(false);
    }
  }

  const byKey: Record<string, ProgramHealth> = {};
  for (const r of data?.results ?? []) byKey[r.program] = r;

  const failed = (data?.results ?? [])
    .filter((r) => r.status === "error" || r.status === "timeout")
    .map((r) => r.program);

  const extras = EXTRA_KEYS.map((k) => byKey[k]).filter(Boolean) as ProgramHealth[];

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-gray-900 dark:text-zinc-100">
          Status por companhia
        </h2>
        <p className="text-sm text-gray-500 dark:text-zinc-400 mt-0.5">
          Cada companhia mostra os provedores que a validam e se estão respondendo agora.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => run()}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-white bg-gradient-to-br from-brand-500 to-brand-700 hover:from-brand-600 hover:to-brand-700 disabled:opacity-60 active:scale-[0.98] transition-all"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <PlayCircle size={15} />}
          {loading ? "Testando…" : data ? "Retestar tudo" : "Testar agora"}
        </button>
        {data && failed.length > 0 && (
          <button
            onClick={() => run(failed)}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-xl px-3.5 py-2 text-sm font-medium text-red-700 dark:text-red-300 border border-red-200 dark:border-red-500/30 hover:bg-red-50 dark:hover:bg-red-500/10 disabled:opacity-60"
          >
            <RefreshCw size={14} /> Retestar só os que falharam ({failed.length})
          </button>
        )}
      </div>

      {loading && !data && (
        <p className="text-sm text-gray-500 dark:text-zinc-400 flex items-center gap-2">
          <Loader2 size={14} className="animate-spin" /> Testando provedores… (AwardTool é por navegador, pode levar ~60-90s)
        </p>
      )}

      {error && (
        <div className="text-sm rounded-lg px-3 py-2 bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/30 text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {data && (
        <>
          <div className="text-sm text-gray-600 dark:text-zinc-400 flex flex-wrap gap-x-4 gap-y-1">
            <span>✅ <strong>{data.ok_count}</strong> ok</span>
            <span>⚠️ <strong>{data.empty_count}</strong> sem tarifa</span>
            <span>❌ <strong>{data.error_count}</strong> com erro</span>
            <span className="text-gray-400 dark:text-zinc-500">testado às {fmtTime(data.ran_at)}</span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {AIRLINE_COVERAGE.map(({ airline, keys }) => (
              <AirlineCard key={airline} airline={airline} keys={keys} byKey={byKey} />
            ))}
          </div>

          {extras.length > 0 && (
            <div className="pt-2">
              <h3 className="text-sm font-semibold text-gray-700 dark:text-zinc-300 mb-1.5">Outras fontes</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {extras.map((r) => (
                  <div key={r.program} className="rounded-xl border border-gray-200 dark:border-zinc-700/70 bg-white dark:bg-zinc-900/60 px-3.5 py-2.5 flex items-center justify-between">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className={`shrink-0 h-2.5 w-2.5 rounded-full ${STATUS[r.status].dot}`} title={STATUS[r.status].label} />
                      <span className="font-medium text-gray-900 dark:text-zinc-100 truncate">{r.label}</span>
                    </div>
                    <span className="shrink-0 text-xs text-gray-500 dark:text-zinc-400">
                      {STATUS[r.status].emoji} · {fmtLatency(r.latency_ms)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <p className="text-[11px] text-gray-400 dark:text-zinc-500">
            ✅ Funcionando · ⚠️ Respondeu sem tarifa · ❌ Com erro · ⏱️ Tempo esgotado.
            O status de cada provedor indica se ele está no ar agora (trecho-teste, hoje+30) — não é cotação real.
          </p>
        </>
      )}
    </div>
  );
}

function AirlineCard({ airline, keys, byKey }: {
  airline: string;
  keys: string[];
  byKey: Record<string, ProgramHealth>;
}) {
  const rows = keys.map((k) => ({ key: k, name: PROVIDER_NAME[k] ?? k, r: byKey[k] }));
  // status da cia = melhor status entre os providers (verde se algum ok)
  const anyOk = rows.some((x) => x.r?.status === "ok");
  const headDot = anyOk ? "bg-emerald-500" : rows.some((x) => x.r?.status === "empty") ? "bg-amber-500" : "bg-zinc-400";

  return (
    <div className="rounded-xl border border-gray-200 dark:border-zinc-700/70 bg-white dark:bg-zinc-900/60 px-3.5 py-3">
      <div className="flex items-center gap-2 mb-2">
        <span className={`shrink-0 h-2.5 w-2.5 rounded-full ${headDot}`} />
        <span className="font-semibold text-gray-900 dark:text-zinc-100 truncate">{airline}</span>
      </div>
      <div className="space-y-1">
        {rows.map(({ key, name, r }) => {
          const s = r ? STATUS[r.status] : null;
          return (
            <div key={key} className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-1.5 min-w-0">
                <span className={`shrink-0 h-1.5 w-1.5 rounded-full ${s ? s.dot : "bg-zinc-300 dark:bg-zinc-600"}`} />
                <span className="text-gray-700 dark:text-zinc-300 truncate">{name}</span>
              </div>
              <span className="shrink-0 text-gray-400 dark:text-zinc-500">
                {s ? `${s.emoji} ${r!.offers_count > 0 ? `${r!.offers_count} of.` : s.label}` : "—"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
