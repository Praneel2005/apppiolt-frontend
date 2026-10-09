import { runAction } from '../actions/runner'
import type { Application, Page, Widget } from '../lib/types'
import { toast, useStore } from '../state/store'
import { ChartWidget } from '../widgets/ChartWidget'
import { GridWidget } from '../widgets/GridWidget'
import { KpiCard } from '../widgets/KpiCard'
import { KpiStrip } from '../widgets/KpiStrip'
import { FilterBar } from './FilterBar'

function renderWidget(w: Widget, page: Page) {
  switch (w.type) {
    case 'kpi_card': return <KpiCard key={w.widget_id} widget={w} page={page} />
    case 'kpi_strip': return <KpiStrip key={w.widget_id} widget={w} page={page} />
    case 'bar_chart':
    case 'pie_chart':
    case 'line_chart': return <ChartWidget key={w.widget_id} widget={w} page={page} />
    case 'grid': return <GridWidget key={w.widget_id} widget={w} page={page} />
    default: return null
  }
}

/** Rows of widgets: the page's declared layout, or a sensible default for generated report pages. */
function layoutRows(app: Application, page: Page): { ids: string[]; weights: number[] }[] {
  if (page.layout) return page.layout.map((ids) => ({ ids, weights: ids.map(() => 1) }))
  const ws = page.widgets.map((id) => app.widgets.find((w) => w.widget_id === id)!)
  const kpis = ws.filter((w) => w.type === 'kpi_card')
  const rest = ws.filter((w) => w.type !== 'kpi_card')
  const rows = kpis.length ? [{ ids: kpis.map((w) => w.widget_id), weights: kpis.map(() => 1) }] : []
  const bar = rest.find((w) => w.type === 'bar_chart')
  const grids = rest.filter((w) => w.type === 'grid')
  if (bar && grids.length === 1 && rest.length === 2 && grids[0].dimensions.length < 2) {
    rows.push({ ids: [bar.widget_id, grids[0].widget_id], weights: [3, 2] })  // breakdown: chart left, table right
  } else {
    for (const w of rest) rows.push({ ids: [w.widget_id], weights: [1] })
  }
  return rows
}

export function PageView({ page }: { page: Page }) {
  const app = useStore((s) => s.app)!
  const rows = layoutRows(app, page)
  const mod = app.modules.find((m) => m.module_id === page.directory.split('/')[0])
  const leaf = app.directories.find((d) => d.path === page.directory)
  const copyLink = async () => {
    try { await navigator.clipboard.writeText(location.href); toast('info', 'Link to this exact view copied') }
    catch { toast('info', location.href) }
  }
  return (
    <div className="flex flex-col gap-4 p-6 max-w-[1500px]">
      <div>
        <div className="text-xs text-muted">{mod?.title}{leaf && leaf.path !== mod?.module_id ? ` › ${leaf.title.replace(mod?.title ?? '', '').trim() || leaf.title}` : ''}</div>
        <div className="flex items-start justify-between gap-4 mt-0.5">
          <div className="min-w-0">
            <h1 className="text-xl font-semibold text-ink flex items-center gap-2">
              {page.title}
              {page.page_code && <span className="text-[11px] font-mono font-normal text-muted bg-surface border border-border rounded px-1.5 py-0.5" title={page.page_id}>{page.page_code}</span>}
            </h1>
            <p className="text-sm text-muted mt-1 max-w-3xl">{page.description}</p>
          </div>
          <div className="flex gap-2 shrink-0">
            <button onClick={copyLink} className="px-3 py-1.5 text-sm rounded-lg border border-border bg-white hover:bg-page">Copy link</button>
            {page.actions.map((a) => (
              <button key={a.action_id} onClick={() => runAction(a)} title={a.description}
                className="px-3 py-1.5 text-sm rounded-lg bg-primary text-white hover:bg-primary-hover">+ {a.label}</button>
            ))}
          </div>
        </div>
      </div>
      <FilterBar page={page} />
      {rows.map((r, i) => (
        <div key={i} className="grid gap-4" style={{ gridTemplateColumns: r.weights.map((w) => `minmax(0, ${w}fr)`).join(' ') }}>
          {r.ids.map((id) => renderWidget(app.widgets.find((w) => w.widget_id === id)!, page))}
        </div>
      ))}
    </div>
  )
}
