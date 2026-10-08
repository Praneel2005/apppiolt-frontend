/**
 * Number formatting per ADITYA.md §3 design table.
 * All formatting is driven by the metric's `unit` field from application.json.
 */

export function formatValue(value: unknown, unit: string): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (isNaN(n)) return String(value);

  switch (unit) {
    case "BRL": {
      // R$ + thousands separators; no decimals at ≥ 1000, 2 decimals below
      const abs = Math.abs(n);
      const formatted =
        abs >= 1000
          ? abs.toLocaleString("en-US", { maximumFractionDigits: 0 })
          : abs.toLocaleString("en-US", {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            });
      return (n < 0 ? "-" : "") + "R$ " + formatted;
    }
    case "ratio":
      // percent, 1 decimal: 0.0483 → "4.8%"
      return (n * 100).toFixed(1) + "%";
    case "days":
      // 1 decimal + " days"
      return n.toFixed(1) + " days";
    case "count":
      // integer with separators
      return Math.round(n).toLocaleString("en-US");
    case "score":
      // 2 decimals
      return n.toFixed(2);
    default:
      // fallback: locale number
      return n.toLocaleString("en-US");
  }
}

/** Format a month string like "2018-01" → "Jan 2018" */
export function formatMonth(raw: string): string {
  if (!raw || raw.length < 7) return raw;
  const [year, mon] = raw.split("-");
  const months = [
    "Jan","Feb","Mar","Apr","May","Jun",
    "Jul","Aug","Sep","Oct","Nov","Dec",
  ];
  const idx = parseInt(mon, 10) - 1;
  return `${months[idx] ?? mon} ${year}`;
}
