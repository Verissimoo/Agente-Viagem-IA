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
    <main className="min-h-screen bg-gray-50 dark:bg-zinc-950">
      <div className="max-w-5xl mx-auto px-4 py-8">
        <div className="flex items-center justify-between mb-6">
          <Link href="/chat" className="inline-flex items-center gap-2 text-sm text-gray-600 dark:text-zinc-400 hover:text-brand-600">
            <ArrowLeft size={16} /> Voltar pro chat
          </Link>
        </div>
        {token ? (
          <MilesHealthPanel token={token} />
        ) : (
          <p className="text-sm text-gray-500 dark:text-zinc-400">Carregando…</p>
        )}
      </div>
    </main>
  );
}
