import { useEffect, useMemo, useState } from 'react'
import type { JsonSchema, RowChange, WriteResult } from '../lib/types'
import { humanize } from '../lib/format'
import { api, ApiError } from '../net/api'
import { applyWrite, useActionDialog } from './runner'

type Prop = NonNullable<JsonSchema['properties']>[string]

interface Field { name: string; label: string; kind: 'text' | 'textarea' | 'number' | 'integer' | 'boolean' | 'select' | 'date' | 'list'; required: boolean; options?: string[]; hint?: string; def?: unknown }

const TEXTAREA = new Set(['reply_text', 'notes', 'note', 'subject', 'add_note'])

function propType(p: Prop): string {
  const t = p.type ?? p.anyOf?.map((x) => x.type).find((x) => x && x !== 'null')
  return Array.isArray(t) ? String(t.find((x) => x !== 'null')) : String(t ?? 'string')
}

export function fieldsFor(entry: { parameters: { name: string; in: string; required: boolean; description?: string; enum?: string[] }[]; body_schema: JsonSchema | null },
                          locked: Record<string, unknown>): Field[] {
  const out: Field[] = []
  for (const p of entry.parameters.filter((x) => x.in === 'path' && !(x.name in locked))) {
    out.push({ name: p.name, label: humanize(p.name), kind: 'text', required: true, hint: p.description })
  }
  const props = entry.body_schema?.properties ?? {}
  const required = new Set(entry.body_schema?.required ?? [])
  for (const [name, p] of Object.entries(props)) {
    if (name in locked) continue
    const t = propType(p)
    const enumVals = (p.enum ?? (p.anyOf as any[] | undefined)?.find((x) => x.enum)?.enum) as unknown[] | undefined
    const kind: Field['kind'] = enumVals ? 'select' : t === 'boolean' ? 'boolean' : t === 'integer' ? 'integer'
      : t === 'number' ? 'number' : t === 'array' ? 'list' : /date/.test(name) || p.format === 'date' ? 'date'
      : TEXTAREA.has(name) ? 'textarea' : 'text'
    out.push({ name, label: p.title ?? humanize(name), kind, required: required.has(name), options: enumVals?.map(String),
              hint: (p as any).description, def: p.default })
  }
  return out
}

function coerce(f: Field, raw: unknown): unknown {
  if (raw === '' || raw === undefined || raw === null) return undefined
  if (f.kind === 'integer') return parseInt(String(raw), 10)
  if (f.kind === 'number') return parseFloat(String(raw))
  if (f.kind === 'list') return String(raw).split(',').map((s) => s.trim()).filter(Boolean)
  return raw
}

export function ActionDialog() {
  const { req, close } = useActionDialog()
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [step, setStep] = useState<'input' | 'loading' | 'preview' | 'applying'>('input')
  const [preview, setPreview] = useState<WriteResult | null>(null)
  const [error, setError] = useState<ApiError | string | null>(null)

  const fields = useMemo(() => (req ? fieldsFor(req.entry, req.locked) : []), [req])

  const dryRun = async (vals: Record<string, unknown>) => {
    if (!req) return
    setStep('loading'); setError(null)
    try {
      const all = { ...req.locked, ...Object.fromEntries(fields.map((f) => [f.name, coerce(f, vals[f.name])]).filter(([, v]) => v !== undefined)) }
      setPreview(await api.callWrite(req.entry, all, true))
      setStep('preview')
    } catch (e) {
      setError(e instanceof ApiError ? e : String(e)); setStep('input')
    }
  }

  useEffect(() => {
    if (!req) return
    const init = { ...Object.fromEntries(fields.filter((f) => f.def !== undefined && f.def !== null).map((f) => [f.name, f.def])), ...(req.initial ?? {}) }
    setValues(init); setPreview(null); setError(null)
    const missing = fields.some((f) => f.required)
    if (!req.form && !missing) void dryRun(init)  // everything is known: go straight to the preview
    else setStep('input')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [req])

  if (!req) return null

  const confirm = async () => {
    setStep('applying'); setError(null)
    try {
      const all = { ...req.locked, ...Object.fromEntries(fields.map((f) => [f.name, coerce(f, values[f.name])]).filter(([, v]) => v !== undefined)) }
      await applyWrite(req, all)
      close()
    } catch (e) {
      setError(e instanceof ApiError ? e : String(e)); setStep('preview')
    }
  }

  const missing = fields.filter((f) => f.required && (values[f.name] === undefined || values[f.name] === ''))
  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 flex items-center justify-center p-4" onClick={close}>
      <div className="bg-surface rounded-xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="px-5 pt-4 pb-3 border-b border-border">
          <div className="text-[11px] uppercase tracking-wide text-muted">{req.entry.entity.replace('_', ' ')} · {req.entry.api_id}</div>
          <h2 className="text-base font-semibold text-ink">{req.label}</h2>
          {req.description && <p className="text-xs text-muted mt-0.5">{req.description}</p>}
        </div>
        <div className="px-5 py-4 space-y-3">
          {error && <ErrorBox error={error} />}
          {step === 'input' && (
            <>
              {fields.length === 0 && <p className="text-sm text-muted">No further input needed.</p>}
              {fields.map((f) => <FieldInput key={f.name} f={f} value={values[f.name]} onChange={(v) => setValues({ ...values, [f.name]: v })} />)}
              {Object.keys(req.locked).length > 0 && (
                <div className="text-xs text-muted bg-page border border-border rounded-lg p-2">
                  Already set: {Object.entries(req.locked).map(([k, v]) => <span key={k} className="mr-2"><b>{humanize(k)}</b> {String(v)}</span>)}
                </div>
              )}
            </>
          )}
          {step === 'loading' && <p className="text-sm text-muted">Checking what would change…</p>}
          {(step === 'preview' || step === 'applying') && preview && <Preview res={preview} />}
        </div>
        <div className="px-5 py-3 border-t border-border flex items-center justify-between gap-2">
          <span className="text-[11px] text-muted">{step === 'preview' ? 'Nothing is saved until you confirm.' : req.entry.undoable ? 'Can be undone afterwards.' : ''}</span>
          <div className="flex gap-2">
            <button className="px-3 py-1.5 text-sm rounded-lg border border-border text-ink hover:bg-page" onClick={close}>Cancel</button>
            {step === 'input' && <button disabled={missing.length > 0} className="px-3 py-1.5 text-sm rounded-lg bg-primary text-white hover:bg-primary-hover" onClick={() => dryRun(values)}>Preview</button>}
            {(step === 'preview' || step === 'applying') && (
              <>
                {req.form || fields.length > 0 ? <button className="px-3 py-1.5 text-sm rounded-lg border border-border hover:bg-page" onClick={() => setStep('input')} disabled={step === 'applying'}>Back</button> : null}
                <button disabled={step === 'applying'} onClick={confirm}
                  className={`px-3 py-1.5 text-sm rounded-lg text-white ${req.style === 'danger' ? 'bg-danger hover:bg-red-700' : 'bg-primary hover:bg-primary-hover'}`}>
                  {step === 'applying' ? 'Saving…' : 'Confirm'}
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function FieldInput({ f, value, onChange }: { f: Field; value: unknown; onChange: (v: unknown) => void }) {
  const base = 'w-full border border-border rounded-lg px-2.5 py-1.5 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-primary/30'
  return (
    <label className="block">
      <span className="text-xs font-medium text-ink">{f.label}{f.required && <span className="text-danger"> *</span>}</span>
      {f.kind === 'select' ? (
        <select className={base} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}>
          <option value="">{f.required ? 'Choose…' : '—'}</option>
          {f.options!.map((o) => <option key={o} value={o}>{humanize(o)}</option>)}
        </select>
      ) : f.kind === 'textarea' ? (
        <textarea className={base} rows={3} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} />
      ) : f.kind === 'boolean' ? (
        <input type="checkbox" className="ml-2 align-middle" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
      ) : (
        <input className={base} type={f.kind === 'number' || f.kind === 'integer' ? 'number' : f.kind === 'date' ? 'date' : 'text'}
          step={f.kind === 'number' ? 'any' : undefined} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}
          placeholder={f.kind === 'list' ? 'comma-separated' : undefined} />
      )}
      {f.hint && <span className="text-[11px] text-muted">{f.hint}</span>}
    </label>
  )
}

function ErrorBox({ error }: { error: ApiError | string }) {
  const e = typeof error === 'string' ? null : error
  return (
    <div className="text-sm text-danger bg-red-50 border border-red-100 rounded-lg p-3">
      <div className="font-medium">{e ? e.message : error as string}</div>
      {e?.body.did_you_mean?.length ? <div className="text-xs mt-1">Did you mean: {e.body.did_you_mean.join(', ')}?</div> : null}
      {e?.body.allowed?.length ? <div className="text-xs mt-1">Allowed: {e.body.allowed.slice(0, 12).join(', ')}</div> : null}
      {e?.body.candidates?.length ? <div className="text-xs mt-1">Matches: {e.body.candidates.join(', ')}</div> : null}
    </div>
  )
}

const SHOW_FIRST = ['code', 'status', 'priority', 'category', 'quantity', 'product_id', 'order_id', 'seller_id', 'reason', 'severity',
  'list_price', 'new_price', 'old_price', 'discount_pct', 'name', 'stock_quantity', 'reply_text', 'subject', 'assignee']

export function Preview({ res }: { res: WriteResult }) {
  return (
    <div className="space-y-3">
      <div className="text-xs font-medium text-warning bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
        Preview: {res.changes.length} {res.changes.length === 1 ? 'record' : 'records'} would change
      </div>
      {res.changes.map((c, i) => <ChangeCard key={i} c={c} />)}
      {Array.isArray((res.result as any)?.skipped) && (res.result as any).skipped.length > 0 && (
        <div className="text-xs text-muted">Skipped: {(res.result as any).skipped.map((s: any) => `${s.product_id ?? s.order ?? ''} (${s.reason})`).join('; ')}</div>
      )}
    </div>
  )
}

function ChangeCard({ c }: { c: RowChange }) {
  const keys = c.op === 'update' ? (c.changed ?? []) : [...SHOW_FIRST.filter((k) => k in c.after), ...Object.keys(c.after).filter((k) => !SHOW_FIRST.includes(k))].slice(0, 8)
  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <div className="bg-page px-3 py-1.5 text-xs text-muted flex justify-between">
        <span><b className="text-ink">{c.op === 'insert' ? 'New' : 'Update'}</b> · {c.table.replace('ops_', '').replace('_', ' ')}</span>
        <span className="font-mono">{String((c.after as any).code ?? c.pk)}</span>
      </div>
      <table className="w-full text-xs">
        <tbody>
          {keys.map((k) => (
            <tr key={k} className="border-t border-border">
              <td className="px-3 py-1 text-muted w-1/3">{humanize(k)}</td>
              <td className="px-3 py-1">
                {c.op === 'update' ? <><span className="text-muted line-through">{fmt(c.before?.[k])}</span> → <b>{fmt(c.after[k])}</b></> : <b>{fmt(c.after[k])}</b>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
const fmt = (v: unknown) => (v === null || v === undefined ? '—' : String(v))
