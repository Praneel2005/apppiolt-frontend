// Reference JavaScript implementation of canonical_series_hash (see contracts/ui_state.py).
// Plain ESM so it can be tested with zero installs:  node --test contracts/ts/
// In the browser use globalThis.crypto.subtle instead of node:crypto.
import { webcrypto } from "node:crypto";

export function cell(v) {
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

export function canonicalText(rows, columns) {
  const lines = rows.map((r) => columns.map((c) => cell(r[c])).join("\x1f")).sort();
  return columns.join("\x1f") + "\x1d" + lines.join("\x1e");
}

export async function canonicalSeriesHash(rows, columns) {
  const bytes = new TextEncoder().encode(canonicalText(rows, columns));
  const digest = await webcrypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
