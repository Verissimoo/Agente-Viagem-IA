"use client";

import Image from "next/image";
import Link from "next/link";
import { FormEvent, useState } from "react";

import { auth, ApiError } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await auth.forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erro ao solicitar redefinição");
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
            Esqueceu a senha?
          </h1>
          <p className="text-sm text-gray-500 dark:text-zinc-400 text-center mb-6">
            Informe seu e-mail e enviaremos um link para criar uma nova senha.
          </p>

          {sent ? (
            <div className="space-y-4">
              <div className="text-sm text-green-700 bg-green-50 border border-green-100 dark:bg-green-600/10 dark:border-green-600/30 dark:text-green-200 rounded-md px-3 py-3">
                Se este e-mail tiver uma conta, enviamos um link para redefinir a
                senha. Verifique sua caixa de entrada (e o spam). O link expira em 1 hora.
              </div>
              <Link
                href="/login"
                className="block text-center w-full rounded-md bg-brand-600 hover:bg-brand-700 text-white font-medium py-2 text-sm transition-colors"
              >
                Voltar ao login
              </Link>
            </div>
          ) : (
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

              {error && (
                <div className="text-sm text-brand-600 bg-brand-50 border border-brand-100 dark:bg-brand-600/10 dark:border-brand-600/30 dark:text-brand-200 rounded-md px-3 py-2">
                  {error}
                </div>
              )}

              <button
                type="submit" disabled={loading}
                className="w-full rounded-md bg-brand-600 hover:bg-brand-700 disabled:opacity-60 text-white font-medium py-2 text-sm transition-colors"
              >
                {loading ? "Enviando…" : "Enviar link de redefinição"}
              </button>

              <p className="text-center">
                <Link href="/login" className="text-sm text-gray-500 dark:text-zinc-400 hover:text-brand-600 dark:hover:text-brand-500 hover:underline">
                  Voltar ao login
                </Link>
              </p>
            </form>
          )}
        </div>
      </div>
    </main>
  );
}
