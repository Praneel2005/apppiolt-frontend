/** Bar and line charts for both widget kinds: metric widgets (dimension + metric) and API widgets (source.chart). */
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { formatCell, formatCompact, formatDay, formatMonth, formatUnit, humanize } from '../lib/format'
import { CHART, PALETTE, TOOLTIP } from '../lib/theme'
import type { Column, Page, Widget } from '../lib/types'
import { useStore } from '../state/store'
import { useWidget, WidgetCard } from './widgetBase'

export { PALETTE }
const PIE_COLORS = PALETTE
const MAX_BARS = 30
const MAX_SLICES = 7

function Donut({ slices, fmt, centerFmt }: { slices: { name: string; value: number }[]; fmt: (v: number) => string; centerFmt: (v: number) => string }) {
  const total = slices.reduce((a, s) => a + s.value, 0)
  return (
    <div className="flex items-center gap-5 flex-wrap">
      <div className="relative w-44 h-44 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={slices} dataKey="value" nameKey="name" innerRadius="62%" outerRadius="96%" paddingAngle={1} stroke="none">
              {slices.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
            </Pie>
            <Tooltip formatter={(v: any, n: any) => [fmt(Number(v)), n]} {...TOOLTIP} />
          </PieChart>
        </ResponsiveContainer>
        <div className="absolute inset-0 grid place-items-center pointer-events-none">
          <div className="text-center"><div className="text-base font-semibold text-ink tabular-nums">{centerFmt(total)}</div><div className="text-[10px] text-muted">total</div></div>
        </div>
      </div>
      <ul className="flex-1 min-w-36 space-y-1.5 text-sm">
        {slices.map((s, i) => (
          <li key={s.name} className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: PIE_COLORS[i % PIE_COLORS.length] }} />
            <span className="truncate flex-1" title={s.name}>{s.name}</span>
            <span className="tabular-nums text-muted">{fmt(s.value)}</span>
            <span className="tabular-nums text-xs text-muted w-10 text-right">{total ? Math.round((100 * s.value) / total) : 0}%</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

interface Series { key: string; label: string; fmt: (v: unknown) => string; compact: (v: number) => string }

function seriesFor(widget: Widget, metricUnits: Record<string, { title: string; unit: string }>): { x: string; series: Series[] } {
  if (widget.source) {
    const cols = Object.fromEntries(widget.source.columns.map((c) => [c.name, c])) as Record<string, Column>
    const spec = widget.source.chart!
    return {
      x: spec.x,
      series: spec.y.map((k) => {
        const c = cols[k]
        const unit = c.type === 'money' ? 'BRL' : c.type === 'ratio' ? 'ratio' : ''
        return { key: k, label: c.title, fmt: (v) => formatCell(v, c), compact: (n) => (c.type === 'percent' ? `${n}%` : formatCompact(n, unit)) }
      }),
    }
  }
  return {
    x: widget.dimensions[0],
    series: widget.metrics.map((m) => {
      const info = metricUnits[m]
      return { key: m, label: info?.title ?? m, fmt: (v) => formatUnit(v, info?.unit ?? ''), compact: (n) => formatCompact(n, info?.unit ?? '') }
    }),
  }
}

export function ChartWidget({ widget, page }: { widget: Widget; page: Page }) {
  const app = useStore((s) => s.app)!
  const { data, loading, error } = useWidget(widget, page)
  const metricUnits = Object.fromEntries(app.datasets.flatMap((d) => d.metrics.map((m) => [m.metric_id, { title: m.title, unit: m.unit }])))
  const { x, series } = seriesFor(widget, metricUnits)
  const rows = data?.rows ?? []
  const isLine = widget.type === 'line_chart'
  const label = (v: unknown) => (x === 'order_month' || x === 'month' || x === 'snapshot_date' ? formatMonth(String(v))
    : x === 'day' ? formatDay(String(v)) : String(v ?? '').includes('_') ? humanize(String(v)) : String(v ?? ''))
  const shown = (isLine ? rows : rows.slice(0, MAX_BARS)).map((r) => ({
    ...Object.fromEntries(series.map((s) => [s.key, r[s.key] == null ? null : Number(r[s.key])])), __x: label(r[x]),
  }))
  const empty = !loading && !error && rows.length === 0
  if (widget.type === 'pie_chart') {
    const all = rows.map((r) => ({ name: label(r[x]), value: Number(r[series[0].key] ?? 0) })).filter((s) => s.value > 0).sort((a, b) => b.value - a.value)
    const slices = all.length > MAX_SLICES + 1
      ? [...all.slice(0, MAX_SLICES), { name: 'Other', value: all.slice(MAX_SLICES).reduce((a, s) => a + s.value, 0) }] : all
    return (
      <WidgetCard widget={widget} loading={loading} error={error} empty={empty || slices.length === 0}>
        <Donut slices={slices} fmt={(v) => series[0].fmt(v)} centerFmt={(v) => series[0].compact(v)} />
      </WidgetCard>
    )
  }
  const height = isLine ? 260 : Math.max(200, shown.length * (series.length > 1 ? 36 : 26) + 50)
  const tip = TOOLTIP
  return (
    <WidgetCard widget={widget} loading={loading} error={error} empty={empty}
      right={!isLine && rows.length > MAX_BARS ? <span className="text-[11px] text-muted">top {MAX_BARS} of {rows.length}</span> : null}>
      <ResponsiveContainer width="100%" height={height}>
        {isLine ? (
          <LineChart data={shown} margin={{ left: 4, right: 16, top: 8, bottom: 4 }}>
            <CartesianGrid stroke={CHART.grid} strokeDasharray="3 4" vertical={false} />
            <XAxis dataKey="__x" tick={{ fontSize: 11, fill: CHART.axis }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 11, fill: CHART.axis }} axisLine={false} tickLine={false} width={64} tickFormatter={(v: number) => series[0].compact(v)} />
            <Tooltip {...tip} formatter={(v: any, name: any) => { const s = series.find((q) => q.key === name); return [s ? s.fmt(v) : v, s?.label ?? humanize(String(name))] }} />
            {series.length > 1 && <Legend formatter={(n: any) => series.find((q) => q.key === n)?.label ?? n} wrapperStyle={{ fontSize: 12 }} />}
            {series.map((s, i) => <Line key={s.key} type="monotone" dataKey={s.key} stroke={PALETTE[i % PALETTE.length]} strokeWidth={2.5} dot={shown.length < 20 ? { r: 3, fill: '#fff', strokeWidth: 2 } : false} activeDot={{ r: 5 }} connectNulls={false} />)}
          </LineChart>
        ) : (
          <BarChart data={shown} layout="vertical" margin={{ left: 0, right: 24, top: 4, bottom: 4 }}>
            <CartesianGrid stroke={CHART.grid} strokeDasharray="3 4" horizontal={false} />
            <XAxis type="number" tick={{ fontSize: 11, fill: CHART.axis }} axisLine={false} tickLine={false} tickFormatter={(v: number) => series[0].compact(v)} />
            <YAxis type="category" dataKey="__x" width={120} tick={{ fontSize: 12, fill: CHART.label }} axisLine={false} tickLine={false} interval={0} />
            <Tooltip {...tip} formatter={(v: any, name: any) => { const s = series.find((q) => q.key === name); return [s ? s.fmt(v) : v, s?.label ?? humanize(String(name))] }} />
            {series.length > 1 && <Legend formatter={(n: any) => series.find((q) => q.key === n)?.label ?? n} wrapperStyle={{ fontSize: 12 }} />}
            {series.map((s, i) => <Bar key={s.key} dataKey={s.key} fill={PALETTE[i % PALETTE.length]} radius={[0, 6, 6, 0]} barSize={series.length > 1 ? 12 : 16} background={{ fill: CHART.track, radius: 6 }} />)}
          </BarChart>
        )}
      </ResponsiveContainer>
    </WidgetCard>
  )
}
