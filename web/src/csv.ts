// Tiny CSV builder + browser download helper.
// Used by Find/Walk-forward to export their tables for spreadsheet review.

function escapeCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  const s = typeof v === "object" ? JSON.stringify(v) : String(v);
  // RFC 4180 — quote if it contains a comma, quote, or newline; double-quote any embedded quotes.
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

export function toCsv(headers: string[], rows: unknown[][]): string {
  const lines = [headers.join(",")];
  for (const r of rows) lines.push(r.map(escapeCell).join(","));
  return lines.join("\n");
}

export function downloadCsv(filename: string, csv: string): void {
  // Add a BOM so Excel auto-detects UTF-8 on open.
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Defer revoke to next tick so the click is fully processed.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function todayStamp(): string {
  const d = new Date();
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}
