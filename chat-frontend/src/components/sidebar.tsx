"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Plus, MessageSquare, LogOut, Moon, Sun, ChevronUp, Trash2, Loader2, Settings, Activity, ClipboardCheck,
} from "lucide-react";
import type { Thread, Session } from "@/lib/api";
import { useTheme } from "@/lib/theme";
import Avatar from "@/components/avatar";
import { ThreadSkeleton } from "@/components/skeleton";

interface SidebarProps {
  session: Session;
  threads: Thread[];
  threadsLoading: boolean;
  activeThreadId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => Promise<void> | void;
  onLogout: () => void;
}

export default function Sidebar({
  session, threads, threadsLoading, activeThreadId,
  onNew, onSelect, onDelete, onLogout,
}: SidebarProps) {
  const [theme, toggleTheme] = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const accountRef = useRef<HTMLDivElement | null>(null);

  async function handleDelete(id: string) {
    setDeletingId(id);
    try {
      await onDelete(id);
    } finally {
      setDeletingId(null);
      setConfirmDeleteId(null);
    }
  }

  // Fecha o dropdown se clicar fora dele OU apertar ESC.
  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (accountRef.current && !accountRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  return (
    // Sidebar sempre escura (padrão ChatGPT) — logo branca + texto branco ficam visíveis.
    <aside className="w-72 bg-zinc-950 text-zinc-200 border-r border-zinc-800/80 flex flex-col h-full relative">
      {/* Brand area — logo cortada (sem padding transparente) full-width */}
      <div className="px-4 pt-6 pb-5 flex items-center justify-center">
        <Image
          src="/logo-pcd-tight.png" alt="Passagens com Desconto"
          width={1516} height={144} priority
          className="w-full h-auto max-w-[230px]"
        />
      </div>

      {/* Botão Nova cotação — gradient + sombra */}
      <div className="px-3 pt-1 pb-3">
        <button
          onClick={onNew}
          className={[
            "group w-full flex items-center justify-center gap-2",
            "rounded-xl px-3.5 py-2.5 text-sm font-semibold text-white",
            "bg-gradient-to-br from-brand-500 to-brand-700",
            "shadow-[0_4px_14px_-4px_rgba(220,38,38,0.6)]",
            "hover:from-brand-600 hover:to-brand-700 hover:shadow-[0_6px_18px_-4px_rgba(220,38,38,0.7)]",
            "active:scale-[0.98] transition-all",
          ].join(" ")}
        >
          <Plus size={15} className="group-hover:rotate-90 transition-transform" />
          Nova cotação
        </button>
      </div>

      {/* Lista de threads */}
      <nav className="flex-1 overflow-y-auto px-2 pb-2 custom-scroll">
        <div className="text-[10px] uppercase tracking-wider text-zinc-500 px-3 mt-2 mb-1.5 font-semibold">
          Conversas
        </div>

        {threadsLoading && (
          <>
            <ThreadSkeleton />
            <ThreadSkeleton />
            <ThreadSkeleton />
          </>
        )}

        {!threadsLoading && threads.length === 0 && (
          <p className="text-xs text-zinc-500 px-3 py-3">
            Nenhuma conversa ainda. Clique em <strong className="text-zinc-300">Nova cotação</strong> pra começar.
          </p>
        )}

        {!threadsLoading && threads.map((t) => {
          const isActive = activeThreadId === t.id;
          const isConfirming = confirmDeleteId === t.id;
          const isDeleting = deletingId === t.id;
          return (
            <div
              key={t.id}
              className={[
                "group relative mb-0.5 rounded-lg transition-colors",
                isActive ? "bg-zinc-800" : "hover:bg-zinc-900/80",
                isDeleting ? "opacity-50 pointer-events-none" : "",
              ].join(" ")}
            >
              <button
                onClick={() => onSelect(t.id)}
                className={[
                  "w-full text-left pl-3 pr-9 py-2 text-[13px]",
                  "flex items-start gap-2",
                  isActive ? "text-white" : "text-zinc-300 group-hover:text-white",
                ].join(" ")}
              >
                <MessageSquare size={13} className={[
                  "mt-0.5 shrink-0",
                  isActive ? "text-brand-500" : "text-zinc-500",
                ].join(" ")} />
                <span className="line-clamp-2 break-words leading-snug">{t.title}</span>
              </button>

              {/* Trash button — aparece no hover ou quando ativa */}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setConfirmDeleteId(isConfirming ? null : t.id);
                }}
                aria-label="Excluir conversa"
                title="Excluir conversa"
                className={[
                  "absolute right-1.5 top-1/2 -translate-y-1/2 p-1.5 rounded-md",
                  "text-zinc-500 hover:text-brand-400 hover:bg-zinc-800",
                  "opacity-0 group-hover:opacity-100 focus:opacity-100",
                  isActive ? "opacity-100" : "",
                  isConfirming ? "opacity-100 text-brand-400" : "",
                ].join(" ")}
              >
                {isDeleting
                  ? <Loader2 size={13} className="animate-spin" />
                  : <Trash2 size={13} />
                }
              </button>

              {/* Pop-up de confirmação */}
              {isConfirming && (
                <div className="absolute right-1.5 top-full mt-1 z-20 w-52 rounded-xl border border-zinc-800 bg-zinc-900 shadow-xl p-3 anim-fade-in">
                  <p className="text-xs text-zinc-300 mb-2.5 leading-snug">
                    Excluir essa conversa? Não dá pra desfazer.
                  </p>
                  <div className="flex justify-end gap-2">
                    <button
                      onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(null); }}
                      className="text-xs text-zinc-400 hover:text-zinc-200 px-2 py-1 rounded"
                    >
                      Cancelar
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDelete(t.id); }}
                      className="text-xs font-semibold text-white bg-brand-600 hover:bg-brand-700 px-3 py-1 rounded"
                    >
                      Excluir
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </nav>

      {/* Footer com avatar e menu de conta */}
      <div ref={accountRef} className="border-t border-zinc-800/80 px-2 py-2 relative">
        {menuOpen && (
          <div
            className="absolute left-2 right-2 bottom-[calc(100%-2px)] mb-1 rounded-xl border border-zinc-800 bg-zinc-900 shadow-xl overflow-hidden anim-fade-in-up"
            onClick={() => setMenuOpen(false)}
          >
            <button
              onClick={(e) => { e.stopPropagation(); toggleTheme(); }}
              className="w-full px-3 py-2.5 text-sm text-left text-zinc-200 hover:bg-zinc-800/80 flex items-center gap-2"
            >
              {theme === "dark"
                ? <><Sun size={14} className="text-zinc-400" /> Modo claro</>
                : <><Moon size={14} className="text-zinc-400" /> Modo escuro</>
              }
            </button>
            <div className="h-px bg-zinc-800" />
            <Link
              href="/settings/rates"
              className="w-full px-3 py-2.5 text-sm text-left text-zinc-200 hover:bg-zinc-800/80 flex items-center gap-2"
              onClick={() => setMenuOpen(false)}
            >
              <Settings size={14} className="text-zinc-400" /> Tabela de milhas
            </Link>
            <div className="h-px bg-zinc-800" />
            <Link
              href="/settings/diagnostics"
              prefetch
              className="w-full px-3 py-2.5 text-sm text-left text-zinc-200 hover:bg-zinc-800/80 flex items-center gap-2"
              onClick={() => setMenuOpen(false)}
            >
              <Activity size={14} className="text-zinc-400" /> Status das companhias
            </Link>
            <div className="h-px bg-zinc-800" />
            <Link
              href="/validacoes"
              className="w-full px-3 py-2.5 text-sm text-left text-zinc-200 hover:bg-zinc-800/80 flex items-center gap-2"
              onClick={() => setMenuOpen(false)}
            >
              <ClipboardCheck size={14} className="text-zinc-400" /> Validações
            </Link>
            <div className="h-px bg-zinc-800" />
            <button
              onClick={onLogout}
              className="w-full px-3 py-2.5 text-sm text-left text-zinc-200 hover:bg-zinc-800/80 flex items-center gap-2"
            >
              <LogOut size={14} className="text-zinc-400" /> Sair
            </button>
          </div>
        )}

        <button
          onClick={() => setMenuOpen((v) => !v)}
          className="w-full flex items-center gap-3 px-2 py-2 rounded-xl hover:bg-zinc-900/80 transition-colors"
        >
          <Avatar name={session.display_name || session.email} />
          <div className="flex-1 text-left min-w-0">
            <div className="text-sm font-medium text-zinc-100 truncate leading-tight">
              {session.display_name || session.email.split("@")[0]}
            </div>
            {session.store_name && (
              <div className="text-[11px] text-zinc-500 truncate leading-tight">
                {session.store_name}
              </div>
            )}
          </div>
          <ChevronUp
            size={14}
            className={[
              "text-zinc-500 transition-transform",
              menuOpen ? "rotate-0" : "rotate-180",
            ].join(" ")}
          />
        </button>
      </div>

      <style jsx>{`
        .custom-scroll::-webkit-scrollbar { width: 6px; }
        .custom-scroll::-webkit-scrollbar-track { background: transparent; }
        .custom-scroll::-webkit-scrollbar-thumb { background: #3f3f46; border-radius: 999px; }
        .custom-scroll::-webkit-scrollbar-thumb:hover { background: #52525b; }
      `}</style>
    </aside>
  );
}
