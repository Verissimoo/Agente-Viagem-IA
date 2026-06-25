"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Bug, ChevronRight } from "lucide-react";

import ApproveModal from "@/components/approve-modal";
import BugReportModal from "@/components/bug-report-modal";
import Composer from "@/components/composer";
import CorrectionPanel, { type CorrectionData } from "@/components/correction-panel";
import MessageBubble from "@/components/message-bubble";
import OfferCard from "@/components/offer-card";
import Sidebar from "@/components/sidebar";
import ThinkingBubble from "@/components/thinking-bubble";
import { MessageSkeleton } from "@/components/skeleton";
import {
  ApiError,
  bugReports,
  quotes,
  ranking,
  threads,
  validations,
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
  // Log detalhado do processamento (cada passo do backend: provedor/data).
  const [statusLog, setStatusLog] = useState<string[]>([]);
  const statusLogRef = useRef<HTMLDivElement | null>(null);
  // Espelho do statusLog pra anexar à mensagem quando ela chega (closure-safe).
  const accumLogRef = useRef<string[]>([]);
  const [approving, setApproving] = useState<string | null>(null);
  const [approvedOfferId, setApprovedOfferId] = useState<string | null>(null);
  const [pendingApproveOfferId, setPendingApproveOfferId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Validação interna (sistema vs. manual)
  const [validationsByOffer, setValidationsByOffer] = useState<Record<string, "validated" | "corrected">>({});
  // "Cotação ideal" por mensagem (messageId → offerId marcado). Rótulo de treino.
  const [idealByMessage, setIdealByMessage] = useState<Record<string, string>>({});
  const [correctionOfferId, setCorrectionOfferId] = useState<string | null>(null);
  const [savingValidation, setSavingValidation] = useState(false);
  // Reportar bug
  const [bugOpen, setBugOpen] = useState(false);
  const [bugLoading, setBugLoading] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Guard contra o double-invoke de effects do React StrictMode (dev): sem ele,
  // as duas execuções veem threads=[] (a 1ª thread ainda não persistiu) e cada
  // uma cria uma "Primeira cotação" → 2 chats vazios no 1º load.
  const bootstrapped = useRef(false);

  useEffect(() => {
    if (bootstrapped.current) return;
    bootstrapped.current = true;
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
    // Instantâneo: scroll suave anima a rolagem da lista inteira a cada troca de
    // chat/atualização → trava. Pula direto pro fim (snappy). Não dependemos de
    // statusText (o painel de processamento tem o próprio scroll).
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "auto" });
  }, [messages, sending]);

  // Mantém o painel de processamento rolado no passo mais recente.
  useEffect(() => {
    if (statusLogRef.current) {
      statusLogRef.current.scrollTop = statusLogRef.current.scrollHeight;
    }
  }, [statusLog]);

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
    setValidationsByOffer({});
    setIdealByMessage({});
    setCorrectionOfferId(null);
    setMessagesLoading(true);
    try {
      // Mensagens + validações + "cotação ideal" em paralelo (não bloqueiam a
      // renderização das mensagens).
      const [{ messages }, vals, ideals] = await Promise.all([
        threads.messages(s.access_token, id),
        validations.byThread(s.access_token, id).catch(() => []),
        ranking.byThread(s.access_token, id).catch(() => []),
      ]);
      setMessages(messages);
      const map: Record<string, "validated" | "corrected"> = {};
      for (const v of vals) if (v.offer_id) map[v.offer_id] = v.kind;
      setValidationsByOffer(map);
      const imap: Record<string, string> = {};
      for (const r of ideals) if (r.message_id) imap[r.message_id] = r.ideal_offer_id;
      setIdealByMessage(imap);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha carregando mensagens");
    } finally {
      setMessagesLoading(false);
    }
  }

  async function deleteThread(id: string) {
    if (!session) return;
    // OTIMISTA: tira da lista e troca de chat NA HORA; a rede roda em background.
    // Antes esperávamos o DELETE + o carregamento da próxima thread (2 round-trips)
    // antes de mexer na UI → exclusão travada.
    const prevList = threadList;
    const remaining = threadList.filter((t) => t.id !== id);
    setThreadList(remaining);
    if (activeThreadId === id) {
      if (remaining.length > 0) {
        selectThread(session, remaining[0].id);   // sem await — carrega em paralelo
      } else {
        createNewThread(session);
      }
    }
    try {
      await threads.remove(session.access_token, id);
    } catch (err) {
      // Falhou → restaura a lista (a thread volta).
      setThreadList(prevList);
      setError(err instanceof ApiError ? err.message : "Falha excluindo conversa");
    }
  }

  async function handleSend(text: string) {
    if (!session || !activeThreadId) return;
    setSending(true);
    setStatusText("Processando");
    setStatusLog([]);
    accumLogRef.current = [];
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
      onStatus: (label) => {
        setStatusText(label);
        // Acumula no painel de processamento (ignora repetição consecutiva).
        setStatusLog((log) => {
          const next = log[log.length - 1] === label ? log : [...log, label];
          accumLogRef.current = next;
          return next;
        });
      },
      onAssistant: (m) => {
        assistantArrived = true;
        // Anexa o andamento interno à mensagem pra o vendedor reabrir depois.
        const withLog = accumLogRef.current.length
          ? { ...m, metadata: { ...m.metadata, status_log: [...accumLogRef.current] } }
          : m;
        setMessages((prev) => [...prev, withLog]);
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

  // Interrompe a cotação em andamento (botão durante o processamento).
  async function handleCancel() {
    if (!session || !activeThreadId) return;
    setStatusText("Interrompendo…");
    try {
      await threads.cancel(session.access_token, activeThreadId);
    } catch {
      /* o stream encerra de qualquer forma; ignora erro do cancel */
    }
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

  // Helper: andamento interno da cotação anexado à mensagem (ver depois).
  function statusLogOf(m: Message): string[] {
    if (m.role !== "assistant") return [];
    const log = m.metadata?.status_log;
    return Array.isArray(log) ? (log as string[]) : [];
  }

  // Snapshot autossuficiente da oferta do SISTEMA (a tabela comparativa não
  // depende da thread existir).
  function snapshotOf(offer: Offer): Record<string, unknown> {
    const segs = offer.outbound?.segments || [];
    const route = segs.length
      ? [segs[0].origin, ...segs.map((s) => s.destination)].join("→")
      : "";
    return {
      offer_id: offer.offer_id, airline: offer.airline ?? null,
      category: offer.category ?? null, price_brl: offer.price_brl ?? null,
      miles: offer.miles ?? null, taxes_brl: offer.taxes_brl ?? null,
      equivalent_brl: offer.equivalent_brl ?? null, route,
    };
  }

  function msgIdOfOffer(offerId: string): string | undefined {
    const m = messages.find((mm) => offersOf(mm).some((o) => o.offer_id === offerId));
    return m?.id;
  }

  async function handleValidate(offer: Offer) {
    if (!session || !activeThreadId) return;
    const id = offer.offer_id;
    setValidationsByOffer((p) => ({ ...p, [id]: "validated" }));  // otimista
    try {
      await validations.create(session.access_token, {
        thread_id: activeThreadId, message_id: msgIdOfOffer(id), offer_id: id,
        kind: "validated", system_offer: snapshotOf(offer),
      });
    } catch (err) {
      setValidationsByOffer((p) => { const n = { ...p }; delete n[id]; return n; });
      setError(err instanceof ApiError ? err.message : "Falha ao validar");
    }
  }

  async function handleMarkIdeal(messageId: string, offer: Offer) {
    if (!session || !activeThreadId) return;
    const prev = idealByMessage[messageId];
    setIdealByMessage((p) => ({ ...p, [messageId]: offer.offer_id }));  // otimista
    try {
      await ranking.markIdeal(session.access_token, activeThreadId, messageId, offer.offer_id);
    } catch (err) {
      setIdealByMessage((p) => {
        const n = { ...p };
        if (prev) n[messageId] = prev; else delete n[messageId];
        return n;
      });
      setError(err instanceof ApiError ? err.message : "Falha ao marcar cotação ideal");
    }
  }

  async function handleSaveCorrection(offer: Offer, data: CorrectionData) {
    if (!session || !activeThreadId) return;
    const id = offer.offer_id;
    setSavingValidation(true);
    setValidationsByOffer((p) => ({ ...p, [id]: "corrected" }));  // otimista
    try {
      await validations.create(session.access_token, {
        thread_id: activeThreadId, message_id: msgIdOfOffer(id), offer_id: id,
        kind: "corrected", system_offer: snapshotOf(offer), ...data,
      });
      setCorrectionOfferId(null);
    } catch (err) {
      setValidationsByOffer((p) => { const n = { ...p }; delete n[id]; return n; });
      setError(err instanceof ApiError ? err.message : "Falha ao salvar correção");
    } finally {
      setSavingValidation(false);
    }
  }

  async function handleBugSubmit(description: string) {
    if (!session || !activeThreadId) return;
    setBugLoading(true);
    try {
      await bugReports.create(session.access_token, activeThreadId, description);
      setBugOpen(false);
      setToast("Bug reportado — obrigado!");
      setTimeout(() => setToast(null), 3000);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha ao reportar bug");
    } finally {
      setBugLoading(false);
    }
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

            <div className="space-y-6">
            {messages.map((m, mi) => {
              const offers = offersOf(m);
              const isLatest = m.id === lastWithOffersId;
              // Anima só a ÚLTIMA mensagem (entrada da resposta nova). Sem isso,
              // trocar de chat re-animava a lista toda em cascata → travado.
              const isLastMsg = mi === messages.length - 1;
              return (
                <div key={m.id} className={isLastMsg ? "space-y-4 anim-fade-in-up" : "space-y-4"}>
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
                      {offers.map((offer, idx) => {
                        const vState = validationsByOffer[offer.offer_id];  // undefined = sem estado
                        // Controles só no card recomendado do último turno e sem
                        // estado ainda; cards antigos com validação só mostram o badge.
                        const showCtrls = isLatest && idx === 0 && !vState;
                        return (
                          <div key={offer.offer_id}>
                            <OfferCard
                              offer={offer}
                              approving={approving === offer.offer_id}
                              approvedOfferId={isLatest ? approvedOfferId : null}
                              onApprove={isLatest ? handleApprove : () => {}}
                              isBest={isLatest && idx === 0 && !approvedOfferId}
                              readonly={!isLatest}
                              validationState={vState ?? "none"}
                              showValidationControls={showCtrls}
                              onValidate={() => handleValidate(offer)}
                              onOpenCorrection={() => setCorrectionOfferId(offer.offer_id)}
                              isIdeal={idealByMessage[m.id] === offer.offer_id}
                              showIdealControl={isLatest}
                              onMarkIdeal={() => handleMarkIdeal(m.id, offer)}
                            />
                            {correctionOfferId === offer.offer_id && (
                              <CorrectionPanel
                                saving={savingValidation}
                                onSave={(data) => handleSaveCorrection(offer, data)}
                                onCancel={() => setCorrectionOfferId(null)}
                              />
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                  {statusLogOf(m).length > 0 && (
                    <details className="ml-11 group">
                      <summary className="cursor-pointer inline-flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-600 dark:text-zinc-500 dark:hover:text-zinc-300 select-none transition-colors list-none [&::-webkit-details-marker]:hidden">
                        <ChevronRight size={13} className="transition-transform duration-200 group-open:rotate-90" />
                        Ver andamento ({statusLogOf(m).length} etapas)
                      </summary>
                      <div className="mt-2 max-w-xl rounded-xl bg-zinc-50 dark:bg-zinc-900/50 ring-1 ring-black/5 dark:ring-white/5 px-3.5 py-2.5">
                        <div className="max-h-60 overflow-y-auto space-y-1 text-[11.5px] leading-relaxed">
                          {statusLogOf(m).map((line, i) => {
                            const ok = line.includes("✓");
                            const fail = line.includes("✗");
                            return (
                              <div key={i} className={
                                ok ? "text-emerald-600 dark:text-emerald-400"
                                  : fail ? "text-rose-500 dark:text-rose-400"
                                    : "text-zinc-500 dark:text-zinc-400"
                              }>
                                {line}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </details>
                  )}
                </div>
              );
            })}
            </div>

            {sending && (
              <div className="anim-fade-in">
                <ThinkingBubble text={statusText} onCancel={handleCancel}>
                  {statusLog.length > 0 && (
                    <div ref={statusLogRef} className="max-h-44 overflow-y-auto space-y-1 text-[11.5px] leading-relaxed">
                      {statusLog.map((line, i) => {
                        const ok = line.startsWith("✓");
                        const fail = line.startsWith("✗");
                        const last = i === statusLog.length - 1;
                        return (
                          <div
                            key={i}
                            className={[
                              "flex items-start gap-1.5",
                              ok ? "text-emerald-600 dark:text-emerald-400"
                                 : fail ? "text-rose-500 dark:text-rose-400"
                                 : "text-zinc-500 dark:text-zinc-400",
                              last && !ok && !fail ? "opacity-100" : last ? "" : "opacity-70",
                            ].join(" ")}
                          >
                            <span>{line}</span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </ThinkingBubble>
              </div>
            )}

            {error && (
              <div className="text-sm text-brand-700 dark:text-brand-200 bg-brand-50 dark:bg-brand-600/10 border border-brand-200 dark:border-brand-600/30 rounded-xl px-4 py-3">
                {error}
              </div>
            )}
          </div>
        </div>

        {activeThreadId && (
          <div className="max-w-3xl mx-auto w-full px-4 flex justify-end">
            <button
              onClick={() => setBugOpen(true)}
              className="inline-flex items-center gap-1.5 text-[11px] text-gray-400 hover:text-amber-600 dark:text-zinc-500 dark:hover:text-amber-400 transition-colors py-1"
            >
              <Bug size={13} /> Reportar bug
            </button>
          </div>
        )}

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

      <BugReportModal
        open={bugOpen}
        loading={bugLoading}
        onConfirm={handleBugSubmit}
        onCancel={() => { if (!bugLoading) setBugOpen(false); }}
      />

      {toast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 anim-fade-in-up px-4 py-2 rounded-lg bg-emerald-600 text-white text-sm font-medium shadow-lg">
          {toast}
        </div>
      )}

      <style jsx global>{`
        .custom-scroll::-webkit-scrollbar { width: 8px; }
        .custom-scroll::-webkit-scrollbar-track { background: transparent; }
        .custom-scroll::-webkit-scrollbar-thumb { background: rgba(120,120,120,0.2); border-radius: 999px; }
        .custom-scroll::-webkit-scrollbar-thumb:hover { background: rgba(120,120,120,0.4); }
      `}</style>
    </div>
  );
}
