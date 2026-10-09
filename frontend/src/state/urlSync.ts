/**
 * URL ↔ Zustand store synchronisation.
 *
 * URL format (from ADITYA.md §6):
 *   /sales/revenue-by-customer-state
 *     ?from=2018-04-01&to=2018-06-30&preset=last_quarter
 *     &f.customer_region=Southeast&f.customer_state=SP,RJ
 *     &sort=revenue:desc
 *
 * Two directions:
 *  1. Store → URL  : called after setUiState() so deep links always reflect current state
 *  2. URL → Store  : called on initial load to restore a deep-linked view
 */

import type { DateRange, FilterValue, Sort, UiState } from "../lib/types";

/** Build a URL search string from a UiState. */
export function uiStateToSearch(state: UiState): string {
  const p = new URLSearchParams();

  if (state.date_range) {
    p.set("from", state.date_range.from);
    p.set("to", state.date_range.to);
    if (state.date_range.preset) p.set("preset", state.date_range.preset);
  }

  for (const [filterId, fv] of Object.entries(state.filters)) {
    if (fv.value.length > 0) {
      p.set(`f.${filterId}`, fv.value.join(","));
    }
  }

  if (state.sort) {
    p.set("sort", `${state.sort.field}:${state.sort.dir}`);
  }

  return p.toString();
}

/** Parse a URL search string back into partial UiState fields. */
export function searchToUiStateParts(search: string): {
  date_range?: DateRange;
  filters: Record<string, FilterValue>;
  sort?: Sort;
} {
  const p = new URLSearchParams(search);
  const filters: Record<string, FilterValue> = {};

  for (const [key, val] of p.entries()) {
    if (key.startsWith("f.")) {
      const filterId = key.slice(2);
      filters[filterId] = { op: "in", value: val.split(",").filter(Boolean) };
    }
  }

  let date_range: DateRange | undefined;
  if (p.has("from") && p.has("to")) {
    date_range = {
      from: p.get("from")!,
      to: p.get("to")!,
      preset: p.get("preset") ?? undefined,
    };
  }

  let sort: Sort | undefined;
  if (p.has("sort")) {
    const [field, dir] = p.get("sort")!.split(":");
    sort = { field, dir: dir === "asc" ? "asc" : "desc" };
  }

  return { date_range, filters, sort };
}
