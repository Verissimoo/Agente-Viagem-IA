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

  const failed = (data?.results ?? [])
    .filter((r) => r.status === "error" || r.status === "timeout")
    .map((r) => r.program);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-gray-900 dark:text-zinc-100">
          Status dos programas de milhas
        </h2>
        <p className="text-sm text-gray-500 dark:text-zinc-400 mt-0.5">
          Dispara uma busca real em trechos-teste pra ver quais programas estão respondendo agora.
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
          <Loader2 size={14} className="animate-spin" /> Testando programas… (pode levar até ~35s)
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
            {data.results.map((r) => (
              <HealthCard key={r.program} r={r} />
            ))}
          </div>

          <p className="text-[11px] text-gray-400 dark:text-zinc-500">
            ✅ Funcionando · ⚠️ Respondeu sem tarifa · ❌ Com erro · ⏱️ Tempo esgotado.
            Trecho-teste e data (hoje+30) são só pra checar a fonte — não é cotação real.
          </p>
        </>
      )}
    </div>
  );
}

function HealthCard({ r }: { r: ProgramHealth }) {
  const s = STATUS[r.status];
  return (
    <div className="rounded-xl border border-gray-200 dark:border-zinc-700/70 bg-white dark:bg-zinc-900/60 px-3.5 py-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`shrink-0 h-2.5 w-2.5 rounded-full ${s.dot}`} title={s.label} />
          <span className="font-medium text-gray-900 dark:text-zinc-100 truncate">{r.label}</span>
        </div>
        <span className="shrink-0 text-xs text-gray-400 dark:text-zinc-500">{r.route}</span>
      </div>
      <div className="mt-1.5 flex items-center justify-between text-xs text-gray-500 dark:text-zinc-400">
        <span>{s.emoji} {s.label}</span>
        <span>{fmtLatency(r.latency_ms)} · {r.offers_count} oferta{r.offers_count === 1 ? "" : "s"}</span>
      </div>
      {(r.status === "error" || r.status === "timeout") && (r.error_kind || r.error_detail) && (
        <div className="mt-1.5 text-[11px] text-red-600 dark:text-red-400 break-words">
          {r.error_kind ? <strong>{r.error_kind}:</strong> : null} {r.error_detail}
        </div>
      )}
    </div>
  );
}
