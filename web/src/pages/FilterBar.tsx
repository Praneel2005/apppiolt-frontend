import { useEffect, useMemo, useRef, useState } from 'react'
import { humanize } from '../lib/format'
import type { FilterDef, FilterValue, Page } from '../lib/types'
import { clearFilters, setDateRange, setFilter } from '../state/session'
import { useStore } from '../state/store'

function MultiSelect({ f, value, onChange }: { f: FilterDef; value: string[]; onChange: (v: string[]) => void }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  const options = useMemo(() => (f.allowed_values ?? []).filter((v) => v.toLowerCase().includes(q.toLowerCase())), [f, q])
  const label = value.length === 0 ? 'Any' : value.length <= 2 ? value.join(', ') : `${value.length} selected`
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(!open)} className={`border rounded-lg px-2.5 py-1.5 text-sm bg-white flex items-center gap-2 ${value.length ? 'border-primary text-primary' : 'border-border text-ink'}`}>
        <span className="text-muted text-xs">{f.title}</span><span className="font-medium max-w-48 truncate">{label}</span><span className="text-muted text-xs">▾</span>
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-64 bg-white border border-border rounded-lg shadow-lg">
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…" className="w-full px-3 py-2 text-sm border-b border-border focus:outline-none rounded-t-lg" />
          <div className="max-h-64 overflow-auto scroll-thin p-1">
            {options.map((v) => (
              <label key={v} className="flex items-center gap-2 px-2 py-1 text-sm rounded hover:bg-page cursor-pointer">
                <input type="checkbox" checked={value.includes(v)} onChange={() => onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v])} />
                <span className="truncate">{v}</span>
              </label>
            ))}
            {options.length === 0 && <div className="px-3 py-2 text-xs text-muted">No match</div>}
          </div>
          {value.length > 0 && <button className="w-full text-xs text-primary py-2 border-t border-border hover:bg-page rounded-b-lg" onClick={() => onChange([])}>Clear</button>}
        </div>
      )}
    </div>
  )
}

function DateFilter({ f }: { f: FilterDef }) {
  const range = useStore((s) => s.state?.date_range ?? null)
  const presets = useStore((s) => s.presets)
  const preset = range?.preset && presets[range.preset] ? range.preset : range ? 'custom' : ''
  return (
    <div className="flex items-center gap-1.5 border border-border rounded-lg bg-white px-2 py-1">
      <span className="text-muted text-xs">{f.title}</span>
      <select className="text-sm font-medium bg-transparent focus:outline-none" value={preset}
        onChange={(e) => {
          const v = e.target.value
          if (v === '') setDateRange(null)
          else if (v === 'custom') setDateRange(range ?? { from: presets.last_30_days?.from ?? '', to: presets.last_30_days?.to ?? '', preset: null })
          else setDateRange({ ...presets[v], preset: v })
        }}>
        <option value="">All time</option>
        {Object.keys(presets).map((p) => <option key={p} value={p}>{humanize(p)}</option>)}
        <option value="custom">Custom…</option>
      </select>
      {range && (
        <>
          <input type="date" className="text-sm border border-border rounded px-1" value={range.from} onChange={(e) => e.target.value && setDateRange({ from: e.target.value, to: range.to, preset: null })} />
          <span className="text-muted">–</span>
          <input type="date" className="text-sm border border-border rounded px-1" value={range.to} onChange={(e) => e.target.value && setDateRange({ from: range.from, to: e.target.value, preset: null })} />
        </>
      )}
    </div>
  )
}

export function FilterBar({ page }: { page: Page }) {
  const filters = useStore((s) => s.state?.filters ?? {})
  const active = Object.keys(filters).length
  if (page.filters.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-2">
      {page.filters.map((f) => {
        const cur: FilterValue | undefined = filters[f.filter_id]
        if (f.type === 'date_range') return <DateFilter key={f.filter_id} f={f} />
        if (f.type === 'multiselect') {
          return <MultiSelect key={f.filter_id} f={f} value={cur?.value ?? []} onChange={(v) => setFilter(f.filter_id, v.length ? { op: 'in', value: v } : null)} />
        }
        return (
          <label key={f.filter_id} className={`border rounded-lg px-2.5 py-1.5 text-sm bg-white flex items-center gap-2 ${cur ? 'border-primary text-primary' : 'border-border'}`}>
            <span className="text-muted text-xs">{f.title}</span>
            <select className="font-medium bg-transparent focus:outline-none" value={cur?.value[0] ?? ''}
              onChange={(e) => setFilter(f.filter_id, e.target.value ? { op: 'eq', value: [e.target.value] } : null)}>
              <option value="">Any</option>
              {(f.allowed_values ?? []).map((v) => <option key={v} value={v}>{humanize(v)}</option>)}
            </select>
          </label>
        )
      })}
      {active > 0 && <button onClick={clearFilters} className="text-xs text-muted hover:text-danger underline">Clear filters</button>}
    </div>
  )
}
