"use client";

import Image from "next/image";

interface ThinkingBubbleProps {
  text?: string;
}

export default function ThinkingBubble({ text = "Pensando" }: ThinkingBubbleProps) {
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
      <div className="px-4 py-3 bg-white text-gray-900 rounded-2xl rounded-tl-md ring-1 ring-black/5 shadow-sm dark:bg-zinc-900 dark:text-zinc-100 dark:ring-white/10 flex items-center gap-2 min-h-[44px]">
        <span className="text-sm text-gray-600 dark:text-zinc-300 transition-opacity">{text}</span>
        <span className="inline-flex gap-0.5 items-center">
          <span className="dot-1 inline-block w-1.5 h-1.5 rounded-full bg-brand-500" />
          <span className="dot-2 inline-block w-1.5 h-1.5 rounded-full bg-brand-500" />
          <span className="dot-3 inline-block w-1.5 h-1.5 rounded-full bg-brand-500" />
        </span>
      </div>
    </div>
  );
}
