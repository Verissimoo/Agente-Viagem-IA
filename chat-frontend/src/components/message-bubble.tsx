"use client";

import Image from "next/image";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "@/lib/api";
import Avatar from "@/components/avatar";

interface MessageBubbleProps {
  message: Message;
  userName?: string;
}

export default function MessageBubble({ message, userName }: MessageBubbleProps) {
  const isUser = message.role === "user";

  return (
    <div className={["w-full flex gap-3", isUser ? "flex-row-reverse" : "flex-row"].join(" ")}>
      {/* Avatar lateral */}
      <div className="shrink-0 pt-0.5">
        {isUser ? (
          <Avatar name={userName || "Você"} size="sm" />
        ) : (
          <div className="w-9 h-9 rounded-full bg-white dark:bg-zinc-100 flex items-center justify-center ring-1 ring-black/5 dark:ring-white/20 overflow-hidden p-0.5">
            <Image
              src="/assistant-avatar.png" alt="Atendente PCD"
              width={36} height={36}
              className="w-full h-full object-contain"
            />
          </div>
        )}
      </div>

      {/* Bolha */}
      <div
        className={[
          "max-w-[78%] px-4 py-3 shadow-sm transition-colors",
          isUser
            ? "bg-gradient-to-br from-brand-500 to-brand-700 text-white rounded-2xl rounded-tr-md"
            : "bg-white text-gray-900 rounded-2xl rounded-tl-md ring-1 ring-black/5 dark:bg-zinc-900 dark:text-zinc-100 dark:ring-white/10",
        ].join(" ")}
      >
        <div className="chat-prose">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
