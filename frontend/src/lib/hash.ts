/**
 * Canonical series hash — browser port of contracts/ts/hash.mjs
 * Uses globalThis.crypto.subtle (Web Crypto API, available in all modern browsers + Vite/Vitest).
 *
 * Rules (must match the Python implementation in contracts/ui_state.py exactly):
 *  - numbers: n = floor(abs(v) * 1e6 + 0.5), rendered as decimal string; negative zero → "null" sign dropped
 *  - null/undefined → "null"
 *  - boolean → "true" / "false"
 *  - everything else → String(v)
 *  - cells joined with \x1f, rows sorted lexicographically, joined with \x1e
 *  - full text: columns joined with \x1f + \x1d + rows text
 *  - hash: SHA-256 hex of UTF-8 encoded text
 *
 * Do NOT use toFixed() or localeCompare() — they differ between JS engines.
 */

export function cell(v: unknown): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") {
    if (Number.isNaN(v)) return "nan";
    if (!Number.isFinite(v)) return v > 0 ? "inf" : "-inf";
    const n = Math.floor(Math.abs(v) * 1e6 + 0.5);
    return (v < 0 && n !== 0 ? "-" : "") + String(n);
  }
  return String(v);
}

export function canonicalText(
  rows: Record<string, unknown>[],
  columns: string[]
): string {
  const lines = rows
    .map((r) => columns.map((c) => cell(r[c])).join("\x1f"))
    .sort(); // plain code-unit sort — no localeCompare
  return columns.join("\x1f") + "\x1d" + lines.join("\x1e");
}

export async function canonicalSeriesHash(
  rows: Record<string, unknown>[],
  columns: string[]
): Promise<string> {
  const text = canonicalText(rows, columns);
  const bytes = new TextEncoder().encode(text);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
