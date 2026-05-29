"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { auth, ApiError } from "@/lib/api";
import { saveSession } from "@/lib/session";

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [storeName, setStoreName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const session = await auth.register({
        email: email.trim(),
        password,
        display_name: displayName.trim() || undefined,
        store_name: storeName.trim() || undefined,
      });
      saveSession(session);
      router.push("/chat");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erro ao criar conta");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center px-4 py-10 bg-gradient-to-br from-gray-50 via-gray-100 to-gray-50 dark:from-zinc-950 dark:via-black dark:to-zinc-950">
      <div className="w-full max-w-md anim-fade-in-up">
        <div className="bg-zinc-950 rounded-t-2xl px-6 py-6 flex items-center justify-center border-b-2 border-brand-600">
          <Image
            src="/logo-pcd-tight.png" alt="Passagens com Desconto"
            width={1516} height={144} priority
            className="w-full h-auto max-w-[320px]"
          />
        </div>
        <div className="bg-white dark:bg-zinc-900 rounded-b-2xl shadow-2xl ring-1 ring-black/5 dark:ring-white/10 p-8">
        <h1 className="text-xl font-bold text-center mb-1 text-gray-900 dark:text-zinc-100">Criar conta</h1>
        <p className="text-sm text-gray-500 dark:text-zinc-400 text-center mb-6">
          Cadastro de vendedor — gratuito, leva 30s.
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="E-mail" required>
            <input
              type="email" required value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="input" autoComplete="email"
            />
          </Field>
          <Field label="Senha (mín. 8 caracteres)" required>
            <input
              type="password" required minLength={8} value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input" autoComplete="new-password"
            />
          </Field>
          <Field label="Seu nome">
            <input
              type="text" value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="input"
            />
          </Field>
          <Field label="Loja/Agência">
            <input
              type="text" value={storeName}
              onChange={(e) => setStoreName(e.target.value)}
              className="input"
            />
          </Field>

          {error && (
            <div className="text-sm text-brand-600 bg-brand-50 border border-brand-100 dark:bg-brand-600/10 dark:border-brand-600/30 dark:text-brand-200 rounded-md px-3 py-2">
              {error}
            </div>
          )}

          <button
            type="submit" disabled={loading}
            className="w-full rounded-md bg-brand-600 hover:bg-brand-700 disabled:opacity-60 text-white font-medium py-2 text-sm transition-colors"
          >
            {loading ? "Criando…" : "Criar conta"}
          </button>
        </form>

        <p className="text-sm text-gray-600 dark:text-zinc-400 text-center mt-6">
          Já tem conta?{" "}
          <Link href="/login" className="text-brand-600 dark:text-brand-500 font-semibold hover:underline">
            Entrar
          </Link>
        </p>
        </div>

        <style jsx>{`
          .input {
            margin-top: 0.25rem; width: 100%;
            border: 1px solid #d1d5db; border-radius: 0.375rem;
            padding: 0.5rem 0.75rem; font-size: 0.875rem;
            background: transparent;
          }
          :global(html.dark) .input {
            border-color: #3f3f46;
            color: #fafafa;
            background: #27272a;
          }
          .input:focus { outline: none; box-shadow: 0 0 0 2px #dc2626; }
        `}</style>
      </div>
    </main>
  );
}

function Field({
  label, required, children,
}: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 dark:text-zinc-300">
        {label} {required && <span className="text-brand-500">*</span>}
      </label>
      {children}
    </div>
  );
}
