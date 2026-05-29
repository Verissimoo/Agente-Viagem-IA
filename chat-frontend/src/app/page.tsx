"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { loadSession } from "@/lib/session";

export default function HomePage() {
  const router = useRouter();
  useEffect(() => {
    const session = loadSession();
    router.replace(session ? "/chat" : "/login");
  }, [router]);
  return (
    <div className="min-h-screen flex items-center justify-center text-gray-500">
      Carregando…
    </div>
  );
}
