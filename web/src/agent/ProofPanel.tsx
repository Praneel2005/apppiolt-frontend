/** "How was this computed?": every claim in an answer, the evidence behind it, the exact governed call, and a replay check. */
import { useState } from 'react'
import type { Claim, Proof, Replay } from '../lib/types'
import { api } from '../net/api'
import { openUrl } from '../state/session'
import { useStore } from '../state/store'
import { highlightWidget } from '../widgets/widgetBase'

const SUPPORT: Record<string, { label: string; cls: string; title: string }> = {
  direct: { label: 'in evidence', cls: 'text-emerald-700 bg-emerald-50 border-emerald-200', title: 'This number appears in the recorded result.' },
  derived: { label: 'derived', cls: 'text-amber-700 bg-amber-50 border-amber-200', title: 'Computed from evidence numbers (a difference, share or ratio).' },
  none: { label: 'no evidence', cls: 'text-slate-600 bg-slate-50 border-slate-200', title: 'This statement carries no recorded evidence.' },
}

const short = (v: unknown, n = 140) => {
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  return s && s.length > n ? s.slice(0, n) + '…' : s ?? ''
}

function Request({ req }: { req: Record<string, unknown> }) {
  const rows = Object.entries(req).filter(([, v]) => v !== null && v !== undefined && !(Array.isArray(v) && v.length === 0) &&
    !(typeof v === 'object' && !Array.isArray(v) && Object.keys(v as object).length === 0))
  return (
    <div className="font-mono text-[10px] text-slate-600 bg-page border border-border rounded p-1.5 space-y-0.5">
      {rows.map(([k, v]) => <div key={k}><span className="text-slate-400">{k}</span> {short(v, 90)}</div>)}
    </div>
  )
}

function EvidenceProof({ sid, id }: { sid: string; id: string }) {
  const [proof, setProof] = useState<Proof | null | 'missing'>(null)
  const [replay, setReplay] = useState<Replay | 'running' | null>(null)
  const load = async () => {
    try { setProof(await api.proof(sid, id)) } catch { setProof('missing') }
  }
  const run = async () => {
    setReplay('running')
    try { setReplay(await api.replay(sid, id)) } catch (e) { setReplay({ ok: false, message: String(e) }) }
  }
  if (proof === null) return <button className="text-[11px] text-primary underline" onClick={load}>{id}: show how it was computed</button>
  if (proof === 'missing') return <div className="text-[11px] text-muted">{id}: no recorded proof.</div>
  const view = proof.view
  return (
    <div className="border border-border rounded-lg p-2 space-y-1.5 bg-white">
      <div className="text-[11px]"><b className="font-mono text-primary">{proof.evidence_id}</b> · {proof.metric.title ?? proof.source} · {proof.kind.replace('_', ' ')}</div>
      {proof.metric.definition && <div className="text-[11px] text-muted">Definition: {proof.metric.definition}</div>}
      <div className="text-[11px] text-muted">
        {proof.period ? <>Period {short(proof.period, 100)}. </> : null}
        {proof.row_count != null ? <>{proof.row_count} row(s). </> : null}
        {proof.data_layers.length ? <>Data: {proof.data_layers.join(', ')}. </> : null}
      </div>
      {proof.call
        ? <><div className="text-[10px] uppercase tracking-wide text-slate-400">Governed call · {proof.call.op}</div><Request req={proof.call.request} /></>
        : <div className="text-[11px] text-muted">Combines {proof.calls.length} governed queries; each is listed below.
          {proof.calls.map((c, i) => <div key={i} className="mt-1"><Request req={{ op: c.op, ...c.request }} /></div>)}</div>}
      <div className="flex flex-wrap items-center gap-3">
        {proof.exact && <button className="text-[11px] text-primary underline" onClick={run} disabled={replay === 'running'}>{replay === 'running' ? 'Re-running…' : 'Replay this query'}</button>}
        {view?.url && <button className="text-[11px] text-primary underline" onClick={() => { openUrl(view.url!); setTimeout(() => view.widget_id && highlightWidget(view.widget_id), 900) }}>Show on screen{view.page_code ? ` (${view.page_code})` : ''}</button>}
      </div>
      {replay && replay !== 'running' && (
        <div className={`text-[11px] rounded px-2 py-1 border ${replay.ok && replay.match ? 'text-emerald-700 bg-emerald-50 border-emerald-200' : 'text-amber-800 bg-amber-50 border-amber-200'}`}>
          {replay.ok && replay.match ? '✓ ' : '• '}{replay.message}{replay.checked ? ` (${replay.checked} values compared)` : ''}
          {replay.differences?.map((d) => <div key={d.path} className="font-mono">{d.path}: {d.stored} → {d.now}</div>)}
        </div>
      )}
    </div>
  )
}

export function ProofPanel({ claims, citations }: { claims: Claim[]; citations: string[] }) {
  const sid = useStore((s) => s.sessionId)
  const [open, setOpen] = useState(false)
  if (!sid || (claims.length === 0 && citations.length === 0)) return null
  const ids = [...new Set([...citations, ...claims.flatMap((c) => c.evidence)])]
  return (
    <div>
      <button className="text-[11px] text-slate-500 hover:text-ink underline decoration-dotted" onClick={() => setOpen(!open)}>
        {open ? 'Hide' : 'How was this computed?'}
      </button>
      {open && (
        <div className="mt-1.5 space-y-2">
          {claims.length > 0 && (
            <div className="space-y-1">
              <div className="text-[10px] uppercase tracking-wide text-slate-400">Claims and their evidence</div>
              {claims.map((c, i) => {
                const worst = Object.values(c.support).includes('none') ? 'none' : Object.values(c.support).includes('derived') ? 'derived' : 'direct'
                const s = SUPPORT[worst]
                return (
                  <div key={i} className="text-[11px] flex items-start gap-1.5">
                    <span title={s.title} className={`shrink-0 border rounded px-1 py-px text-[9px] ${s.cls}`}>{s.label}</span>
                    <span className="text-ink">{c.text.replace(/\*\*/g, '')} <span className="font-mono text-primary">{c.evidence.join(' ')}</span></span>
                  </div>
                )
              })}
            </div>
          )}
          <div className="space-y-1.5">{ids.map((id) => <EvidenceProof key={id} sid={sid} id={id} />)}</div>
        </div>
      )}
    </div>
  )
}
