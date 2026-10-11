/** Assistant side panel: live trace of what the agent does, confirmation cards, a plan runner (no LLM needed),
 *  and "what the assistant can see right now". */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Preview } from '../actions/ActionDialog'
import type { AgentEvent, WriteResult } from '../lib/types'
import { screenChanged, type AnswerScreen } from '../lib/screen'
import { api, ApiError } from '../net/api'
import { openUrl } from '../state/session'
import { toast, useStore } from '../state/store'
import { highlightWidget } from '../widgets/widgetBase'
import { Markdown } from './Markdown'
import { ProofPanel } from './ProofPanel'

type Tab = 'chat' | 'runner' | 'context' | 'metrics'

const short = (v: unknown, n = 90) => { const s = typeof v === 'string' ? v : JSON.stringify(v); return s.length > n ? s.slice(0, n) + '…' : s }

const OK = 'text-[#8fd3a4]'
const BAD = 'text-[#f2a99b]'

/** One line of the work log (shown on the deep-green log background). */
function Trace({ e }: { e: AgentEvent }) {
  const d = e.data
  switch (e.type) {
    case 'retrieval': return <div>◆ candidates {((d.pages ?? d.candidates ?? []) as any[]).slice(0, 3).map((c) => `${c.page_id ?? c.id} (${c.score})`).join(' · ')}</div>
    case 'analysis_step': {
      if (d.tasks) return <div>◆ plan <span className="text-white/45">— {d.detail}</span></div>
      if (d.task) {
        const s = d.task.status as string
        return <div className={s === 'failed' ? BAD : s === 'done' ? OK : s === 'skipped' ? 'text-white/45' : ''}>
          {s === 'running' ? '▶' : s === 'done' ? '✓' : s === 'failed' ? '✕' : '–'} {d.title}{d.task.ms ? <span className="text-white/45"> · {Math.round(d.task.ms)} ms</span> : null}
          {(s === 'skipped' || s === 'failed') && d.detail ? <span className="text-white/45"> — {short(d.detail, 80)}</span> : null}</div>
      }
      return <div>⟳ {d.title}{d.detail ? <span className="text-white/45"> — {short(d.detail, 110)}</span> : null}</div>
    }
    case 'form_open': return <div>◆ form opened: <b>{d.title || d.api_id}</b></div>
    case 'form_patch': return <div>◆ form {d.status ?? 'updated'}{d.changes && Object.keys(d.changes).length ? `: ${Object.keys(d.changes).join(', ')}` : ''}{d.errors && Object.keys(d.errors).length ? ` · problems: ${Object.keys(d.errors).join(', ')}` : ''}</div>
    case 'plan': return d.skill
      ? <div>◆ skill <b>{d.skill}</b><span className="text-white/45"> · intent {d.intent}</span></div>
      : <div>◆ plan {(d.steps ?? []).map((s: any, i: number) => <div key={i} className="pl-4 text-white/70">{i + 1}. {s.tool} <span className="text-white/45">{short(s.args, 70)}</span></div>)}</div>
    case 'validation': return d.ok ? <div className={OK}>✓ plan validated</div>
      : <div className={BAD}>✕ plan rejected{(d.issues ?? []).map((x: any, i: number) => <div key={i} className="pl-4">{x.code}: {x.message}{x.suggestion ? ` → ${x.suggestion}` : ''}</div>)}</div>
    case 'action': return <div className={d.status === 'failed' ? BAD : d.status === 'done' ? 'text-white/45' : ''}>
      {d.status === 'running' ? '▶' : d.status === 'done' ? '✓' : '✕'} {d.tool} {d.status === 'running' ? short(d.args, 60) : d.message ? `— ${short(d.message, 100)}` : ''}</div>
    case 'verify': return d.ok ? <div className={OK}>✓ screen verified <span className="text-white/45">({d.source})</span></div>
      : <div className={BAD}>✕ verification caught a mismatch: {short(d.mismatches, 160)}</div>
    case 'canvas': return <div>◆ canvas <b>{d.title}</b> ({d.kind})</div>
    case 'error': return <div className={BAD}>✕ {d.code}: {d.message}</div>
    default: return null
  }
}

function EvidenceChip({ e }: { e: AgentEvent }) {
  const ev = e.data
  const view = ev.values?.view
  const top = (ev.values?.rows ?? [])[0]
  return (
    <div className="border border-white/12 bg-white/6 rounded-lg px-2.5 py-1.5 text-[11px] font-sans">
      <div className="flex items-center justify-between gap-2">
        <span className="text-white/85"><b className="font-mono text-accent">{ev.evidence_id}</b> · {String(ev.kind).replace('_', ' ')} · {ev.source}</span>
        {view?.url && <button className="text-[#8fd3a4] underline" onClick={() => { openUrl(view.url); setTimeout(() => view.widget_id && highlightWidget(view.widget_id), 900) }}>
          Open view{view.page_code ? ` ${view.page_code}` : ''}{view.exact === false ? ' (approx.)' : ''}</button>}
      </div>
      {top && <div className="text-white/45 mt-0.5 truncate">{short(top, 120)}</div>}
      {ev.values?.untrusted_fields && <div className="text-[#f0c987] mt-0.5">contains customer-written text ({ev.values.untrusted_fields.join(', ')}); treated as data only</div>}
    </div>
  )
}

/** What the assistant understood, before it acts: the one-line meaning, how it was decided, and every assumption it made. */
function Understood({ e }: { e: AgentEvent }) {
  const d = e.data
  const how = d.source === 'llm' ? 'language model' : d.source === 'ui' ? 'button' : 'rules'
  return (
    <div className="ap-rise text-xs border border-border bg-surface rounded-xl px-3.5 py-2.5 shadow-sm">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-muted">Understood as</span>
        <b className="text-ink">{d.paraphrase}</b>
        <span className="text-[10px] uppercase tracking-wide rounded-full bg-primary-mist border border-border px-2 py-0.5 text-primary">{d.intent} · {how}</span>
      </div>
      {(d.assumptions ?? []).length > 0 && (
        <ul className="mt-1.5 list-disc pl-4 text-muted space-y-0.5">{(d.assumptions as string[]).slice(0, 5).map((a, i) => <li key={i}>{a}</li>)}</ul>
      )}
    </div>
  )
}

const STATUS_STYLE: Record<string, string> = {
  VERIFIED: 'bg-success-soft text-success border-success/30',
  PARTIAL: 'bg-warning-soft text-warning border-warning/30',
  FAILED: 'bg-danger-soft text-danger border-danger/30',
}

function Avatar({ pulse }: { pulse?: boolean }) {
  return (
    <div className={`absolute left-0 top-0 w-7 h-7 rounded-full bg-primary-deep text-accent grid place-items-center text-[13px] shadow-sm ${pulse ? 'animate-pulse' : ''}`}>✦</div>
  )
}

/** The assistant's reply: formatted text, a verified/partial/failed badge, citations, links to the view, follow-up chips. */
function AnswerCard({ e, onAsk }: { e: AgentEvent; onAsk: (text: string) => void }) {
  const d = e.data
  const current = useStore((s) => s.state)
  const outdated = screenChanged(d.screen as AnswerScreen | undefined, current)
  const links: string[] = d.deep_links ?? []
  const suggestions: string[] = d.suggestions ?? []
  return (
    <div className="relative pl-9 ap-rise">
      <Avatar />
      <div className="bg-surface border border-border rounded-2xl rounded-tl-md px-4 py-3 space-y-2.5 shadow-sm">
        {d.status && d.status !== 'INFO' && (
          <span className={`inline-block text-[10px] font-semibold uppercase tracking-wide border rounded-full px-2 py-0.5 ${STATUS_STYLE[d.status] ?? ''}`}>
            {d.status === 'VERIFIED' ? '✓ verified' : String(d.status).toLowerCase()}
          </span>
        )}
        <div className="text-[13.5px] leading-relaxed"><Markdown text={String(d.text ?? '')} /></div>
        {(d.citations ?? []).length > 0 && (
          <div className="flex flex-wrap gap-1">{(d.citations as string[]).map((c) => <span key={c} className="text-[10px] font-mono bg-primary-soft text-primary rounded px-1.5 py-0.5">{c}</span>)}</div>
        )}
        {outdated && (
          <div className="flex items-center justify-between gap-2 text-[11.5px] rounded-lg bg-warning-soft text-warning border border-warning/25 px-2.5 py-1.5">
            <span>The screen has changed since this answer.</span>
            {links[0] && <button className="font-medium underline" onClick={() => openUrl(links[0])}>Show it again</button>}
          </div>
        )}
        <ProofPanel claims={d.claims ?? []} citations={d.citations ?? []} />
        {links.length > 0 && (
          <div className="flex gap-3 flex-wrap">{links.slice(0, 2).map((u, i) => (
            <button key={u} className="text-xs text-primary font-medium hover:underline" onClick={() => openUrl(u)}>Open the view{links.length > 1 ? ` ${i + 1}` : ''} →</button>
          ))}</div>
        )}
        {suggestions.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-0.5">{suggestions.slice(0, 4).map((s) => (
            <button key={s} onClick={() => onAsk(s)} className="text-xs border border-primary/25 text-primary bg-primary-mist rounded-full px-3 py-1 hover:bg-primary-soft transition-colors">{s}</button>
          ))}</div>
        )}
      </div>
    </div>
  )
}

function ConfirmCard({ e, state, onAnswer }: { e: AgentEvent; state?: 'approved' | 'declined'; onAnswer: (a: boolean) => void }) {
  const d = e.data
  const preview: WriteResult | undefined = d.preview
  return (
    <div className="ap-rise border border-warning/30 bg-warning-soft rounded-xl p-3.5 text-sm">
      <div className="text-[11px] uppercase tracking-wide text-warning font-semibold">{d.kind === 'write' ? 'Confirm change' : 'Confirm step'}{d.auto_confirmed ? ' · auto-approved (test)' : ''}</div>
      <div className="font-medium text-ink mt-0.5">{d.summary}</div>
      {preview && <div className="mt-2 max-h-60 overflow-auto"><Preview res={preview} /></div>}
      {state ? <div className="mt-2 text-xs font-medium">{state === 'approved' ? '✓ Approved' : '✕ Declined'}</div> : !d.auto_confirmed && (
        <div className="flex gap-2 mt-3">
          <button className="btn btn-primary" onClick={() => onAnswer(true)}>Approve</button>
          <button className="btn" onClick={() => onAnswer(false)}>Decline</button>
        </div>
      )}
    </div>
  )
}

interface PlanTask { id: string; title: string; deps: string[]; status: string }

const TASK_ICON: Record<string, { icon: string; cls: string }> = {
  pending: { icon: '○', cls: 'text-muted/60' }, running: { icon: '◐', cls: 'text-primary animate-pulse' },
  done: { icon: '✓', cls: 'text-success' }, failed: { icon: '✕', cls: 'text-danger' }, skipped: { icon: '–', cls: 'text-muted/60' },
}

/** The analysis plan as it runs: which tasks are waiting, running or finished (independent ones run side by side). */
function PlanChecklist({ plan }: { plan: PlanTask[] }) {
  const done = plan.filter((t) => t.status === 'done' || t.status === 'skipped' || t.status === 'failed').length
  return (
    <div className="mt-2.5 border-t border-border pt-2">
      <div className="text-[10.5px] uppercase tracking-wide text-muted mb-1">Plan · {done}/{plan.length} done</div>
      <ul className="space-y-0.5 max-h-40 overflow-auto scroll-thin">
        {plan.map((t) => {
          const s = TASK_ICON[t.status] ?? TASK_ICON.pending
          return (
            <li key={t.id} className="flex items-center gap-2 text-[12px]">
              <span className={`w-3 text-center ${s.cls}`}>{s.icon}</span>
              <span className={t.status === 'pending' ? 'text-muted/70' : t.status === 'skipped' ? 'text-muted line-through' : 'text-ink'}>{t.title}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/** The assistant is working: bouncing dots, the step it is on, the plan, and a thin shimmer line. */
function Thinking({ label, plan }: { label: string; plan: PlanTask[] | null }) {
  return (
    <div className="relative pl-9 ap-rise" role="status" aria-live="polite">
      <Avatar pulse />
      <div className="bg-surface border border-border rounded-2xl rounded-tl-md px-4 py-3 w-fit max-w-full shadow-sm">
        <div className="flex items-center gap-3">
          <span className="ap-dots" aria-hidden="true"><span /><span /><span /></span>
          <span key={label} className="ap-step-label text-[12.5px] text-muted">{label}</span>
        </div>
        <div className="ap-shimmer mt-2.5 w-44" aria-hidden="true" />
        {plan && plan.length > 0 && <PlanChecklist plan={plan} />}
      </div>
    </div>
  )
}

/** Everything the assistant did for one turn, folded into one line; open it to see each step, source and check. */
function TraceGroup({ events, live, open, onToggle }: { events: AgentEvent[]; live: boolean; open: boolean; onToggle: () => void }) {
  const steps = events.filter((e) => e.type === 'analysis_step' || e.type === 'action' || e.type === 'form_open' || e.type === 'form_patch').length
  const sources = events.filter((e) => e.type === 'evidence').length
  const verify = events.filter((e) => e.type === 'verify').at(-1)
  const failed = events.some((e) => e.type === 'error' || (e.type === 'verify' && e.data.ok === false))
  return (
    <div className="pl-9 ap-rise">
      <button onClick={onToggle} className="flex items-center gap-2 text-[11.5px] text-muted hover:text-ink transition-colors">
        <span className={`inline-block transition-transform ${open ? 'rotate-90' : ''}`}>▸</span>
        <span>{live ? 'Working' : 'How I worked'}{steps ? ` · ${steps} step${steps === 1 ? '' : 's'}` : ''}{sources ? ` · ${sources} source${sources === 1 ? '' : 's'}` : ''}</span>
        {verify && !live && <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${failed ? 'bg-danger-soft text-danger' : 'bg-success-soft text-success'}`}>{failed ? 'check failed' : '✓ screen verified'}</span>}
      </button>
      {open && (
        <div className="mt-1.5 bg-primary-deep text-white/85 font-mono text-[11px] leading-5 rounded-xl px-3 py-2 space-y-0.5 max-h-72 overflow-auto scroll-dark">
          {events.map((e, i) => e.type === 'evidence' ? <EvidenceChip key={i} e={e} /> : <Trace key={i} e={e} />)}
        </div>
      )}
    </div>
  )
}

const EXAMPLES = ['Revenue by customer state in July 2018', 'Why did revenue change in July 2018 compared to June', 'Show late deliveries in RJ last month']

type Row =
  | { kind: 'msg'; key: string; m: { text: string; note?: boolean } }
  | { kind: 'card'; key: string; e: AgentEvent }
  | { kind: 'trace'; key: string; events: AgentEvent[] }
  | { kind: 'footer'; key: string; e: AgentEvent }

function ChatTab() {
  const events = useStore((s) => s.events)
  const sid = useStore((s) => s.sessionId)
  const [text, setText] = useState('')
  const [confirmMode, setConfirmMode] = useState(false)
  const [msgs, setMsgs] = useState<{ text: string; at: number; note?: boolean }[]>([])
  const [answered, setAnswered] = useState<Record<string, 'approved' | 'declined'>>({})
  const [rated, setRated] = useState<Record<number, 'up' | 'down'>>({})
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({})
  const [pendingAt, setPendingAt] = useState<number | null>(null)
  // waiting for the assistant: from sending until its answer (or a fatal error) arrives
  const busy = pendingAt !== null && !events.some((e) => e.seq > pendingAt && (e.type === 'answer' || (e.type === 'error' && e.data.recoverable === false)))
  const end = useRef<HTMLDivElement>(null)
  const rate = async (seq: number, r: 'up' | 'down') => {
    if (!sid) return
    try { await api.feedback(sid, r, seq); setRated((x) => ({ ...x, [seq]: r })) } catch (e) { toast('error', String(e)) }
  }
  useEffect(() => { end.current?.scrollIntoView({ block: 'end', behavior: 'smooth' }) }, [events.length, msgs.length, busy])

  const send = async (override?: string) => {
    const t = (override ?? text).trim()
    if (!t || !sid) return
    const at = events.at(-1)?.seq ?? 0
    setMsgs((m) => [...m, { text: t, at }])
    if (override === undefined) setText('')
    setPendingAt(at)
    try { await api.message(sid, t, confirmMode) }
    catch (e) {
      setPendingAt(null)
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

  const rows = useMemo<Row[]>(() => {
    const items = [
      ...events.map((e) => ({ seq: e.seq, e, m: null as null | { text: string; note?: boolean } })),
      ...msgs.map((m) => ({ seq: m.at + 0.5, e: null as AgentEvent | null, m })),
    ].sort((a, b) => a.seq - b.seq)
    const out: Row[] = []
    for (const it of items) {
      if (it.m) { out.push({ kind: 'msg', key: `m${it.seq}`, m: it.m }); continue }
      const e = it.e!
      if (['answer_delta', 'status', 'frame', 'understanding'].includes(e.type)) continue
      if (e.type === 'done') { if (e.data.skill) out.push({ kind: 'footer', key: `f${e.seq}`, e }); continue }   // the executor's own 'done' is skipped
      if (e.type === 'understood' || e.type === 'answer' || e.type === 'confirm_request') { out.push({ kind: 'card', key: `c${e.seq}`, e }); continue }
      const last = out[out.length - 1]
      if (last && last.kind === 'trace') last.events.push(e)
      else out.push({ kind: 'trace', key: `t${e.seq}`, events: [e] })
    }
    return out
  }, [events, msgs])

  // the step the assistant is on right now, for the typing indicator
  const liveLabel = useMemo(() => {
    if (pendingAt === null) return ''
    const mine = events.filter((e) => e.seq > pendingAt)
    const step = [...mine].reverse().find((e) => e.type === 'analysis_step')
    if (step) return `${step.data.title}…`
    if (mine.some((e) => e.type === 'plan')) return 'Putting the answer together…'
    if (mine.some((e) => e.type === 'understood')) return 'Planning the steps…'
    return 'Understanding your request…'
  }, [events, pendingAt])

  // the analysis plan for the question being answered, with each task's current status
  const livePlan = useMemo<PlanTask[] | null>(() => {
    if (pendingAt === null) return null
    const mine = events.filter((e) => e.seq > pendingAt && e.type === 'analysis_step')
    const planEv = [...mine].reverse().find((e) => e.data.tasks)
    if (!planEv) return null
    const status: Record<string, string> = {}
    for (const e of mine) if (e.data.task && e.seq > planEv.seq) status[e.data.task.id] = e.data.task.status
    return (planEv.data.tasks as { id: string; title: string; deps: string[] }[]).map((t) => ({ ...t, status: status[t.id] ?? 'pending' }))
  }, [events, pendingAt])

  const lastTraceKey = [...rows].reverse().find((r) => r.kind === 'trace')?.key

  return (
    <div className="flex flex-col h-full min-h-0 bg-page">
      <div className="flex-1 overflow-auto scroll-thin px-4 py-4 space-y-3">
        {rows.length === 0 && (
          <div className="card ap-rise p-4 rounded-2xl!">
            <div className="text-sm font-semibold text-ink">Ask about your data, or tell me where to go</div>
            <p className="text-[12.5px] text-muted mt-1 leading-relaxed">I open the right page, set filters and dates, explain changes with sources you can re-check, and fill forms for you to confirm.</p>
            <div className="flex flex-col gap-1.5 mt-3">
              {EXAMPLES.map((q) => <button key={q} onClick={() => void send(q)} className="text-left text-[12.5px] border border-primary/20 text-primary bg-primary-mist rounded-xl px-3 py-2 hover:bg-primary-soft transition-colors">{q}</button>)}
            </div>
          </div>
        )}
        {rows.map((r) => r.kind === 'msg' ? (
          <div key={r.key} className={`ap-rise text-[13.5px] leading-relaxed px-3.5 py-2 w-fit max-w-[88%] ${r.m.note ? 'bg-warning-soft text-warning border border-warning/20 rounded-xl' : 'bg-primary text-white rounded-2xl rounded-br-md ml-auto shadow-sm'}`}>{r.m.text}</div>
        ) : r.kind === 'card' ? (
          r.e.type === 'confirm_request' ? <ConfirmCard key={r.key} e={r.e} state={answered[r.e.data.action_id]} onAnswer={(a) => answer(r.e.data.action_id, a)} />
          : r.e.type === 'understood' ? <div key={r.key} className="pl-9"><Understood e={r.e} /></div>
          : <AnswerCard key={r.key} e={r.e} onAsk={(t) => void send(t)} />
        ) : r.kind === 'trace' ? (
          <TraceGroup key={r.key} events={r.events} live={busy && r.key === lastTraceKey}
            open={openGroups[r.key] ?? (busy && r.key === lastTraceKey)} onToggle={() => setOpenGroups((g) => ({ ...g, [r.key]: !(g[r.key] ?? (busy && r.key === lastTraceKey)) }))} />
        ) : (
          <div key={r.key} className="pl-9 flex items-center justify-between text-[11px] text-muted -mt-1">
            <span>{r.e.data.steps ?? 0} step(s) · {r.e.data.elapsed_ms} ms</span>
            <span className="flex items-center gap-1.5">
              {rated[r.e.seq] ? <span>{rated[r.e.seq] === 'up' ? '👍 thanks' : '👎 noted'}</span> : (
                <><span>useful?</span>
                  <button className="hover:scale-110 transition-transform" title="Useful" onClick={() => void rate(r.e.seq, 'up')}>👍</button>
                  <button className="hover:scale-110 transition-transform" title="Not useful" onClick={() => void rate(r.e.seq, 'down')}>👎</button></>
              )}
            </span>
          </div>
        ))}
        {busy && <Thinking label={liveLabel} plan={livePlan} />}
        <div ref={end} />
      </div>
      <div className="border-t border-border bg-surface p-3 space-y-2">
        <div className="flex gap-2 items-center">
          <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void send()} placeholder="Ask the assistant…"
            className="flex-1 border border-border bg-page rounded-xl px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:bg-surface transition-colors" />
          <button onClick={() => void send()} disabled={!text.trim()} aria-label="Send" className="btn btn-primary rounded-xl! px-3.5! py-2.5!">Send ↑</button>
        </div>
        <label className="flex items-center gap-2 text-[11.5px] text-muted"><input type="checkbox" className="accent-[#1f5c45]" checked={confirmMode} onChange={(e) => setConfirmMode(e.target.checked)} />
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
      <select value={name} onChange={(e) => { setName(e.target.value); setText(JSON.stringify(PLANS[e.target.value], null, 2)) }} className="w-full border border-border rounded-lg px-2 py-1.5 text-sm bg-surface">
        {Object.keys(PLANS).map((k) => <option key={k}>{k}</option>)}
      </select>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={12} spellCheck={false} className="w-full border border-border rounded-lg p-2 font-mono text-xs" />
      <div className="flex items-center justify-between">
        <label className="flex items-center gap-2 text-xs text-muted"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> auto-approve confirmations (testing)</label>
        <button disabled={busy} onClick={() => void run()} className="btn btn-primary">{busy ? 'Running…' : 'Run plan'}</button>
      </div>
      <label className="flex items-center gap-2 text-xs text-muted">Simulated-browser fault (when no browser is attached)
        <select value={fault} onChange={(e) => { setFault(e.target.value); if (sid) void api.setFault(sid, e.target.value) }} className="border border-border rounded px-1 py-0.5">
          {['none', 'drop_filter', 'empty_widget', 'wrong_route'].map((f) => <option key={f}>{f}</option>)}
        </select>
      </label>
      {result && (
        <div className={`rounded-lg p-3 text-xs border ${result.ok ? 'bg-success-soft border-success/20' : 'bg-danger-soft border-danger/20'}`}>
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

const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`)
const num = (v: number | null | undefined, d = 0) => (v == null ? '—' : v.toFixed(d))

/** Live figures for the KPIs: effort saved (K7), speed and steps (K3), safety gates (K6), usefulness (K7). */
function MetricsTab() {
  const sid = useStore((s) => s.sessionId)
  const version = useStore((s) => s.events.length)
  const [m, setM] = useState<any>(null)
  useEffect(() => {
    if (!sid) return
    const t = setTimeout(() => { api.stats(sid).then(setM).catch(() => {}) }, 400)
    return () => clearTimeout(t)
  }, [sid, version])
  if (!m) return <div className="p-3 text-sm text-muted">Loading…</div>
  const Tile = ({ k, v, hint }: { k: string; v: string; hint?: string }) => (
    <div className="border border-border rounded-lg p-2.5" title={hint}><div className="text-[11px] text-muted">{k}</div><div className="text-lg font-semibold tabular-nums">{v}</div></div>
  )
  return (
    <div className="p-3 space-y-4 overflow-auto scroll-thin h-full">
      <p className="text-xs text-muted">Measured live for this session. These are the quantities behind the project KPIs.</p>
      <section><h4 className="text-xs font-semibold uppercase tracking-wide text-muted mb-1.5">Task success and speed (K3)</h4>
        <div className="grid grid-cols-2 gap-2">
          <Tile k="Plan runs" v={String(m.runs)} /><Tile k="Run success rate" v={pct(m.run_success_rate)} />
          <Tile k="Avg steps per run" v={num(m.avg_steps_per_run, 1)} /><Tile k="Latency p50 / p90" v={`${num(m.latency_ms.p50)} / ${num(m.latency_ms.p90)} ms`} />
        </div></section>
      <section><h4 className="text-xs font-semibold uppercase tracking-wide text-muted mb-1.5">Reliability and safety (K6)</h4>
        <div className="grid grid-cols-2 gap-2">
          <Tile k="Screen verification pass rate" v={pct(m.verify_pass_rate)} hint="Share of screen changes where the browser's acknowledgement matched the expected state" />
          <Tile k="Plans stopped by validator" v={String(m.validation_failures)} hint="Invented pages, filters, values or APIs caught before running" />
          <Tile k="Changes confirmed / declined" v={`${m.confirmations_approved} / ${m.confirmations_declined}`} />
          <Tile k="Data changes undone" v={`${m.writes_undone} of ${m.writes_applied}`} />
        </div>
        {Object.keys(m.issues_caught_by_validator).length > 0 && <div className="text-xs text-muted mt-2">Caught: {Object.entries(m.issues_caught_by_validator).map(([k, v]) => `${k} ×${v}`).join(', ')}</div>}
      </section>
      <section><h4 className="text-xs font-semibold uppercase tracking-wide text-muted mb-1.5">Usefulness (K7)</h4>
        <div className="grid grid-cols-2 gap-2">
          <Tile k="Agent steps vs manual changes" v={`${m.agent_steps} vs ${m.manual_changes}`} hint="How much navigation the assistant did instead of the user" />
          <Tile k="Share done by the assistant" v={pct(m.agent_share_of_actions)} />
          <Tile k="Helpful 👍 / 👎" v={`${m.feedback.up} / ${m.feedback.down}`} />
        </div></section>
    </div>
  )
}

export function AgentPanel({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('chat')
  const names: Record<Tab, string> = { chat: 'Assistant', runner: 'Plan runner', context: 'Context', metrics: 'Metrics' }
  return (
    <aside className="w-[440px] shrink-0 bg-surface border-l border-border flex flex-col h-full shadow-[-8px_0_24px_-16px_rgba(20,36,27,0.25)]">
      <div className="flex items-center justify-between px-4 h-14 border-b border-border shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-full bg-primary-deep text-accent grid place-items-center text-[13px]">✦</div>
          <div className="flex gap-0.5">
            {(['chat', 'runner', 'context', 'metrics'] as Tab[]).map((t) => (
              <button key={t} onClick={() => setTab(t)}
                className={`px-2.5 py-1.5 text-[13px] rounded-lg transition-colors ${tab === t ? 'bg-primary-soft text-primary font-semibold' : 'text-muted hover:bg-primary-mist hover:text-ink'}`}>{names[t]}</button>
            ))}
          </div>
        </div>
        <button onClick={onClose} className="btn btn-ghost px-2!" aria-label="Close assistant">✕</button>
      </div>
      <div className="flex-1 min-h-0">{tab === 'chat' ? <ChatTab /> : tab === 'runner' ? <RunnerTab /> : tab === 'context' ? <ContextTab /> : <MetricsTab />}</div>
    </aside>
  )
}
