/** Assistant side panel: live trace of what the agent does, confirmation cards, a plan runner (no LLM needed),
 *  and "what the assistant can see right now". */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Preview } from '../actions/ActionDialog'
import type { AgentEvent, WriteResult } from '../lib/types'
import { api, ApiError } from '../net/api'
import { openUrl } from '../state/session'
import { toast, useStore } from '../state/store'
import { highlightWidget } from '../widgets/widgetBase'

type Tab = 'chat' | 'runner' | 'context'

const short = (v: unknown, n = 90) => { const s = typeof v === 'string' ? v : JSON.stringify(v); return s.length > n ? s.slice(0, n) + '…' : s }

function Trace({ e }: { e: AgentEvent }) {
  const d = e.data
  switch (e.type) {
    case 'understanding': return <div>◆ intent <b>{d.intent}</b> {d.slots && Object.keys(d.slots).length ? short(d.slots) : ''}</div>
    case 'retrieval': return <div>◆ retrieve {(d.candidates ?? []).slice(0, 3).map((c: any) => `${c.page_id ?? c.id} (${c.score})`).join(' · ')}</div>
    case 'plan': return <div>◆ plan {(d.steps ?? []).map((s: any, i: number) => <div key={i} className="pl-4 text-slate-300">{i + 1}. {s.tool} <span className="text-slate-400">{short(s.args, 70)}</span></div>)}</div>
    case 'validation': return d.ok ? <div className="text-emerald-300">✓ plan validated</div>
      : <div className="text-red-300">✕ plan rejected{(d.issues ?? []).map((x: any, i: number) => <div key={i} className="pl-4">{x.code}: {x.message}{x.suggestion ? ` → ${x.suggestion}` : ''}</div>)}</div>
    case 'action': return <div className={d.status === 'failed' ? 'text-red-300' : d.status === 'done' ? 'text-slate-400' : ''}>
      {d.status === 'running' ? '▶' : d.status === 'done' ? '✓' : '✕'} {d.tool} {d.status === 'running' ? short(d.args, 60) : d.message ? `— ${short(d.message, 100)}` : ''}</div>
    case 'verify': return d.ok ? <div className="text-emerald-300">✓ screen verified <span className="text-slate-400">({d.source})</span></div>
      : <div className="text-red-300">✕ verification caught a mismatch: {short(d.mismatches, 160)}</div>
    case 'canvas': return <div>◆ canvas <b>{d.title}</b> ({d.kind})</div>
    case 'error': return <div className="text-red-300">✕ {d.code}: {d.message}</div>
    case 'done': return <div className="text-slate-500">— {d.steps} step(s) · {d.elapsed_ms} ms {d.ok === false ? '· stopped' : ''}</div>
    default: return null
  }
}

function EvidenceChip({ e }: { e: AgentEvent }) {
  const ev = e.data
  const view = ev.values?.view
  const top = (ev.values?.rows ?? [])[0]
  return (
    <div className="border border-primary/30 bg-primary-soft rounded-lg px-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span><b className="font-mono text-primary">{ev.evidence_id}</b> · {ev.kind.replace('_', ' ')} · {ev.source}</span>
        {view?.url && <button className="text-primary underline" onClick={() => { openUrl(view.url); setTimeout(() => view.widget_id && highlightWidget(view.widget_id), 900) }}>
          Open view{view.page_code ? ` ${view.page_code}` : ''}{view.exact === false ? ' (approx.)' : ''}</button>}
      </div>
      {top && <div className="text-muted mt-1 truncate">{short(top, 120)}</div>}
      {ev.values?.untrusted_fields && <div className="text-warning mt-1">contains customer-written text ({ev.values.untrusted_fields.join(', ')}); treated as data only</div>}
    </div>
  )
}

function ConfirmCard({ e, state, onAnswer }: { e: AgentEvent; state?: 'approved' | 'declined'; onAnswer: (a: boolean) => void }) {
  const d = e.data
  const preview: WriteResult | undefined = d.preview
  return (
    <div className="border border-amber-200 bg-amber-50 rounded-lg p-3 text-sm">
      <div className="text-[11px] uppercase tracking-wide text-warning font-semibold">{d.kind === 'write' ? 'Confirm change' : 'Confirm step'}{d.auto_confirmed ? ' · auto-approved (test)' : ''}</div>
      <div className="font-medium text-ink mt-0.5">{d.summary}</div>
      {preview && <div className="mt-2 max-h-60 overflow-auto"><Preview res={preview} /></div>}
      {state ? <div className="mt-2 text-xs font-medium">{state === 'approved' ? '✓ Approved' : '✕ Declined'}</div> : !d.auto_confirmed && (
        <div className="flex gap-2 mt-3">
          <button className="px-3 py-1.5 text-sm rounded-lg bg-primary text-white hover:bg-primary-hover" onClick={() => onAnswer(true)}>Approve</button>
          <button className="px-3 py-1.5 text-sm rounded-lg border border-border bg-white hover:bg-page" onClick={() => onAnswer(false)}>Decline</button>
        </div>
      )}
    </div>
  )
}

function ChatTab() {
  const events = useStore((s) => s.events)
  const sid = useStore((s) => s.sessionId)
  const [text, setText] = useState('')
  const [confirmMode, setConfirmMode] = useState(false)
  const [msgs, setMsgs] = useState<{ text: string; at: number; note?: boolean }[]>([])
  const [answered, setAnswered] = useState<Record<string, 'approved' | 'declined'>>({})
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [events.length, msgs.length])

  const send = async () => {
    const t = text.trim()
    if (!t || !sid) return
    const at = events.at(-1)?.seq ?? 0
    setMsgs((m) => [...m, { text: t, at }]); setText('')
    try { await api.message(sid, t, confirmMode) }
    catch (e) {
      const note = e instanceof ApiError && e.code === 'agent_not_configured'
        ? 'No language-model agent is attached to the backend yet. Use the Plan runner tab to run plans directly.' : String(e)
      setMsgs((m) => [...m, { text: note, at: events.at(-1)?.seq ?? at, note: true }])
    }
  }
  const answer = async (id: string, approve: boolean) => {
    if (!sid) return
    try { await api.confirm(sid, id, approve); setAnswered((a) => ({ ...a, [id]: approve ? 'approved' : 'declined' })) }
    catch (e) { toast('error', e instanceof Error ? e.message : String(e)) }
  }

  const items = useMemo(() => [
    ...events.map((e) => ({ seq: e.seq, e, m: null as null | { text: string; note?: boolean } })),
    ...msgs.map((m) => ({ seq: m.at + 0.5, e: null as AgentEvent | null, m })),
  ].sort((a, b) => a.seq - b.seq), [events, msgs])

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex-1 overflow-auto scroll-thin px-3 py-3 space-y-2">
        {items.length === 0 && (
          <div className="text-sm text-muted p-3 bg-page rounded-lg">Ask for something like “late orders in Rio last month” or “why did revenue jump in November 2017”.
            Every step the assistant takes shows up here, is validated before it runs and verified on screen afterwards.</div>
        )}
        {items.map((it, i) => it.m ? (
          <div key={i} className={`text-sm rounded-lg px-3 py-2 ${it.m.note ? 'bg-amber-50 text-warning border border-amber-100' : 'bg-primary text-white ml-8'}`}>{it.m.text}</div>
        ) : it.e!.type === 'confirm_request' ? (
          <ConfirmCard key={i} e={it.e!} state={answered[it.e!.data.action_id]} onAnswer={(a) => answer(it.e!.data.action_id, a)} />
        ) : it.e!.type === 'evidence' ? <EvidenceChip key={i} e={it.e!} />
        : it.e!.type === 'answer' ? (
          <div key={i} className="bg-white border border-border rounded-lg px-3 py-2 text-sm">
            {it.e!.data.text}
            <div className="mt-1 flex flex-wrap gap-1">{(it.e!.data.citations ?? []).map((c: string) => <span key={c} className="text-[10px] font-mono bg-primary-soft text-primary rounded px-1.5 py-0.5">{c}</span>)}</div>
          </div>
        ) : it.e!.type === 'answer_delta' ? null : (
          <div key={i} className="bg-slate-900 text-slate-100 font-mono text-[11px] leading-5 rounded-lg px-3 py-1"><Trace e={it.e!} /></div>
        ))}
        <div ref={end} />
      </div>
      <div className="border-t border-border p-3 space-y-2">
        <div className="flex gap-2">
          <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void send()} placeholder="Ask the assistant…"
            className="flex-1 border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30" />
          <button onClick={() => void send()} className="px-3 py-2 text-sm rounded-lg bg-primary text-white hover:bg-primary-hover">Send</button>
        </div>
        <label className="flex items-center gap-2 text-xs text-muted"><input type="checkbox" checked={confirmMode} onChange={(e) => setConfirmMode(e.target.checked)} />
          Confirm every screen change (data changes are always confirmed)</label>
      </div>
    </div>
  )
}

const step = (tool: string, args: unknown) => ({ tool, args })
const PLANS: Record<string, unknown> = {
  'Late orders in Rio, last month': { intent: { name: 'navigate' }, steps: [step('navigate', { page_id: 'ops.late_deliveries', filters: { customer_state: { value: ['Rio de Janeiro'] } }, date_range: { preset: 'last_month' } })] },
  'Revenue by state + chart': { intent: { name: 'analyze_trend' }, steps: [step('run_metric_query', { metric: 'revenue', group_by: ['customer_state'], preset: 'last_quarter', limit: 8 }), step('render_canvas', { kind: 'chart', evidence_id: 'E1', title: 'Revenue by state, last quarter', x: 'customer_state', y: ['value'] })] },
  'Why did revenue jump in Nov 2017?': { intent: { name: 'explain_change' }, steps: [step('explain_change', { metric: 'revenue', group_by: 'product_category', preset: 'month:2017-11', limit: 5 })] },
  'Why did late rate move? (mix/rate)': { intent: { name: 'explain_change' }, steps: [step('explain_change', { metric: 'late_rate', group_by: 'customer_region', preset: 'quarter:2018Q2' })] },
  'Revenue trend 2017': { intent: { name: 'analyze_trend' }, steps: [step('analyze_trend', { metric: 'revenue', preset: 'year:2017' })] },
  'Restock the top low-stock product (asks to confirm)': { intent: { name: 'multi_step' }, steps: [step('call_api', { api_id: 'inventory.alerts', params: { limit: 1 } }), step('navigate', { page_id: 'ops.low_stock' })] },
  'Reply form for a review': { intent: { name: 'multi_step' }, steps: [step('render_canvas', { kind: 'form', api_id: 'tickets.create', defaults: { category: 'late_delivery' } })] },
}

function RunnerTab() {
  const sid = useStore((s) => s.sessionId)
  const link = useStore((s) => s.link)
  const [name, setName] = useState(Object.keys(PLANS)[0])
  const [text, setText] = useState(JSON.stringify(PLANS[Object.keys(PLANS)[0]], null, 2))
  const [auto, setAuto] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [fault, setFault] = useState('none')

  const run = async () => {
    if (!sid) return
    let plan: unknown
    try { plan = JSON.parse(text) } catch (e) { toast('error', 'Invalid JSON: ' + String(e)); return }
    setBusy(true); setResult(null)
    try {
      const r = await api.runPlan(sid, plan, auto)
      setResult(r)
      if (link !== 'open') useStore.setState((s) => ({ events: [...s.events, ...r.events] }))
      if (r.state) useStore.setState({ state: r.state })
    } catch (e) { setResult({ ok: false, error: e instanceof ApiError ? `${e.code}: ${e.message}` : String(e) }) }
    finally { setBusy(false) }
  }
  return (
    <div className="p-3 space-y-3 overflow-auto scroll-thin h-full">
      <p className="text-xs text-muted">Runs a plan through the same validator → executor → verifier the language-model agent will use. No LLM involved.</p>
      <select value={name} onChange={(e) => { setName(e.target.value); setText(JSON.stringify(PLANS[e.target.value], null, 2)) }} className="w-full border border-border rounded-lg px-2 py-1.5 text-sm">
        {Object.keys(PLANS).map((k) => <option key={k}>{k}</option>)}
      </select>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={12} spellCheck={false} className="w-full border border-border rounded-lg p-2 font-mono text-xs" />
      <div className="flex items-center justify-between">
        <label className="flex items-center gap-2 text-xs text-muted"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> auto-approve confirmations (testing)</label>
        <button disabled={busy} onClick={() => void run()} className="px-3 py-1.5 text-sm rounded-lg bg-primary text-white hover:bg-primary-hover">{busy ? 'Running…' : 'Run plan'}</button>
      </div>
      <label className="flex items-center gap-2 text-xs text-muted">Simulated-browser fault (when no browser is attached)
        <select value={fault} onChange={(e) => { setFault(e.target.value); if (sid) void api.setFault(sid, e.target.value) }} className="border border-border rounded px-1 py-0.5">
          {['none', 'drop_filter', 'empty_widget', 'wrong_route'].map((f) => <option key={f}>{f}</option>)}
        </select>
      </label>
      {result && (
        <div className={`rounded-lg p-3 text-xs border ${result.ok ? 'bg-green-50 border-green-100' : 'bg-red-50 border-red-100'}`}>
          <b>{result.ok ? '✓ Plan executed' : `✕ ${result.stage ?? 'failed'}`}</b>
          {result.error && <div>{result.error}</div>}
          {(result.issues ?? []).map((i: any, k: number) => <div key={k}>{i.code}: {i.message}{i.suggestion ? ` → ${i.suggestion}` : ''}</div>)}
          {(result.results ?? []).filter((r: any) => !r.ok).map((r: any, k: number) => <div key={k}>step {r.step_index + 1}: {r.error_code} — {r.message}</div>)}
          <div className="text-muted mt-1">Watch the Chat tab for the full trace.</div>
        </div>
      )}
    </div>
  )
}

function ContextTab() {
  const sid = useStore((s) => s.sessionId)
  const state = useStore((s) => s.state)
  const [ctx, setCtx] = useState<any>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    if (!sid) return
    const t = setTimeout(() => { api.context(sid).then(setCtx).catch((e) => setErr(String(e))) }, 600)
    return () => clearTimeout(t)
  }, [sid, state?.version, state?.page_id])
  if (err) return <div className="p-3 text-sm text-danger">{err}</div>
  if (!ctx) return <div className="p-3 text-sm text-muted">Loading…</div>
  return (
    <div className="p-3 space-y-3 overflow-auto scroll-thin h-full text-sm">
      <p className="text-xs text-muted">What the assistant can see right now (page context).</p>
      <div className="bg-page rounded-lg p-3">
        <div className="font-semibold">{ctx.page.title} <span className="font-mono text-xs text-muted">{ctx.page.page_code}</span></div>
        <div className="text-xs text-muted mt-1">{ctx.page.agent_context}</div>
        <div className="text-xs mt-2">Period: <b>{ctx.date_range ? `${ctx.date_range.from} → ${ctx.date_range.to}` : 'all time'}</b></div>
        <div className="text-xs">Filters: {ctx.filters.filter((f: any) => f.value).map((f: any) => <b key={f.filter_id} className="mr-2">{f.title}={f.value.join(',')}</b>)}{ctx.filters.every((f: any) => !f.value) && 'none'}</div>
      </div>
      {ctx.widgets.map((w: any) => (
        <div key={w.widget_id} className="border border-border rounded-lg p-2">
          <div className="flex justify-between text-xs"><b>{w.title}</b><span className="font-mono text-muted">{w.widget_code}</span></div>
          <div className="text-[11px] text-muted">{w.row_count ?? 0} rows · bound to {typeof w.bound_to === 'string' ? w.bound_to : `${w.bound_to?.dataset}`}</div>
          {w.rows?.[0] && <pre className="text-[10px] bg-page rounded p-1.5 mt-1 overflow-auto">{short(w.rows[0], 220)}</pre>}
          {w.row_actions?.length > 0 && <div className="text-[11px] mt-1">Actions: {w.row_actions.map((a: any) => a.label).join(', ')}</div>}
        </div>
      ))}
    </div>
  )
}

export function AgentPanel({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('chat')
  return (
    <aside className="w-[420px] shrink-0 bg-surface border-l border-border flex flex-col h-full">
      <div className="flex items-center justify-between px-3 h-12 border-b border-border shrink-0">
        <div className="flex gap-1">
          {(['chat', 'runner', 'context'] as Tab[]).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`px-3 py-1.5 text-sm rounded-lg ${tab === t ? 'bg-primary-soft text-primary font-medium' : 'text-muted hover:bg-page'}`}>
              {t === 'chat' ? 'Assistant' : t === 'runner' ? 'Plan runner' : 'Page context'}
            </button>
          ))}
        </div>
        <button onClick={onClose} className="text-muted hover:text-ink">✕</button>
      </div>
      <div className="flex-1 min-h-0">{tab === 'chat' ? <ChatTab /> : tab === 'runner' ? <RunnerTab /> : <ContextTab />}</div>
    </aside>
  )
}
