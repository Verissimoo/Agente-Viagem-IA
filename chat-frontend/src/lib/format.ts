// Formatadores PT-BR usados em vários components.

export function formatBRL(value: number | null | undefined): string {
  if (value == null) return "—";
  return new Intl.NumberFormat("pt-BR", {
    style: "currency", currency: "BRL", maximumFractionDigits: 0,
  }).format(value);
}

export function formatMiles(miles: number | null | undefined): string {
  if (miles == null) return "—";
  return `${new Intl.NumberFormat("pt-BR").format(miles)} milhas`;
}

export function formatTime(isoDt: string | null | undefined): string {
  if (!isoDt) return "—";
  try {
    return new Date(isoDt).toLocaleTimeString("pt-BR", {
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return "—"; }
}

export function formatDate(isoDt: string | null | undefined): string {
  if (!isoDt) return "—";
  try {
    return new Date(isoDt).toLocaleDateString("pt-BR", {
      day: "2-digit", month: "short",
    });
  } catch { return "—"; }
}
