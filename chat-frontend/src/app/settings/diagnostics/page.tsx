"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { loadSession } from "@/lib/session";
import MilesHealthPanel from "@/components/miles-health-panel";

export default function DiagnosticsSettingsPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    const s = loadSession();
    if (!s) { router.replace("/login"); return; }
    setToken(s.access_token);
  }, [router]);

  return (
    <main className="min-h-screen bg-gray-50 dark:bg-[#0a0a0a]">
      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-8 anim-fade-in">
        <Link
          href="/chat"
          prefetch
          className="inline-flex items-center gap-1.5 text-sm text-gray-500 dark:text-zinc-400 hover:text-brand-600 dark:hover:text-brand-400 transition-colors mb-6"
        >
          <ArrowLeft size={16} /> Voltar pro chat
        </Link>
        {token ? (
          <MilesHealthPanel token={token} />
        ) : (
          <div className="space-y-5">
            <div className="h-7 w-56 rounded-lg bg-gray-200 dark:bg-zinc-800 animate-pulse" />
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="h-28 rounded-2xl bg-gray-100 dark:bg-zinc-900 ring-1 ring-black/5 dark:ring-white/5 animate-pulse" />
              ))}
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
