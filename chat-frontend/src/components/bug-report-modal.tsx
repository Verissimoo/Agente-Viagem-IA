"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { Loader2, X, Bug } from "lucide-react";

interface BugReportModalProps {
  open: boolean;
  loading: boolean;
  onConfirm: (description: string) => Promise<void> | void;
  onCancel: () => void;
}

export default function BugReportModal({
  open, loading, onConfirm, onCancel,
}: BugReportModalProps) {
  const [desc, setDesc] = useState("");
  const ref = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (open) { setDesc(""); setTimeout(() => ref.current?.focus(), 50); }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !loading) onCancel(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, loading, onCancel]);

  if (!open) return null;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (loading || !desc.trim()) return;
    onConfirm(desc.trim());
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm anim-fade-in p-4"
      onClick={(e) => { if (e.target === e.currentTarget && !loading) onCancel(); }}
    >
      <div className="w-full max-w-md bg-white dark:bg-zinc-900 rounded-2xl shadow-2xl ring-1 ring-black/10 dark:ring-white/10 anim-fade-in-up overflow-hidden">
        <div className="bg-[#0d1c3d] px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Bug size={18} className="text-amber-300" />
            <div>
              <h2 className="text-white text-base font-bold">Reportar bug</h2>
              <p className="text-[12px] text-blue-200/80 mt-0.5">Conta o que deu errado nesta conversa</p>
            </div>
          </div>
          <button onClick={onCancel} disabled={loading}
            className="text-blue-200/80 hover:text-white p-1 rounded disabled:opacity-50">
            <X size={18} />
          </button>
        </div>

        <form onSubmit={submit} className="px-5 py-5 space-y-4">
          <textarea
            ref={ref}
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="O que deu errado? (ex.: trouxe voo com troca de aeroporto, valor errado, não entendeu a rota…)"
            maxLength={2000}
            rows={5}
            disabled={loading}
            className="w-full px-3 py-2.5 rounded-md border border-gray-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-gray-900 dark:text-zinc-100 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 resize-none"
          />
          <div className="flex justify-end gap-2 pt-2 border-t border-gray-100 dark:border-zinc-800">
            <button type="button" onClick={onCancel} disabled={loading}
              className="px-4 py-2 rounded-md text-sm font-medium text-gray-600 dark:text-zinc-300 hover:bg-gray-100 dark:hover:bg-zinc-800 disabled:opacity-50">
              Cancelar
            </button>
            <button type="submit" disabled={loading || !desc.trim()}
              className="px-4 py-2 rounded-md text-sm font-semibold text-white bg-amber-600 hover:bg-amber-700 disabled:opacity-60 flex items-center gap-2">
              {loading ? <><Loader2 size={14} className="animate-spin" /> Enviando…</> : "Enviar"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
