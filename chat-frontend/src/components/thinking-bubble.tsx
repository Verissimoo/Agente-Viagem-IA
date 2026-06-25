"use client";

import Image from "next/image";
import { Square } from "lucide-react";
import type { ReactNode } from "react";

interface ThinkingBubbleProps {
  text?: string;
  onCancel?: () => void;
  children?: ReactNode; // conteúdo extra dentro do balão (ex.: log de processamento)
}

export default function ThinkingBubble({ text = "Pensando", onCancel, children }: ThinkingBubbleProps) {
  return (
    <div className="w-full flex gap-3">
      <div className="shrink-0 pt-0.5">
        <div className="w-9 h-9 rounded-full bg-white dark:bg-zinc-100 flex items-center justify-center ring-1 ring-black/5 dark:ring-white/20 overflow-hidden p-0.5 animate-pulse">
          <Image
            src="/assistant-avatar.png" alt="Atendente PCD"
            width={36} height={36}
            className="w-full h-full object-contain"
          />
        </div>
      </div>

      {/* Balão único: status → log → interromper. Largura PADRÃO (flex-1) —
          mesma do balão de resposta, não muda de tamanho conforme o status. */}
      <div className="flex-1 min-w-0 bg-white text-gray-900 rounded-2xl rounded-tl-md ring-1 ring-black/5 shadow-sm dark:bg-zinc-900 dark:text-zinc-100 dark:ring-white/10 overflow-hidden">
        <div className="px-4 py-3 flex items-center gap-2 min-h-[44px]">
          <span className="text-sm text-gray-600 dark:text-zinc-300 transition-opacity">{text}</span>
          <span className="inline-flex gap-0.5 items-center">
            <span className="dot-1 inline-block w-1.5 h-1.5 rounded-full bg-brand-500" />
            <span className="dot-2 inline-block w-1.5 h-1.5 rounded-full bg-brand-500" />
            <span className="dot-3 inline-block w-1.5 h-1.5 rounded-full bg-brand-500" />
          </span>
          {onCancel && (
            <button
              onClick={onCancel}
              title="Interromper cotação"
              aria-label="Interromper cotação"
              className="ml-auto shrink-0 inline-flex items-center justify-center w-6 h-6 rounded-full text-zinc-400 hover:text-rose-600 hover:bg-rose-50 dark:text-zinc-500 dark:hover:text-rose-400 dark:hover:bg-rose-500/10 transition-colors"
            >
              <Square size={11} className="fill-current" />
            </button>
          )}
        </div>

        {children && (
          <div className="border-t border-zinc-100 dark:border-zinc-800/80 px-4 py-2.5 bg-zinc-50/60 dark:bg-zinc-950/30">
            {children}
          </div>
        )}
      </div>
    </div>
  );
}
