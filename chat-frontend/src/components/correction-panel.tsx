"use client";

import { useState } from "react";

export interface CorrectionData {
  found_airline?: string;
  emission_method?: string;
  found_program?: string;
  found_value_brl?: number;
  found_miles?: number;
  observations?: string;
}

const METHODS: { value: string; label: string }[] = [
  { value: "milhas", label: "Milhas" },
  { value: "cash_cia", label: "Cash site da cia" },
  { value: "ota", label: "OTA" },
  { value: "hidden_city", label: "Hidden city" },
  { value: "split", label: "Split" },
  { value: "outro", label: "Outro" },
];

export default function CorrectionPanel({
  onSave, onCancel, saving,
}: {
  onSave: (data: CorrectionData) => void;
  onCancel: () => void;
  saving?: boolean;
}) {
  const [airline, setAirline] = useState("");
  const [method, setMethod] = useState("milhas");
  const [program, setProgram] = useState("");
  const [value, setValue] = useState("");
  const [miles, setMiles] = useState("");
  const [obs, setObs] = useState("");

  const valueNum = value ? Number(value.replace(",", ".")) : undefined;
  const milesNum = miles ? Number(miles.replace(/\D/g, "")) : undefined;
  const canSave = !!(valueNum || milesNum) && !saving;

  const inputCls =
    "w-full text-xs px-2 py-1.5 rounded-md bg-white dark:bg-zinc-900 " +
    "ring-1 ring-gray-200 dark:ring-zinc-700 focus:ring-brand-400 focus:outline-none " +
    "text-gray-800 dark:text-zinc-100";

  return (
    <div className="mt-2 anim-fade-in rounded-lg bg-amber-50/60 dark:bg-amber-500/5 ring-1 ring-amber-200 dark:ring-amber-500/20 p-3 space-y-2">
      <div className="text-xs font-semibold text-amber-800 dark:text-amber-200">
        Registrar o voo que você achou melhor
      </div>
      <div className="grid grid-cols-2 gap-2">
        <input className={inputCls} placeholder="Companhia (ex.: LATAM)"
          value={airline} onChange={(e) => setAirline(e.target.value)} />
        <select className={inputCls} value={method} onChange={(e) => setMethod(e.target.value)}>
          {METHODS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
        {method === "milhas" && (
          <>
            <input className={inputCls} placeholder="Programa (Smiles, LATAM Pass…)"
              value={program} onChange={(e) => setProgram(e.target.value)} />
            <input className={inputCls} placeholder="Milhas (opcional)" inputMode="numeric"
              value={miles} onChange={(e) => setMiles(e.target.value)} />
          </>
        )}
        <input className={inputCls} placeholder="Valor R$ *" inputMode="decimal"
          value={value} onChange={(e) => setValue(e.target.value)} />
      </div>
      <textarea className={inputCls + " resize-none"} rows={2} placeholder="Observações (opcional)"
        value={obs} onChange={(e) => setObs(e.target.value)} />
      <div className="flex items-center gap-2">
        <button
          disabled={!canSave}
          onClick={() => onSave({
            found_airline: airline.trim() || undefined,
            emission_method: method,
            found_program: method === "milhas" ? (program.trim() || undefined) : undefined,
            found_value_brl: valueNum,
            found_miles: milesNum,
            observations: obs.trim() || undefined,
          })}
          className="text-xs font-semibold px-3 py-1.5 rounded-md bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {saving ? "Salvando…" : "Salvar correção"}
        </button>
        <button onClick={onCancel}
          className="text-xs px-3 py-1.5 rounded-md text-gray-600 dark:text-zinc-400 hover:bg-gray-100 dark:hover:bg-zinc-800 transition-colors">
          Cancelar
        </button>
        {!valueNum && !milesNum && (
          <span className="text-[11px] text-gray-400">informe valor R$ ou milhas</span>
        )}
      </div>
    </div>
  );
}
