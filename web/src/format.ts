// Display formatters. Money uses tabular numerals — set in CSS, just render.

export function pct(n: number, digits = 1): string {
  if (!Number.isFinite(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(digits)}%`;
}

export function money(n: number, digits = 0): string {
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

export function pctColor(n: number): string {
  if (n > 0) return "text-(--color-pos)";
  if (n < 0) return "text-(--color-neg)";
  return "text-(--color-text-dim)";
}
