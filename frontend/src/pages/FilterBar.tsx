/**
 * Filter bar.
 * One control per entry in page.filters:
 *   - date_range → dropdown of presets + custom from/to (dates from /api/date-presets)
 *   - multiselect → searchable multi-select with allowed_values (synonyms shown as "SP — São Paulo")
 *   - [Reset] → return to page.default_state
 *
 * Filter changes are sent as user_state_change via WS.
 */

import { useEffect, useRef, useState } from "react";
import type { DateRange, FilterDef, FilterValue, Page, UiState } from "../lib/types";
import { useAppStore } from "../state/store";
import { uiStateToSearch } from "../state/urlSync";
import { useNavigate } from "react-router-dom";

interface FilterBarProps {
  page: Page;
}

const DATE_PRESET_LABELS: Record<string, string> = {
  last_7_days: "Last 7 days",
  last_30_days: "Last 30 days",
  last_90_days: "Last 90 days",
  last_12_months: "Last 12 months",
  this_month: "This month",
  last_month: "Last month",
  this_quarter: "This quarter",
  last_quarter: "Last quarter",
  this_year: "This year",
  year_to_date: "Year to date",
  last_year: "Last year",
};

export function FilterBar({ page }: FilterBarProps) {
  const uiState = useAppStore((s) => s.uiState);
  const wsSend = useAppStore((s) => s.wsSend);
  const datePresets = useAppStore((s) => s.datePresets);
  const navigate = useNavigate();

  if (!uiState) return null;

  function updateState(partial: Partial<UiState>) {
    if (!uiState || !wsSend) return;
    const newState: UiState = { ...uiState, ...partial };
    wsSend({ type: "user_state_change", state: newState });
    const search = uiStateToSearch(newState);
    navigate(newState.route + (search ? `?${search}` : ""), { replace: true });
  }

  function handleFilterChange(filterId: string, values: string[]) {
    const newFilters = { ...uiState!.filters };
    if (values.length === 0) {
      delete newFilters[filterId];
    } else {
      newFilters[filterId] = { op: "in", value: values };
    }
    updateState({ filters: newFilters });
  }

  function handleDatePreset(preset: string) {
    const range = datePresets[preset];
    if (!range) return;
    updateState({ date_range: { ...range, preset } });
  }

  function handleReset() {
    const ds = page.default_state as {
      date_range?: { preset?: string; from?: string; to?: string };
      filters?: Record<string, FilterValue>;
    };
    const preset = ds?.date_range?.preset;
    const presetRange = preset ? datePresets[preset] : null;
    const newDateRange: DateRange | null = presetRange
      ? { ...presetRange, preset }
      : null;
    updateState({ filters: ds?.filters ?? {}, date_range: newDateRange });
  }

  const currentPreset = uiState.date_range?.preset;

  return (
    <div className="flex flex-wrap items-center gap-3 bg-white border border-slate-200 rounded-xl px-4 py-3">
      {/* Date range filter */}
      {page.filters.some((f) => f.type === "date_range") && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400 font-medium">Date range</span>
          <select
            value={currentPreset ?? ""}
            onChange={(e) => handleDatePreset(e.target.value)}
            className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="" disabled>
              Select preset…
            </option>
            {Object.entries(DATE_PRESET_LABELS).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
          {uiState.date_range && (
            <span className="text-xs text-slate-400">
              {uiState.date_range.from} → {uiState.date_range.to}
            </span>
          )}
        </div>
      )}

      {/* Multiselect filters */}
      {page.filters
        .filter((f) => f.type === "multiselect" || f.type === "select")
        .map((filter) => (
          <MultiSelectFilter
            key={filter.filter_id}
            filter={filter}
            selectedValues={uiState.filters[filter.filter_id]?.value ?? []}
            onChange={(vals) => handleFilterChange(filter.filter_id, vals)}
          />
        ))}

      {/* Reset */}
      <button
        onClick={handleReset}
        className="ml-auto text-xs text-slate-400 hover:text-slate-700 border border-slate-200 rounded-lg px-3 py-1.5 hover:bg-slate-50 transition-colors"
      >
        Reset
      </button>
    </div>
  );
}

// ─── Searchable multi-select dropdown ────────────────────────────────────────

interface MultiSelectFilterProps {
  filter: FilterDef;
  selectedValues: string[];
  onChange: (values: string[]) => void;
}

function MultiSelectFilter({
  filter,
  selectedValues,
  onChange,
}: MultiSelectFilterProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const allowed = filter.allowed_values ?? [];

  const label = (val: string) => {
    const syn = filter.synonyms?.[val];
    return syn?.length ? `${val} — ${syn[0]}` : val;
  };

  const filtered = allowed.filter((v) =>
    label(v).toLowerCase().includes(search.toLowerCase())
  );

  function toggle(val: string) {
    if (selectedValues.includes(val)) {
      onChange(selectedValues.filter((v) => v !== val));
    } else {
      onChange([...selectedValues, val]);
    }
  }

  const buttonLabel =
    selectedValues.length === 0
      ? filter.title
      : selectedValues.length === 1
      ? label(selectedValues[0])
      : `${filter.title} (${selectedValues.length})`;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-sm border border-slate-200 rounded-lg px-3 py-1.5 bg-white text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500"
      >
        {buttonLabel}
        <span className="text-slate-400 text-xs">▾</span>
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-60 bg-white border border-slate-200 rounded-xl shadow-lg overflow-hidden">
          <div className="p-2 border-b border-slate-100">
            <input
              type="text"
              placeholder="Search…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full text-sm px-2 py-1 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400"
              autoFocus
            />
          </div>
          <div className="max-h-52 overflow-y-auto">
            {filtered.length === 0 && (
              <div className="text-xs text-slate-400 px-3 py-2">No options</div>
            )}
            {filtered.map((val) => (
              <label
                key={val}
                className="flex items-center gap-2 px-3 py-2 hover:bg-slate-50 cursor-pointer text-sm text-slate-700"
              >
                <input
                  type="checkbox"
                  checked={selectedValues.includes(val)}
                  onChange={() => toggle(val)}
                  className="accent-indigo-600"
                />
                {label(val)}
              </label>
            ))}
          </div>
          {selectedValues.length > 0 && (
            <div className="p-2 border-t border-slate-100">
              <button
                onClick={() => onChange([])}
                className="text-xs text-slate-400 hover:text-red-500"
              >
                Clear all
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
