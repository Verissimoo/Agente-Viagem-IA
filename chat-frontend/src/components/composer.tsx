"use client";

import { Send } from "lucide-react";
import { FormEvent, KeyboardEvent, useState, useRef, useEffect } from "react";

interface ComposerProps {
  disabled?: boolean;
  onSend: (text: string) => Promise<void> | void;
  placeholder?: string;
}

export default function Composer({ disabled, onSend, placeholder }: ComposerProps) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Auto-grow do textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [text]);

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || disabled || busy) return;
    setBusy(true);
    try {
      setText("");
      await onSend(trimmed);
    } finally {
      setBusy(false);
    }
  }

  function handleKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="px-4 pb-6 pt-3 bg-gradient-to-t from-gray-50 via-gray-50 to-transparent dark:from-zinc-950 dark:via-zinc-950">
      <form onSubmit={submit} className="max-w-3xl mx-auto">
        <div className="relative flex items-end gap-2 bg-white dark:bg-zinc-900 rounded-3xl shadow-lg ring-1 ring-black/5 dark:ring-white/10 focus-within:ring-2 focus-within:ring-brand-500 dark:focus-within:ring-brand-600 transition-all px-2 py-2">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKey}
            rows={1}
            placeholder={placeholder || "Escreva uma mensagem… (Enter envia · Shift+Enter quebra linha)"}
            className="flex-1 resize-none bg-transparent text-gray-900 dark:text-zinc-100 placeholder:text-gray-400 dark:placeholder:text-zinc-500 px-3 py-2 text-[14px] focus:outline-none max-h-48"
            disabled={disabled}
          />
          <button
            type="submit"
            disabled={disabled || busy || !text.trim()}
            className={[
              "shrink-0 rounded-full p-2.5 m-0.5",
              "bg-gradient-to-br from-brand-500 to-brand-700",
              "text-white shadow-md",
              "hover:from-brand-600 hover:to-brand-700 hover:shadow-lg",
              "disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none",
              "transition-all active:scale-95",
            ].join(" ")}
            aria-label="Enviar"
          >
            <Send size={16} />
          </button>
        </div>
        <p className="text-[11px] text-gray-400 dark:text-zinc-500 text-center mt-2">
          Sou um atendente virtual — sempre confira os detalhes antes de fechar com o cliente.
        </p>
      </form>
    </div>
  );
}
