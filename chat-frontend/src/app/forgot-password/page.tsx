"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { auth, ApiError } from "@/lib/api";
import { saveSession } from "@/lib/session";

export default function ForgotPasswordPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < 8) {
      setError("A senha precisa ter pelo menos 8 caracteres.");
      return;
    }
    if (password !== confirm) {
      setError("As senhas não conferem.");
      return;
    }
    setLoading(true);
    try {
      const session = await auth.resetPasswordSimple(email.trim(), password);
      saveSession(session);
      router.push("/chat");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erro ao redefinir a senha");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center px-4 bg-gradient-to-br from-gray-50 via-gray-100 to-gray-50 dark:from-zinc-950 dark:via-black dark:to-zinc-950">
      <div className="w-full max-w-md anim-fade-in-up">
        <div className="bg-zinc-950 rounded-t-2xl px-6 py-6 flex items-center justify-center border-b-2 border-brand-600">
          <Image
            src="/logo-pcd-tight.png" alt="Passagens com Desconto"
            width={1516} height={144} priority
            className="w-full h-auto max-w-[320px]"
          />
        </div>
        <div className="bg-white dark:bg-zinc-900 rounded-b-2xl shadow-2xl ring-1 ring-black/5 dark:ring-white/10 p-8">
          <h1 className="text-xl font-bold text-center mb-1 text-gray-900 dark:text-zinc-100">
            Redefinir senha
          </h1>
          <p className="text-sm text-gray-500 dark:text-zinc-400 text-center mb-6">
            Informe seu e-mail e escolha uma nova senha.
          </p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-zinc-300">E-mail</label>
              <input
                type="email" required value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-gray-900 dark:text-zinc-100 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
                autoComplete="email"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-zinc-300">Nova senha</label>
              <input
                type="password" required value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-gray-900 dark:text-zinc-100 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
                autoComplete="new-password"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-zinc-300">Confirme a nova senha</label>
              <input
                type="password" required value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-gray-900 dark:text-zinc-100 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
                autoComplete="new-password"
              />
            </div>

            {error && (
              <div className="text-sm text-brand-600 bg-brand-50 border border-brand-100 dark:bg-brand-600/10 dark:border-brand-600/30 dark:text-brand-200 rounded-md px-3 py-2">
                {error}
              </div>
            )}

            <button
              type="submit" disabled={loading}
              className="w-full rounded-md bg-brand-600 hover:bg-brand-700 disabled:opacity-60 text-white font-medium py-2 text-sm transition-colors"
            >
              {loading ? "Salvando…" : "Salvar nova senha"}
            </button>

            <p className="text-center">
              <Link href="/login" className="text-sm text-gray-500 dark:text-zinc-400 hover:text-brand-600 dark:hover:text-brand-500 hover:underline">
                Voltar ao login
              </Link>
            </p>
          </form>
        </div>
      </div>
    </main>
  );
}
