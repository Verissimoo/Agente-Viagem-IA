"use client";

import { AlertTriangle, FileDown, Plane, CheckCircle2, Clock } from "lucide-react";
import type { Offer } from "@/lib/api";
import { formatBRL, formatDate, formatMiles, formatTime } from "@/lib/format";

interface OfferCardProps {
  offer: Offer;
  approving?: boolean;
  approvedOfferId?: string | null;
  onApprove: (offerId: string) => void;
  isBest?: boolean;
  readonly?: boolean;
}

export default function OfferCard({
  offer, approving, approvedOfferId, onApprove, isBest, readonly,
}: OfferCardProps) {
  const isApproved = approvedOfferId === offer.offer_id;

  return (
    <div
      className={[
        "relative rounded-2xl bg-white dark:bg-zinc-900 p-5",
        "ring-1 ring-black/5 dark:ring-white/10",
        "shadow-[0_1px_3px_rgba(0,0,0,0.04)] dark:shadow-[0_1px_2px_rgba(0,0,0,0.3)]",
        "hover:shadow-[0_8px_24px_-12px_rgba(0,0,0,0.15)] dark:hover:shadow-[0_8px_24px_-12px_rgba(0,0,0,0.5)]",
        "hover:ring-brand-200 dark:hover:ring-brand-600/30",
        "transition-all duration-200",
        isBest ? "ring-2 ring-brand-500 dark:ring-brand-600" : "",
        isApproved ? "ring-2 ring-emerald-500 dark:ring-emerald-600/80" : "",
      ].join(" ")}
    >
      {isBest && (
        <div className="absolute -top-2.5 left-5 px-2.5 py-0.5 rounded-full bg-brand-600 text-white text-[10px] font-bold uppercase tracking-wider shadow-md">
          ⭐ Recomendada
        </div>
      )}

      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <span className="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wider bg-brand-50 text-brand-700 dark:bg-brand-600/20 dark:text-brand-200">
            {offer.category || "Padrão"}
          </span>
          <h3 className="text-lg font-bold mt-1.5 text-gray-900 dark:text-zinc-100">
            {offer.airline || "—"}
          </h3>
        </div>
        <div className="text-right">
          {offer.price_brl != null && (
            <div className="text-2xl font-bold text-gray-900 dark:text-zinc-100 leading-none">
              {formatBRL(offer.price_brl)}
            </div>
          )}
          {offer.miles != null && (
            <>
              <div className={[
                "font-semibold text-gray-700 dark:text-zinc-300 leading-none",
                offer.price_brl != null ? "mt-1 text-sm" : "text-xl",
              ].join(" ")}>
                {formatMiles(offer.miles)}
                {offer.taxes_brl ? (
                  <span className="text-gray-500 dark:text-zinc-500 font-normal">
                    {" + " + formatBRL(offer.taxes_brl)}
                  </span>
                ) : ""}
              </div>
              {offer.equivalent_brl != null && offer.price_brl == null && (
                <div className="mt-1 text-xs text-gray-500 dark:text-zinc-400 italic">
                  ≈ {formatBRL(offer.equivalent_brl)} total
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {offer.category_why && (
        <p className="text-xs text-gray-600 dark:text-zinc-400 italic mb-3 border-l-2 border-blue-200 dark:border-blue-600/40 pl-3">
          {offer.category_why}
        </p>
      )}

      {offer.outbound?.segments && offer.outbound.segments.length > 0 && (
        <Leg title="Ida" segments={offer.outbound.segments} />
      )}
      {offer.inbound?.segments && offer.inbound.segments.length > 0 && (
        <Leg title="Volta" segments={offer.inbound.segments} />
      )}

      {offer.risk_notes && (
        <div className="mt-3 flex items-start gap-2 text-xs bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/30 rounded-lg px-3 py-2 text-amber-800 dark:text-amber-200">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>{offer.risk_notes}</span>
        </div>
      )}

      {/* Otimização de datas via Kayak (só pra splits com flex) */}
      {offer.kayak_date_optimization && offer.kayak_date_optimization.breakdown && (
        <div className="mt-3 text-xs bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/30 rounded-lg px-3 py-2 text-emerald-800 dark:text-emerald-200">
          <div className="flex items-center gap-1.5 mb-1">
            <span className="font-semibold">Melhores datas por perna</span>
            <span className="inline-flex items-center px-1.5 py-0 rounded text-[9px] font-bold bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-200 uppercase tracking-wider">
              ✓ otimizado (±{offer.kayak_date_optimization.flex_days_used}d)
            </span>
          </div>
          <div className="space-y-0.5">
            {offer.kayak_date_optimization.breakdown.map((leg, i) => (
              <div key={i} className="text-[11px] flex items-center gap-1.5">
                <span className="text-emerald-500 dark:text-emerald-400 font-mono">
                  {leg.origin} → {leg.destination}
                </span>
                <span>
                  melhor em <strong>{formatDate(leg.best_date)}</strong>
                  {leg.moved_days !== 0 && (
                    <span className="text-emerald-600/80 dark:text-emerald-300/70">
                      {" "}({leg.moved_days > 0 ? `+${leg.moved_days}` : leg.moved_days}d)
                    </span>
                  )}
                  {" · "}
                  <strong>{formatBRL(leg.price_brl)}</strong>
                  {leg.airline && (
                    <span className="text-emerald-600/80 dark:text-emerald-300/70">
                      {" "}{leg.airline}
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
          <div className="border-t border-emerald-200 dark:border-emerald-500/30 pt-1 mt-1 flex items-center justify-between">
            <span>
              Total: <strong>{formatBRL(offer.kayak_date_optimization.total_price_brl)}</strong>
            </span>
            {offer.kayak_date_optimization.savings_brl != null && offer.kayak_date_optimization.savings_brl > 0 && (
              <span className="font-bold text-emerald-700 dark:text-emerald-300">
                economia de {formatBRL(offer.kayak_date_optimization.savings_brl)}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Alternativa em milhas — hidden city OU split */}
      {offer.miles_alternative && offer.miles_alternative.miles ? (
        <div className="mt-3 flex items-start gap-2 text-xs bg-blue-50 dark:bg-blue-500/10 border border-blue-200 dark:border-blue-500/30 rounded-lg px-3 py-2 text-blue-800 dark:text-blue-200">
          <div className="flex-1">
            <div className="flex items-center gap-1.5">
              <span className="font-semibold">
                {offer.miles_alternative.is_split
                  ? "Mesmo split em milhas"
                  : offer.miles_alternative.validated
                    ? "Mesmo bilhete em milhas"
                    : "Em milhas (mesmo trecho)"}:
              </span>
              {offer.miles_alternative.validated && (
                <span className="inline-flex items-center px-1.5 py-0 rounded text-[9px] font-bold bg-blue-100 dark:bg-blue-500/20 text-blue-700 dark:text-blue-200 uppercase tracking-wider">
                  ✓ verificado
                </span>
              )}
            </div>

            {/* Breakdown por perna pra split */}
            {offer.miles_alternative.is_split && offer.miles_alternative.split_breakdown ? (
              <div className="mt-1 space-y-0.5">
                {offer.miles_alternative.split_breakdown.map((leg, i) => (
                  <div key={i} className="text-[11px] flex items-center gap-1.5">
                    <span className="text-blue-500 dark:text-blue-400 font-mono">
                      {leg.origin} → {leg.destination}
                    </span>
                    <span>
                      {leg.airline} ·{" "}
                      <strong>{new Intl.NumberFormat("pt-BR").format(leg.miles)} mi</strong>
                      {leg.taxes_brl ? <> + {formatBRL(leg.taxes_brl)}</> : ""}
                    </span>
                  </div>
                ))}
                <div className="border-t border-blue-200 dark:border-blue-500/30 pt-1 mt-1">
                  Total:{" "}
                  <strong>
                    {new Intl.NumberFormat("pt-BR").format(offer.miles_alternative.miles)} mi
                  </strong>
                  {offer.miles_alternative.taxes_brl ? (
                    <> + {formatBRL(offer.miles_alternative.taxes_brl)}</>
                  ) : ""}
                  {offer.miles_alternative.equivalent_brl ? (
                    <span className="italic text-blue-600 dark:text-blue-300/80">
                      {" "}(≈ {formatBRL(offer.miles_alternative.equivalent_brl)})
                    </span>
                  ) : ""}
                </div>
              </div>
            ) : (
              /* Single offer (hidden city) */
              <div className="mt-0.5">
                <strong>
                  {new Intl.NumberFormat("pt-BR").format(offer.miles_alternative.miles)} mi
                </strong>
                {offer.miles_alternative.taxes_brl ? (
                  <> + {formatBRL(offer.miles_alternative.taxes_brl)}</>
                ) : ""}
                {offer.miles_alternative.airline ? (
                  <span className="text-blue-600 dark:text-blue-300/80">
                    {" "}· {offer.miles_alternative.airline}
                  </span>
                ) : ""}
                {offer.miles_alternative.equivalent_brl ? (
                  <span className="text-blue-600 dark:text-blue-300/80 italic">
                    {" "}(≈ {formatBRL(offer.miles_alternative.equivalent_brl)})
                  </span>
                ) : ""}
              </div>
            )}

            {offer.miles_alternative.validated && offer.miles_alternative.exact_route_match && (
              <div className="text-[10px] text-blue-600/80 dark:text-blue-300/70 mt-0.5">
                Bilhete físico igual ao hidden city (mesma escala)
              </div>
            )}
          </div>
        </div>
      ) : null}

      <div className="mt-4 flex justify-end">
        {readonly ? (
          <span className="text-xs text-gray-400 dark:text-zinc-500 italic">
            Histórico — pra cotar de novo, peça uma nova busca
          </span>
        ) : isApproved ? (
          <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-emerald-700 dark:text-emerald-400">
            <CheckCircle2 size={16} /> Aprovado · PDF baixado
          </span>
        ) : (
          <button
            onClick={() => onApprove(offer.offer_id)}
            disabled={approving}
            className={[
              "inline-flex items-center gap-1.5 text-sm font-semibold",
              "px-3 py-1.5 rounded-lg",
              "bg-brand-50 text-brand-700 hover:bg-brand-100",
              "dark:bg-brand-600/20 dark:text-brand-200 dark:hover:bg-brand-600/30",
              "disabled:opacity-50 disabled:cursor-not-allowed",
              "transition-colors",
            ].join(" ")}
          >
            {approving ? "Gerando…" : <><FileDown size={14} /> Aprovar e baixar PDF</>}
          </button>
        )}
      </div>
    </div>
  );
}

function Leg({
  title, segments,
}: { title: string; segments: NonNullable<Offer["outbound"]>["segments"] }) {
  const first = segments[0];
  const last = segments[segments.length - 1];
  const stops = segments.length - 1;

  // Hidden city: mostra cada segmento separadamente com flag visual.
  // Usados: normais; descartados: strikethrough + cinza.
  const hasDiscarded = segments.some((s) => s.discarded);

  return (
    <div className="border-t border-gray-100 dark:border-zinc-800 pt-3 mt-3 first:border-t-0 first:pt-0 first:mt-0">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-[10px] uppercase tracking-wider text-gray-400 dark:text-zinc-500 font-semibold">
          {title}
        </span>
        <span className="text-[11px] text-gray-500 dark:text-zinc-400 flex items-center gap-1">
          <Clock size={11} /> {formatDate(first.departure_dt)}
        </span>
      </div>

      {hasDiscarded ? (
        // Hidden city: cada segmento em linha, descartados com strikethrough
        <div className="space-y-1">
          {segments.map((seg, idx) => {
            const isDiscarded = !!seg.discarded;
            return (
              <div
                key={idx}
                className={[
                  "flex items-center gap-3 text-sm transition-colors",
                  isDiscarded
                    ? "text-gray-400 dark:text-zinc-600 line-through italic"
                    : "text-gray-900 dark:text-zinc-100",
                ].join(" ")}
              >
                <Plane size={14} className={isDiscarded ? "text-gray-300 dark:text-zinc-700" : "text-gray-400 dark:text-zinc-500"} />
                <span className="font-semibold tabular-nums">{formatTime(seg.departure_dt)}</span>
                <span className="font-medium">{seg.origin}</span>
                <span className="text-gray-300 dark:text-zinc-600">→</span>
                <span className="font-medium">{seg.destination}</span>
                <span className="font-semibold tabular-nums">{formatTime(seg.arrival_dt)}</span>
                {seg.carrier && (
                  <span className="text-xs text-gray-500 dark:text-zinc-400">
                    {seg.carrier}
                  </span>
                )}
                {isDiscarded && (
                  <span className="text-[10px] uppercase tracking-wider font-bold text-amber-600 dark:text-amber-400 ml-auto no-underline not-italic">
                    descartado
                  </span>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        // Padrão (split, cash, milhas): resumo origem→destino
        <div className="flex items-center gap-3 text-sm">
          <Plane size={14} className="text-gray-400 dark:text-zinc-500" />
          <span className="font-semibold text-gray-900 dark:text-zinc-100 tabular-nums">{formatTime(first.departure_dt)}</span>
          <span className="text-gray-500 dark:text-zinc-400 font-medium">{first.origin}</span>
          <span className="text-gray-300 dark:text-zinc-600">→</span>
          <span className="text-gray-500 dark:text-zinc-400 font-medium">{last.destination}</span>
          <span className="font-semibold text-gray-900 dark:text-zinc-100 tabular-nums">{formatTime(last.arrival_dt)}</span>
          <span className="text-gray-400 dark:text-zinc-500 text-xs ml-auto">
            {stops === 0 ? "Direto" : `${stops} ${stops > 1 ? "conexões" : "conexão"}`}
          </span>
        </div>
      )}
    </div>
  );
}
