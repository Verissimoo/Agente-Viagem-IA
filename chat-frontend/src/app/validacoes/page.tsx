"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Download } from "lucide-react";

import { loadSession } from "@/lib/session";
import {
  bugReports, validations,
  type BugReport, type QuoteValidation, type ValidationStats,
} from "@/lib/api";

type KindFilter = "all" | "validated" | "corrected";

export default function ValidacoesPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [stats, setStats] = useState<ValidationStats | null>(null);
  const [items, setItems] = useState<QuoteValidation[]>([]);
  const [bugs, setBugs] = useState<BugReport[]>([]);
  const [filter, setFilter] = useState<KindFilter>("all");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const s = loadSession();
    if (!s) { router.replace("/login"); return; }
    setToken(s.access_token);
  }, [router]);

  const load = useCallback(async (tk: string, f: KindFilter) => {
    setLoading(true);
    try {
      const [st, list, bg] = await Promise.all([
        validations.stats(tk),
        validations.list(tk, f === "all" ? undefined : f),
        bugReports.list(tk).catch(() => []),
      ]);
      setStats(st); setItems(list); setBugs(bg);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { if (token) load(token, filter); }, [token, filter, load]);

  async function exportCsv() {
    if (!token) return;
    const blob = await validations.downloadCsv(token);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "validacoes.csv"; a.click();
    URL.revokeObjectURL(url);
  }

  const sysVal = (v: QuoteValidation) =>
    (v.system_offer?.["equivalent_brl"] as number) ?? (v.system_offer?.["price_brl"] as number) ?? null;
  const brl = (n: number | null | undefined) =>
    n == null ? "—" : `R$ ${Number(n).toLocaleString("pt-BR", { maximumFractionDigits: 0 })}`;

  return (
    <main className="min-h-screen bg-gray-50 dark:bg-zinc-950 text-gray-800 dark:text-zinc-100">
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="flex items-center justify-between mb-6">
          <Link href="/chat" className="inline-flex items-center gap-2 text-sm text-gray-600 dark:text-zinc-400 hover:text-brand-600">
            <ArrowLeft size={16} /> Voltar pro chat
          </Link>
          <button onClick={exportCsv}
            className="inline-flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-lg ring-1 ring-gray-300 dark:ring-zinc-700 hover:bg-gray-100 dark:hover:bg-zinc-800">
            <Download size={14} /> Exportar CSV
          </button>
        </div>

        <h1 className="text-xl font-bold mb-1">Validações da cotação</h1>
        <p className="text-sm text-gray-500 dark:text-zinc-400 mb-6">
          Acertividade do sistema vs. o que o vendedor achou manualmente.
        </p>

        {/* Resumo */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          {[
            ["Total avaliado", stats ? String(stats.total) : "—"],
            ["Acertividade", stats ? `${stats.accuracy_pct}%` : "—"],
            ["Correções", stats ? String(stats.corrected_count) : "—"],
            ["Δ médio (manual ganhou)", stats?.avg_delta_brl != null ? brl(stats.avg_delta_brl) : "—"],
          ].map(([label, val]) => (
            <div key={label} className="rounded-xl bg-white dark:bg-zinc-900 ring-1 ring-gray-200 dark:ring-zinc-800 px-4 py-3">
              <div className="text-[11px] uppercase tracking-wider text-gray-400">{label}</div>
              <div className="text-lg font-bold mt-0.5">{val}</div>
            </div>
          ))}
        </div>

        {/* Filtro */}
        <div className="flex gap-2 mb-3 text-xs">
          {(["all", "validated", "corrected"] as KindFilter[]).map((f) => (
            <button key={f} onClick={() => setFilter(f)}
              className={[
                "px-3 py-1 rounded-full ring-1 transition-colors",
                filter === f
                  ? "bg-brand-600 text-white ring-brand-600"
                  : "ring-gray-300 dark:ring-zinc-700 hover:bg-gray-100 dark:hover:bg-zinc-800",
              ].join(" ")}>
              {f === "all" ? "Todas" : f === "validated" ? "Validadas" : "Corrigidas"}
            </button>
          ))}
        </div>

        {/* Tabela */}
        <div className="overflow-x-auto rounded-xl ring-1 ring-gray-200 dark:ring-zinc-800 bg-white dark:bg-zinc-900">
          <table className="w-full text-xs">
            <thead className="bg-gray-50 dark:bg-zinc-800/50 text-gray-500 dark:text-zinc-400">
              <tr className="text-left">
                <th className="px-3 py-2">Data</th><th className="px-3 py-2">Rota</th>
                <th className="px-3 py-2">Sistema</th><th className="px-3 py-2">Manual</th>
                <th className="px-3 py-2">Δ R$</th><th className="px-3 py-2">Obs.</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-gray-400">Carregando…</td></tr>
              ) : items.length === 0 ? (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-gray-400">Nenhum registro ainda.</td></tr>
              ) : items.map((v) => {
                const sv = sysVal(v);
                const delta = v.kind === "corrected" && sv != null && v.found_value_brl != null
                  ? sv - v.found_value_brl : null;
                return (
                  <tr key={v.id} className="border-t border-gray-100 dark:border-zinc-800">
                    <td className="px-3 py-2 whitespace-nowrap">{new Date(v.created_at).toLocaleDateString("pt-BR")}</td>
                    <td className="px-3 py-2">{String(v.system_offer?.["route"] || "—")}</td>
                    <td className="px-3 py-2">
                      {String(v.system_offer?.["airline"] || "—")} · {brl(sv)}
                    </td>
                    <td className="px-3 py-2">
                      {v.kind === "validated"
                        ? <span className="text-emerald-600 dark:text-emerald-400">✓ validado</span>
                        : `${v.found_airline || "—"}${v.found_program ? ` (${v.found_program})` : ""} · ${v.emission_method || "—"} · ${brl(v.found_value_brl)}`}
                    </td>
                    <td className="px-3 py-2">{delta != null ? brl(delta) : "—"}</td>
                    <td className="px-3 py-2 max-w-[200px] truncate text-gray-500">{v.observations || ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Bugs reportados */}
        <h2 className="text-lg font-bold mt-8 mb-3">Bugs reportados</h2>
        <div className="rounded-xl ring-1 ring-gray-200 dark:ring-zinc-800 bg-white dark:bg-zinc-900 divide-y divide-gray-100 dark:divide-zinc-800">
          {bugs.length === 0 ? (
            <p className="px-4 py-6 text-sm text-center text-gray-400">Nenhum bug reportado.</p>
          ) : bugs.map((b) => (
            <div key={b.id} className="px-4 py-3 text-sm">
              <div className="flex items-center gap-2 text-[11px] text-gray-400 mb-0.5">
                <span>{new Date(b.created_at).toLocaleString("pt-BR")}</span>
                <span>· thread {b.thread_id.slice(0, 8)}</span>
                <span className="px-1.5 rounded bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300">{b.status}</span>
              </div>
              <div className="text-gray-700 dark:text-zinc-200">{b.description}</div>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
