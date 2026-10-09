/**
 * What a widget asks the backend for, given the UI state. Mirrors backend/views.py exactly, because the render
 * acknowledgement reports these same "applied" values and the verifier compares them with its own computation:
 *
 *   metric widget : a filter applies iff its field is in the widget's dataset (S5); the date range applies iff the
 *                   widget supports date_range and the dataset has a time field; sort applies iff the column is shown.
 *   API widget    : a filter applies iff its filter_id is listed in source.filter_params (S10); the date range iff
 *                   source.date_params is set; sort iff the API has a sort parameter and the column is declared.
 */
import type { Application, FilterValue, Page, UiState, Widget, WidgetRequest } from './types'

export function buildRequest(app: Application, page: Page, widget: Widget, state: UiState): WidgetRequest {
  const filters: Record<string, FilterValue> = {}
  const src = widget.source
  const fields = new Set(app.datasets.find((d) => d.dataset_id === widget.dataset_id)?.fields.map((f) => f.name) ?? [])
  for (const fid of Object.keys(state.filters).sort()) {
    const fv = state.filters[fid]
    if (!fv?.value?.length) continue
    if (src) {
      if (fid in src.filter_params) filters[fid] = fv
    } else {
      const field = page.filters.find((f) => f.filter_id === fid)?.field ?? fid
      if (fields.has(field)) filters[fid] = fv
    }
  }
  let date_range = null
  if (state.date_range) {
    if (src ? src.date_params : widget.supports.includes('date_range') && app.datasets.find((d) => d.dataset_id === widget.dataset_id)?.time_field) {
      date_range = state.date_range
    }
  }
  let sort = null
  if (state.sort) {
    const cols = src ? src.columns.map((c) => c.name) : [...widget.dimensions, ...widget.metrics]
    const ok = src ? !!src.sort_param && widget.supports.includes('sort') && cols.includes(state.sort.field) : cols.includes(state.sort.field)
    if (ok) sort = state.sort
  }
  return { widget_id: widget.widget_id, page_id: page.page_id, filters, date_range, sort }
}

/** Stable identity of a request; the same key means the same data. */
export function requestKey(r: WidgetRequest): string {
  return JSON.stringify([r.widget_id, r.filters, r.date_range && [r.date_range.from, r.date_range.to], r.sort && [r.sort.field, r.sort.dir]])
}
