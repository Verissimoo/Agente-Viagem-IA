"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Save, Plus, Trash2, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";

import { ApiError, settings, type RatesPayload, type ProgramRates, type RateTier } from "@/lib/api";
import { clearSession, loadSession } from "@/lib/session";

export default function RatesSettingsPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [payload, setPayload] = useState<RatesPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    const s = loadSession();
    if (!s) { router.replace("/login"); return; }
    setToken(s.access_token);
    settings.getRates(s.access_token).then((data) => {
      setPayload(data);
      setLoading(false);
    }).catch((err) => {
      if (err instanceof ApiError && err.status === 401) {
        clearSession(); router.replace("/login"); return;
      }
      setError(err instanceof ApiError ? err.message : "Falha ao carregar");
      setLoading(false);
    });
  }, [router]);

  function updateTier(progIdx: number, tierIdx: number, patch: Partial<RateTier>) {
    if (!payload) return;
    const programs = [...payload.programs];
    const tiers = [...programs[progIdx].tiers];
    tiers[tierIdx] = { ...tiers[tierIdx], ...patch };
    programs[progIdx] = { ...programs[progIdx], tiers };
    setPayload({ ...payload, programs });
  }

  function addTier(progIdx: number) {
    if (!payload) return;
    const programs = [...payload.programs];
    const tiers = [...programs[progIdx].tiers];
    // Insere ANTES do último (que é o sem-limite)
    const newTier: RateTier = { max_miles: 50000, rate: 0.025 };
    const insertAt = Math.max(0, tiers.length - 1);
    tiers.splice(insertAt, 0, newTier);
    programs[progIdx] = { ...programs[progIdx], tiers };
    setPayload({ ...payload, programs });
  }

  function removeTier(progIdx: number, tierIdx: number) {
    if (!payload) return;
    const programs = [...payload.programs];
    const tiers = programs[progIdx].tiers.filter((_, i) => i !== tierIdx);
    if (tiers.length === 0) return;   // mantém pelo menos 1
    programs[progIdx] = { ...programs[progIdx], tiers };
    setPayload({ ...payload, programs });
  }

  function addProgram() {
    if (!payload) return;
    const name = prompt("Nome do programa (ex: KLM, EMIRATES):");
    if (!name || name.trim().length < 2) return;
    setPayload({
      ...payload,
      programs: [
        ...payload.programs,
        { program: name.trim().toUpperCase(), tiers: [{ max_miles: null, rate: 0.05 }] },
      ],
    });
  }

  function removeProgram(progIdx: number) {
    if (!payload) return;
    const name = payload.programs[progIdx].program;
    if (name === "DEFAULT") {
      alert("DEFAULT não pode ser removido (é usado quando não há match).");
      return;
    }
    if (!confirm(`Remover programa "${name}"?`)) return;
    setPayload({
      ...payload,
      programs: payload.programs.filter((_, i) => i !== progIdx),
    });
  }

  async function save() {
    if (!token || !payload) return;
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const updated = await settings.updateRates(token, payload);
      setPayload(updated);
      setSuccess("Tabela salva. As próximas cotações já usam os novos valores.");
      setTimeout(() => setSuccess(null), 4500);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha ao salvar");
    } finally {
      setSaving(false);
    }
  }

  const programs = payload?.programs ?? [];

  return (
    <main className="min-h-screen bg-gray-50 dark:bg-[#0a0a0a]">
      <div className="max-w-5xl mx-auto px-4 py-8 anim-fade-in">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <Link href="/chat" className="inline-flex items-center gap-2 text-sm text-gray-600 dark:text-zinc-400 hover:text-brand-600">
            <ArrowLeft size={16} /> Voltar pro chat
          </Link>
          <button
            onClick={save}
            disabled={loading || saving || !payload}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white text-sm font-semibold shadow-md"
          >
            {saving ? <><Loader2 size={14} className="animate-spin" /> Salvando…</> : <><Save size={14} /> Salvar tudo</>}
          </button>
        </div>

        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-zinc-100">
            Tabela de milhas
          </h1>
          <p className="text-sm text-gray-500 dark:text-zinc-400 mt-1">
            Valores em BRL por milha (ex.: <code>0.025</code> = R$ 25,00 / mil).
            LATAM e TAP podem ter faixas por volume.
          </p>
        </div>

        {error && (
          <div className="mb-4 flex items-start gap-2 text-sm bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/30 rounded-lg px-3 py-2 text-red-800 dark:text-red-200">
            <AlertCircle size={16} className="mt-0.5 shrink-0" /> {error}
          </div>
        )}
        {success && (
          <div className="mb-4 flex items-start gap-2 text-sm bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/30 rounded-lg px-3 py-2 text-emerald-800 dark:text-emerald-200">
            <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> {success}
          </div>
        )}

        {loading && (
          <div className="space-y-4">
            <div className="h-28 rounded-xl bg-gray-100 dark:bg-zinc-900 ring-1 ring-black/5 dark:ring-white/5 animate-pulse" />
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-40 rounded-xl bg-gray-100 dark:bg-zinc-900 ring-1 ring-black/5 dark:ring-white/5 animate-pulse" />
            ))}
          </div>
        )}

        {/* Config global */}
        {payload && (
          <div className="mb-6 bg-white dark:bg-zinc-900 rounded-xl ring-1 ring-black/5 dark:ring-white/10 p-5">
            <h2 className="text-sm font-bold uppercase tracking-wider text-gray-700 dark:text-zinc-300 mb-3">
              Configuração geral
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <label className="block">
                <span className="text-xs font-semibold text-gray-600 dark:text-zinc-400 block mb-1">
                  Taxa internacional fallback (BRL/milha)
                </span>
                <input
                  type="number" step="0.001" min="0.001" max="1"
                  value={payload.international_fallback_rate}
                  onChange={(e) =>
                    setPayload({ ...payload, international_fallback_rate: parseFloat(e.target.value) || 0 })
                  }
                  className="w-full px-3 py-2 rounded-md border border-gray-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-gray-900 dark:text-zinc-100 text-sm"
                />
              </label>
              <label className="block">
                <span className="text-xs font-semibold text-gray-600 dark:text-zinc-400 block mb-1">
                  Programa de referência pra Skiplagged
                </span>
                <input
                  type="text"
                  value={payload.skiplagged_estimation_program}
                  onChange={(e) =>
                    setPayload({ ...payload, skiplagged_estimation_program: e.target.value.toUpperCase() })
                  }
                  className="w-full px-3 py-2 rounded-md border border-gray-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-gray-900 dark:text-zinc-100 text-sm"
                />
              </label>
            </div>
          </div>
        )}

        {/* Programas */}
        <div className="space-y-4">
          {programs.map((prog, pi) => (
            <div key={pi} className="bg-white dark:bg-zinc-900 rounded-xl ring-1 ring-black/5 dark:ring-white/10 p-5">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-lg font-bold text-brand-700 dark:text-brand-400">
                  {prog.program}
                </h3>
                <div className="flex gap-2">
                  <button
                    onClick={() => addTier(pi)}
                    className="text-xs flex items-center gap-1 px-2 py-1 rounded text-gray-700 dark:text-zinc-300 hover:bg-gray-100 dark:hover:bg-zinc-800"
                    title="Adicionar faixa"
                  >
                    <Plus size={12} /> Faixa
                  </button>
                  <button
                    onClick={() => removeProgram(pi)}
                    className="text-xs flex items-center gap-1 px-2 py-1 rounded text-red-700 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10"
                  >
                    <Trash2 size={12} /> Remover
                  </button>
                </div>
              </div>

              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[10px] uppercase tracking-wider text-gray-500 dark:text-zinc-500 text-left border-b border-gray-200 dark:border-zinc-800">
                    <th className="pb-2 font-semibold">Até X milhas (max_miles)</th>
                    <th className="pb-2 font-semibold">BRL por milha (rate)</th>
                    <th className="pb-2 font-semibold">≈ R$ por mil</th>
                    <th className="pb-2 w-10"></th>
                  </tr>
                </thead>
                <tbody>
                  {prog.tiers.map((tier, ti) => {
                    const isLast = ti === prog.tiers.length - 1;
                    return (
                      <tr key={ti} className="border-b border-gray-100 dark:border-zinc-800/50">
                        <td className="py-2 pr-3">
                          {isLast ? (
                            <span className="text-xs italic text-gray-500 dark:text-zinc-400">
                              sem limite (faixa topo)
                            </span>
                          ) : (
                            <input
                              type="number" min="1"
                              value={tier.max_miles ?? ""}
                              onChange={(e) => updateTier(pi, ti, { max_miles: parseInt(e.target.value || "0", 10) || null })}
                              className="w-32 px-2 py-1 rounded border border-gray-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-sm tabular-nums"
                            />
                          )}
                        </td>
                        <td className="py-2 pr-3">
                          <input
                            type="number" step="0.0001" min="0.0001" max="1"
                            value={tier.rate}
                            onChange={(e) => updateTier(pi, ti, { rate: parseFloat(e.target.value) || 0 })}
                            className="w-28 px-2 py-1 rounded border border-gray-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-sm tabular-nums"
                          />
                        </td>
                        <td className="py-2 pr-3 text-gray-600 dark:text-zinc-400 tabular-nums">
                          R$ {(tier.rate * 1000).toFixed(2)}
                        </td>
                        <td className="py-2">
                          {prog.tiers.length > 1 && (
                            <button
                              onClick={() => removeTier(pi, ti)}
                              className="p-1 rounded text-gray-400 hover:text-red-600 dark:hover:text-red-400"
                              title="Remover faixa"
                            >
                              <Trash2 size={13} />
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ))}
        </div>

        {payload && (
          <button
            onClick={addProgram}
            className="mt-4 w-full inline-flex items-center justify-center gap-2 py-3 rounded-xl border-2 border-dashed border-gray-300 dark:border-zinc-700 text-sm text-gray-600 dark:text-zinc-400 hover:border-brand-500 hover:text-brand-600 transition-colors"
          >
            <Plus size={14} /> Adicionar programa
          </button>
        )}
      </div>
    </main>
  );
}
