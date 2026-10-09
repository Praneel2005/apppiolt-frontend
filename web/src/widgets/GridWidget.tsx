/** Sortable table for metric widgets (dimensions + metrics) and API widgets (declared columns), with row actions. */
import { useMemo, useState } from 'react'
import { runAction } from '../actions/runner'
import { formatCell, formatUnit, humanize } from '../lib/format'
import type { Action, Page, Widget } from '../lib/types'
import { setSort } from '../state/session'
import { useStore } from '../state/store'
import { useWidget, WidgetCard } from './widgetBase'

const MAX_ROWS = 200

interface GridCol { name: string; title: string; type: string; unit: string }

const BADGE: Record<string, string> = {
  open: 'bg-red-50 text-red-700', urgent: 'bg-red-50 text-red-700', high: 'bg-red-50 text-red-700', canceled: 'bg-slate-100 text-slate-600',
  unavailable: 'bg-slate-100 text-slate-600', medium: 'bg-amber-50 text-amber-700', in_progress: 'bg-blue-50 text-blue-700',
  placed: 'bg-blue-50 text-blue-700', in_transit: 'bg-blue-50 text-blue-700', shipped: 'bg-blue-50 text-blue-700',
  scheduled: 'bg-blue-50 text-blue-700', resolved: 'bg-green-50 text-green-700', closed: 'bg-green-50 text-green-700',
  received: 'bg-green-50 text-green-700', delivered: 'bg-green-50 text-green-700', active: 'bg-green-50 text-green-700',
  dismissed: 'bg-slate-100 text-slate-600', ended: 'bg-slate-100 text-slate-600', low: 'bg-slate-100 text-slate-600',
}

function Badge({ v }: { v: unknown }) {
  if (v == null) return <span>—</span>
  const s = String(v)
  return <span className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-medium whitespace-nowrap ${BADGE[s] ?? 'bg-slate-100 text-slate-700'}`}>{humanize(s)}</span>
}

function RowActions({ actions, row }: { actions: Action[]; row: Record<string, unknown> }) {
  const [open, setOpen] = useState(false)
  const btn = (a: Action) => (
    <button key={a.action_id} onClick={() => { setOpen(false); runAction(a, row) }} title={a.description}
      className={`px-2 py-0.5 text-xs rounded-md border whitespace-nowrap ${a.style === 'danger' ? 'border-red-200 text-danger hover:bg-red-50' : 'border-border text-primary hover:bg-primary-soft'}`}>
      {a.label}
    </button>
  )
  if (actions.length <= 2) return <div className="flex gap-1 justify-end">{actions.map(btn)}</div>
  return (
    <div className="flex gap-1 justify-end relative">
      {btn(actions[0])}
      <button onClick={() => setOpen(!open)} className="px-2 py-0.5 text-xs rounded-md border border-border hover:bg-page">⋯</button>
      {open && (
        <div className="absolute right-0 top-7 z-20 bg-white border border-border rounded-lg shadow-lg p-1 flex flex-col gap-1 min-w-32">
          {actions.slice(1).map((a) => <div key={a.action_id} className="flex">{btn(a)}</div>)}
        </div>
      )}
    </div>
  )
}

export function GridWidget({ widget, page }: { widget: Widget; page: Page }) {
  const app = useStore((s) => s.app)!
  const sort = useStore((s) => s.state?.sort ?? null)
  const { req, data, loading, error } = useWidget(widget, page)

  const cols: GridCol[] = useMemo(() => {
    if (widget.source) return widget.source.columns.filter((c) => !c.hidden).map((c) => ({ name: c.name, title: c.title, type: c.type, unit: c.unit }))
    const ds = app.datasets.find((d) => d.dataset_id === widget.dataset_id)
    return [
      ...widget.dimensions.map((d) => ({ name: d, title: humanize(d), type: 'dim', unit: '' })),
      ...widget.metrics.map((m) => { const mm = ds?.metrics.find((x) => x.metric_id === m); return { name: m, title: mm?.title ?? humanize(m), type: 'metric', unit: mm?.unit ?? '' } }),
    ]
  }, [app, widget])

  const canSort = widget.supports.includes('sort') && (!widget.source || !!widget.source.sort_param)
  const active = req.sort
  const toggle = (name: string) => {
    if (!canSort) return
    if (sort?.field !== name) setSort({ field: name, dir: 'desc' })
    else if (sort.dir === 'desc') setSort({ field: name, dir: 'asc' })
    else setSort(null)
  }
  const actions = widget.source?.row_actions ?? []
  const rows = (data?.rows ?? []).slice(0, MAX_ROWS)
  const empty = !loading && !error && (data?.rows.length ?? 0) === 0

  const render = (c: GridCol, v: unknown) => {
    if (c.type === 'badge') return <Badge v={v} />
    if (c.type === 'dim') return c.name === 'order_month' ? formatCell(v, { name: c.name, type: 'text', unit: '' }) : String(v ?? '—')
    if (c.type === 'metric') return formatUnit(v, c.unit)
    const s = formatCell(v, { name: c.name, type: c.type as any, unit: c.unit })
    return s.length > 70 ? <span title={s}>{s.slice(0, 70)}…</span> : s
  }
  const numeric = (c: GridCol) => ['number', 'money', 'percent', 'ratio', 'metric'].includes(c.type)

  return (
    <WidgetCard widget={widget} loading={loading} error={error} empty={empty}
      right={data ? <span className="text-[11px] text-muted">{data.total != null && data.total > data.row_count ? `${data.row_count} of ${data.total}` : `${data.row_count} rows`}</span> : null}>
      <div className="overflow-auto max-h-[460px] scroll-thin -mx-1 px-1">
        <table className="w-full text-sm border-separate border-spacing-0">
          <thead className="sticky top-0 z-10">
            <tr>
              {cols.map((c) => (
                <th key={c.name} onClick={() => toggle(c.name)}
                  className={`bg-page border-y border-border first:border-l first:rounded-l-md last:border-r px-3 py-2 text-xs font-semibold text-muted whitespace-nowrap ${numeric(c) ? 'text-right' : 'text-left'} ${canSort ? 'cursor-pointer hover:text-ink select-none' : ''}`}>
                  {c.title}{active?.field === c.name ? (active.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                </th>
              ))}
              {actions.length > 0 && <th className="bg-page border-y border-r border-border rounded-r-md px-3 py-2 text-xs font-semibold text-muted text-right">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="hover:bg-slate-50">
                {cols.map((c) => <td key={c.name} className={`px-3 py-1.5 border-b border-slate-100 whitespace-nowrap tabular-nums ${numeric(c) ? 'text-right' : ''}`}>{render(c, r[c.name])}</td>)}
                {actions.length > 0 && <td className="px-3 py-1.5 border-b border-slate-100"><RowActions actions={actions} row={r} /></td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data && data.rows.length > MAX_ROWS && <p className="text-xs text-muted mt-2">Showing the first {MAX_ROWS} rows.</p>}
    </WidgetCard>
  )
}
