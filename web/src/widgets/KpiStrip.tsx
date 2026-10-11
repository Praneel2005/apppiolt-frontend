/** A row of headline numbers read from the first row of an API (one tile per visible column). */
import { formatCell } from '../lib/format'
import type { Page, Widget } from '../lib/types'
import { useWidget } from './widgetBase'

export function KpiStrip({ widget, page }: { widget: Widget; page: Page }) {
  const { data, loading, error } = useWidget(widget, page)
  const cols = widget.source!.columns.filter((c) => !c.hidden)
  const row = data?.rows[0]
  return (
    <section data-widget-id={widget.widget_id} title={widget.description} className="relative">
      {error ? (
        <div className="text-sm text-danger bg-danger-soft border border-danger/20 rounded-lg p-3">⚠ {error}</div>
      ) : (
        <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${cols.length}, minmax(0, 1fr))` }}>
          {cols.map((c) => (
            <div key={c.name} className="card ap-rise px-5 py-4 min-w-0">
              <div className="w-6 h-1 rounded-full bg-accent mb-2" /><div className="text-xs text-muted truncate">{c.title}</div>
              {loading ? <div className="h-8 mt-1 rounded bg-primary-mist animate-pulse" /> : (
                <div className="text-2xl font-semibold tracking-tight text-primary tabular-nums mt-0.5 truncate">{formatCell(row?.[c.name], c)}</div>
              )}
            </div>
          ))}
        </div>
      )}
      {widget.widget_code && <span className="absolute -top-2 right-2 text-[10px] font-mono text-muted bg-page border border-border rounded px-1.5" title={widget.widget_id}>{widget.widget_code}</span>}
    </section>
  )
}
