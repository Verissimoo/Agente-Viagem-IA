"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import ApproveModal from "@/components/approve-modal";
import Composer from "@/components/composer";
import MessageBubble from "@/components/message-bubble";
import OfferCard from "@/components/offer-card";
import Sidebar from "@/components/sidebar";
import ThinkingBubble from "@/components/thinking-bubble";
import { MessageSkeleton } from "@/components/skeleton";
import {
  ApiError,
  quotes,
  threads,
  type Message,
  type Offer,
  type Session,
  type Thread,
} from "@/lib/api";
import { clearSession, loadSession } from "@/lib/session";

export default function ChatPage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [threadList, setThreadList] = useState<Thread[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [statusText, setStatusText] = useState<string>("Processando");
  const [approving, setApproving] = useState<string | null>(null);
  const [approvedOfferId, setApprovedOfferId] = useState<string | null>(null);
  const [pendingApproveOfferId, setPendingApproveOfferId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const s = loadSession();
    if (!s) { router.replace("/login"); return; }
    setSession(s);
    setThreadsLoading(true);
    threads.list(s.access_token).then(({ threads }) => {
      setThreadList(threads);
      setThreadsLoading(false);
      if (threads.length === 0) {
        createNewThread(s, "Primeira cotação");
      } else {
        selectThread(s, threads[0].id);
      }
    }).catch((err) => {
      setThreadsLoading(false);
      if (err instanceof ApiError && err.status === 401) {
        clearSession(); router.replace("/login");
      }
    });
  }, [router]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending, statusText]);

  async function createNewThread(s: Session, title?: string) {
    setMessagesLoading(true);
    setMessages([]);
    setApprovedOfferId(null);
    try {
      const t = await threads.create(s.access_token, title);
      setThreadList((prev) => [t, ...prev]);
      setActiveThreadId(t.id);
      // Backend persiste a mensagem de boas-vindas — buscamos pra mostrar.
      const { messages } = await threads.messages(s.access_token, t.id);
      setMessages(messages);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha criando conversa");
    } finally {
      setMessagesLoading(false);
    }
  }

  async function selectThread(s: Session, id: string) {
    setActiveThreadId(id);
    setApprovedOfferId(null);
    setMessages([]);
    setMessagesLoading(true);
    try {
      const { messages } = await threads.messages(s.access_token, id);
      setMessages(messages);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha carregando mensagens");
    } finally {
      setMessagesLoading(false);
    }
  }

  async function deleteThread(id: string) {
    if (!session) return;
    try {
      await threads.remove(session.access_token, id);
      setThreadList((prev) => prev.filter((t) => t.id !== id));
      // Se apagou a thread aberta, pula pra próxima ou cria nova
      if (activeThreadId === id) {
        const remaining = threadList.filter((t) => t.id !== id);
        if (remaining.length > 0) {
          await selectThread(session, remaining[0].id);
        } else {
          await createNewThread(session);
        }
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha excluindo conversa");
    }
  }

  async function handleSend(text: string) {
    if (!session || !activeThreadId) return;
    setSending(true);
    setStatusText("Processando");
    setError(null);

    // Otimismo: mostra a mensagem do usuário imediatamente
    const tempUserMsg: Message = {
      id: `tmp-${Date.now()}`, role: "user", content: text,
      metadata: {}, created_at: new Date().toISOString(),
    };
    setMessages((m) => [...m, tempUserMsg]);

    let userPersistedId: string | null = null;
    let assistantArrived = false;

    await threads.sendStream(session.access_token, activeThreadId, text, {
      onUserMessage: (m) => {
        userPersistedId = m.id;
        setMessages((prev) =>
          prev.map((x) => (x.id === tempUserMsg.id ? m : x)),
        );
      },
      onStatus: (label) => setStatusText(label),
      onAssistant: (m) => {
        assistantArrived = true;
        setMessages((prev) => [...prev, m]);
      },
      onError: (err) => {
        let msg: string;
        if (err instanceof ApiError) {
          msg = err.message;
        } else if (/NetworkError|Failed to fetch|network/i.test(err.message)) {
          msg = "Conexão com o servidor caiu durante o processamento. Tente novamente — se persistir, verifique se o backend ainda está rodando.";
        } else {
          msg = err.message || "Erro desconhecido";
        }
        setError(msg);
        // Reverte msg otimista se nada do servidor chegou
        if (!userPersistedId) {
          setMessages((prev) => prev.filter((x) => x.id !== tempUserMsg.id));
        }
      },
      onDone: (info) => {
        if (!assistantArrived && !info?.error) {
          setError("Sem resposta do assistente. Tente novamente.");
        }
        setSending(false);
        // Recarrega lista de threads pra pegar título atualizado pelo backend
        threads.list(session.access_token).then(({ threads: latest }) => {
          setThreadList(latest);
        }).catch(() => { /* silent */ });
      },
    });
  }

  // Clica em "Aprovar e baixar PDF" → abre modal pedindo nome do cliente.
  function handleApprove(offerId: string) {
    setPendingApproveOfferId(offerId);
  }

  // Confirma aprovação com nome do cliente (opcional).
  async function confirmApproval(clientName: string) {
    if (!session || !activeThreadId || !pendingApproveOfferId) return;
    const offerId = pendingApproveOfferId;
    setApproving(offerId);
    try {
      const result = await quotes.approve(
        session.access_token, activeThreadId, offerId, clientName,
      );
      setApprovedOfferId(offerId);
      const blob = await quotes.downloadPdf(session.access_token, result.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `cotacao-${result.id.slice(0, 8)}.pdf`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      setPendingApproveOfferId(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erro ao aprovar oferta");
    } finally {
      setApproving(null);
    }
  }

  function handleLogout() {
    clearSession();
    router.replace("/login");
  }

  // Helper: ofertas embutidas no metadata da mensagem do assistente.
  function offersOf(m: Message): Offer[] {
    if (m.role !== "assistant") return [];
    const offers = m.metadata?.offers;
    return Array.isArray(offers) ? (offers as Offer[]) : [];
  }

  // Última mensagem com ofertas — só nela mostramos botão de aprovar
  // (turnos antigos ficam read-only no histórico).
  const lastWithOffersId = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (offersOf(messages[i]).length > 0) return messages[i].id;
    }
    return null;
  })();

  if (!session) return null;

  const userName = session.display_name || session.email.split("@")[0];

  return (
    <div className="h-screen flex bg-gray-50 dark:bg-zinc-950">
      <Sidebar
        session={session}
        threads={threadList}
        threadsLoading={threadsLoading}
        activeThreadId={activeThreadId}
        onNew={() => createNewThread(session)}
        onSelect={(id) => selectThread(session, id)}
        onDelete={deleteThread}
        onLogout={handleLogout}
      />

      <main className="flex-1 flex flex-col h-full bg-gradient-to-b from-gray-50 to-white dark:from-zinc-950 dark:to-zinc-900/60">
        <div ref={scrollRef} className="flex-1 overflow-y-auto custom-scroll">
          {/* key={activeThreadId} re-monta o container ao trocar de thread,
              disparando a animação fade-in. Stagger nas mensagens via CSS. */}
          <div
            key={activeThreadId || "empty"}
            className="max-w-3xl mx-auto px-4 py-8 space-y-6 anim-fade-in"
          >
            {messagesLoading && messages.length === 0 && (
              <div className="space-y-6 stagger">
                <div className="anim-fade-in-up"><MessageSkeleton /></div>
                <div className="anim-fade-in-up"><MessageSkeleton /></div>
              </div>
            )}

            <div className="space-y-6 stagger">
            {messages.map((m) => {
              const offers = offersOf(m);
              const isLatest = m.id === lastWithOffersId;
              return (
                <div key={m.id} className="space-y-4 anim-fade-in-up">
                  <MessageBubble message={m} userName={userName} />
                  {offers.length > 0 && (
                    <div className={[
                      "ml-11 space-y-3",
                      isLatest ? "" : "opacity-60",  // turnos antigos mais discretos
                    ].join(" ")}>
                      <div className="flex items-center gap-2">
                        <h3 className="text-[10px] uppercase tracking-wider font-bold text-gray-500 dark:text-zinc-400">
                          {isLatest ? "Opções encontradas" : "Opções desse turno"}
                        </h3>
                        <span className="text-[10px] text-gray-400 dark:text-zinc-500">
                          · {offers.length} {offers.length === 1 ? "oferta" : "ofertas"}
                        </span>
                      </div>
                      {offers.map((offer, idx) => (
                        <OfferCard
                          key={offer.offer_id}
                          offer={offer}
                          approving={approving === offer.offer_id}
                          approvedOfferId={isLatest ? approvedOfferId : null}
                          onApprove={isLatest ? handleApprove : () => {}}
                          isBest={isLatest && idx === 0 && !approvedOfferId}
                          readonly={!isLatest}
                        />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
            </div>

            {sending && (
              <div className="anim-fade-in">
                <ThinkingBubble text={statusText} />
              </div>
            )}

            {error && (
              <div className="text-sm text-brand-700 dark:text-brand-200 bg-brand-50 dark:bg-brand-600/10 border border-brand-200 dark:border-brand-600/30 rounded-xl px-4 py-3">
                {error}
              </div>
            )}
          </div>
        </div>

        <Composer
          disabled={sending}
          onSend={handleSend}
          placeholder={
            sending ? "Aguardando resposta…" :
            "Pra onde seu cliente quer viajar?"
          }
        />
      </main>

      <ApproveModal
        open={!!pendingApproveOfferId}
        loading={approving === pendingApproveOfferId}
        onConfirm={confirmApproval}
        onCancel={() => {
          if (!approving) setPendingApproveOfferId(null);
        }}
      />

      <style jsx global>{`
        .custom-scroll::-webkit-scrollbar { width: 8px; }
        .custom-scroll::-webkit-scrollbar-track { background: transparent; }
        .custom-scroll::-webkit-scrollbar-thumb { background: rgba(120,120,120,0.2); border-radius: 999px; }
        .custom-scroll::-webkit-scrollbar-thumb:hover { background: rgba(120,120,120,0.4); }
      `}</style>
    </div>
  );
}
