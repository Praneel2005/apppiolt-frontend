/** Generated canvas: a chart, table or form the assistant shows when no existing page fits. */
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { runApi } from '../actions/runner'
import { formatCell, formatCompact, humanize } from '../lib/format'
import type { CanvasSpec } from '../lib/types'
import { useStore } from '../state/store'
import { PALETTE } from '../widgets/ChartWidget'

function CanvasCard({ c, onClose }: { c: CanvasSpec; onClose: () => void }) {
  const cols = c.columns
  const colOf = (n: string) => cols.find((x) => x.name === n)
  const fmtY = (n: string, v: unknown) => formatCell(v, colOf(n) ?? { type: 'number', unit: '', name: n })
  return (
    <section className="bg-surface border-2 border-primary/30 rounded-xl shadow-sm">
      <header className="flex items-start justify-between gap-3 px-4 pt-3 pb-2">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-primary font-semibold">Generated on the fly · {c.kind}</div>
          <h3 className="text-sm font-semibold text-ink">{c.title}</h3>
          {c.subtitle && <p className="text-xs text-muted">{c.subtitle}</p>}
        </div>
        <div className="flex items-center gap-2">
          {c.data_layers.map((l) => <span key={l} className="text-[10px] rounded-full bg-page border border-border px-2 py-0.5 text-muted">{l}</span>)}
          {c.evidence_ids.map((e) => <span key={e} className="text-[10px] font-mono rounded bg-primary-soft text-primary px-1.5 py-0.5">{e}</span>)}
          <button onClick={onClose} className="text-muted hover:text-ink">✕</button>
        </div>
      </header>
      <div className="px-4 pb-4">
        {c.kind === 'form' && c.form && (
          <div className="flex items-center justify-between gap-4 bg-page rounded-lg p-3">
            <div className="text-sm"><b>{c.form.title}</b><div className="text-xs text-muted">{c.form.method} {c.form.path} · {Object.keys(c.form.body_schema.properties ?? {}).length} fields</div></div>
            <button className="px-3 py-1.5 text-sm rounded-lg bg-primary text-white hover:bg-primary-hover"
              onClick={() => runApi(c.form!.api_id, c.form!.title, c.form!.defaults)}>Open form</button>
          </div>
        )}
        {c.kind === 'chart' && c.chart && (
          <ResponsiveContainer width="100%" height={c.chart_type === 'line_chart' ? 260 : Math.max(200, c.rows.length * 28 + 50)}>
            {c.chart_type === 'line_chart' ? (
              <LineChart data={c.rows} margin={{ left: 4, right: 16, top: 8 }}>
                <CartesianGrid stroke="#F1F5F9" vertical={false} /><XAxis dataKey={c.chart.x} tick={{ fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 11 }} axisLine={false} tickLine={false} width={60} tickFormatter={(v: number) => formatCompact(v, '')} />
                <Tooltip formatter={(v: any, n: any) => [fmtY(String(n), v), humanize(String(n))]} />{c.chart.y.length > 1 && <Legend />}
                {c.chart.y.map((y, i) => <Line key={y} dataKey={y} stroke={PALETTE[i % 5]} strokeWidth={2} />)}
              </LineChart>
            ) : (
              <BarChart data={c.rows} layout="vertical" margin={{ left: 0, right: 24 }}>
                <CartesianGrid stroke="#F1F5F9" horizontal={false} /><XAxis type="number" tick={{ fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v: number) => formatCompact(v, '')} />
                <YAxis type="category" dataKey={c.chart.x} width={120} tick={{ fontSize: 12 }} axisLine={false} tickLine={false} interval={0} />
                <Tooltip formatter={(v: any, n: any) => [fmtY(String(n), v), humanize(String(n))]} />{c.chart.y.length > 1 && <Legend />}
                {c.chart.y.map((y, i) => <Bar key={y} dataKey={y} fill={PALETTE[i % 5]} radius={[0, 4, 4, 0]} barSize={16} />)}
              </BarChart>
            )}
          </ResponsiveContainer>
        )}
        {c.kind === 'table' && (
          <div className="overflow-auto max-h-80 scroll-thin">
            <table className="w-full text-sm">
              <thead><tr>{cols.map((x) => <th key={x.name} className="text-left text-xs font-semibold text-muted px-3 py-2 bg-page whitespace-nowrap">{x.title}</th>)}</tr></thead>
              <tbody>{c.rows.map((r, i) => <tr key={i} className="border-t border-slate-100">{cols.map((x) => <td key={x.name} className="px-3 py-1.5 whitespace-nowrap tabular-nums">{formatCell(r[x.name], x)}</td>)}</tr>)}</tbody>
            </table>
          </div>
        )}
        {c.note && <p className="text-xs text-muted mt-2">{c.note}</p>}
      </div>
    </section>
  )
}

export function CanvasArea() {
  const canvases = useStore((s) => s.canvases)
  if (!canvases.length) return null
  return (
    <div className="px-6 pt-4 space-y-3 max-w-[1500px]">
      {canvases.slice(-2).map((c) => <CanvasCard key={c.canvas_id} c={c} onClose={() => useStore.setState((s) => ({ canvases: s.canvases.filter((x) => x.canvas_id !== c.canvas_id) }))} />)}
    </div>
  )
}
