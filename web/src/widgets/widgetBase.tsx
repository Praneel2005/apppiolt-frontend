/**
 * Shared widget infrastructure.
 *  useWidget(widget, page): builds the widget's request from the UI state, fetches it, and reports the render
 *  acknowledgement (what was requested and what was actually shown) that the backend verifier checks.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { canonicalSeriesHash } from '../lib/hash'
import { buildRequest, requestKey } from '../lib/request'
import type { Page, Widget, WidgetData, WidgetRequest } from '../lib/types'
import { api } from '../net/api'
import { reportAck, useStore } from '../state/store'

export function highlightWidget(widgetId: string) {
  const el = document.querySelector(`[data-widget-id="${widgetId}"]`)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('widget-highlight')
  setTimeout(() => el.classList.remove('widget-highlight'), 2200)
}

// Identical requests made at the same moment (React dev mode runs effects twice; several widgets can share a
// request) share one network call.
const inflight = new Map<string, Promise<WidgetData>>()
function fetchOnce(key: string, r: WidgetRequest): Promise<WidgetData> {
  let p = inflight.get(key)
  if (!p) {
    p = api.widgetData(r).finally(() => inflight.delete(key))
    inflight.set(key, p)
  }
  return p
}

export interface WidgetView { req: WidgetRequest; data: WidgetData | null; loading: boolean; error: string | null }

export function useWidget(widget: Widget, page: Page): WidgetView {
  const app = useStore((s) => s.app)!
  const state = useStore((s) => s.state)!
  const tick = useStore((s) => s.refreshTick)
  const req = useMemo(() => buildRequest(app, page, widget, state), [app, page, widget, state])
  const key = requestKey(req)
  const [data, setData] = useState<WidgetData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const reqRef = useRef(req)
  reqRef.current = req

  useEffect(() => {
    let cancelled = false
    const r = reqRef.current
    const t0 = performance.now()
    setLoading(true)
    setError(null)
    fetchOnce(`${key}#${tick}`, r)
      .then(async (d) => {
        if (cancelled) return
        setData(d)
        setLoading(false)
        const series_hash = await canonicalSeriesHash(d.rows, d.hash_columns)
        if (cancelled) return
        reportAck(widget.widget_id, key, {
          widget_id: widget.widget_id, applied_filters: r.filters, applied_date_range: r.date_range,
          applied_sort: r.sort, row_count: d.row_count, series_hash, render_ms: Math.round(performance.now() - t0), error: null,
        })
      })
      .catch((e: Error) => {
        if (cancelled) return
        setError(e.message)
        setLoading(false)
        reportAck(widget.widget_id, key, {
          widget_id: widget.widget_id, applied_filters: r.filters, applied_date_range: r.date_range, applied_sort: r.sort,
          row_count: 0, series_hash: '', render_ms: Math.round(performance.now() - t0), error: e.message,
        })
      })
    return () => { cancelled = true }
  }, [key, tick, widget.widget_id])

  return { req, data, loading, error }
}

export function WidgetCard({ widget, loading, error, empty, right, children, className = '' }: {
  widget: Widget; loading?: boolean; error?: string | null; empty?: boolean; right?: ReactNode; children: ReactNode; className?: string
}) {
  return (
    <section data-widget-id={widget.widget_id} className={`bg-surface border border-border rounded-xl shadow-sm flex flex-col min-w-0 ${className}`}>
      <header className="flex items-start justify-between gap-2 px-4 pt-3 pb-2">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-ink truncate" title={widget.description}>{widget.title}</h3>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {right}
          {widget.widget_code && <span className="text-[10px] font-mono text-muted bg-page border border-border rounded px-1.5 py-0.5" title={widget.widget_id}>{widget.widget_code}</span>}
        </div>
      </header>
      <div className="px-4 pb-4 flex-1 min-h-0">
        {loading ? <Skeleton /> : error ? <ErrorBox message={error} /> : empty ? <Empty /> : children}
      </div>
    </section>
  )
}

function Skeleton() {
  return (
    <div className="space-y-2 animate-pulse py-1">
      <div className="h-5 bg-slate-100 rounded w-3/4" /><div className="h-5 bg-slate-100 rounded w-1/2" /><div className="h-5 bg-slate-100 rounded w-2/3" />
    </div>
  )
}
function ErrorBox({ message }: { message: string }) {
  return <div className="text-sm text-danger bg-red-50 border border-red-100 rounded-lg p-3">⚠ {message}</div>
}
function Empty() {
  return <div className="text-sm text-muted py-6 text-center">No data for this selection</div>
}
