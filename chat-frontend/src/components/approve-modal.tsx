"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { Loader2, X, User as UserIcon } from "lucide-react";

interface ApproveModalProps {
  open: boolean;
  loading: boolean;
  onConfirm: (clientName: string) => Promise<void> | void;
  onCancel: () => void;
}

export default function ApproveModal({
  open, loading, onConfirm, onCancel,
}: ApproveModalProps) {
  const [name, setName] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Foco automático no input ao abrir
  useEffect(() => {
    if (open) {
      setName("");
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  // ESC fecha
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !loading) onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, loading, onCancel]);

  if (!open) return null;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (loading) return;
    onConfirm(name);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm anim-fade-in p-4"
      onClick={(e) => { if (e.target === e.currentTarget && !loading) onCancel(); }}
    >
      <div className="w-full max-w-md bg-white dark:bg-zinc-900 rounded-2xl shadow-2xl ring-1 ring-black/10 dark:ring-white/10 anim-fade-in-up overflow-hidden">
        {/* Header navy */}
        <div className="bg-[#0d1c3d] px-5 py-4 flex items-center justify-between">
          <div>
            <h2 className="text-white text-base font-bold">Aprovar cotação</h2>
            <p className="text-[12px] text-blue-200/80 mt-0.5">
              Personalize o PDF antes de gerar
            </p>
          </div>
          <button
            onClick={onCancel}
            disabled={loading}
            className="text-blue-200/80 hover:text-white p-1 rounded disabled:opacity-50"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <form onSubmit={submit} className="px-5 py-5 space-y-4">
          <div>
            <label className="block text-sm font-semibold text-gray-700 dark:text-zinc-200 mb-1.5">
              Nome do cliente <span className="text-gray-400 dark:text-zinc-500 font-normal">(opcional)</span>
            </label>
            <div className="relative">
              <UserIcon size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 dark:text-zinc-500" />
              <input
                ref={inputRef}
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Ex.: Daniela Silva"
                maxLength={120}
                disabled={loading}
                className="w-full pl-9 pr-3 py-2.5 rounded-md border border-gray-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-gray-900 dark:text-zinc-100 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              />
            </div>
            <p className="text-xs text-gray-500 dark:text-zinc-400 mt-1.5 leading-relaxed">
              Aparece personalizado no PDF: <span className="italic">"Proposta para {name.trim() || "[nome]"}"</span>.
              Deixe vazio se preferir genérico.
            </p>
          </div>

          {/* Ações */}
          <div className="flex justify-end gap-2 pt-2 border-t border-gray-100 dark:border-zinc-800">
            <button
              type="button"
              onClick={onCancel}
              disabled={loading}
              className="px-4 py-2 rounded-md text-sm font-medium text-gray-600 dark:text-zinc-300 hover:bg-gray-100 dark:hover:bg-zinc-800 disabled:opacity-50"
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-2 rounded-md text-sm font-semibold text-white bg-brand-600 hover:bg-brand-700 disabled:opacity-60 flex items-center gap-2"
            >
              {loading
                ? <><Loader2 size={14} className="animate-spin" /> Gerando PDF…</>
                : "Aprovar e baixar PDF"
              }
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
