import { useMemo, useState } from 'react'
import type { Application, Module, Page } from '../lib/types'
import { goToPage } from '../state/session'
import { useStore } from '../state/store'

function PageLink({ page, active }: { page: Page; active: boolean }) {
  return (
    <a href={page.route} onClick={(e) => { if (e.metaKey || e.ctrlKey) return; e.preventDefault(); goToPage(page.page_id) }}
      className={`flex items-center justify-between gap-2 px-3 py-1.5 rounded-md text-sm ${active ? 'bg-primary-soft text-primary font-medium' : 'text-ink hover:bg-page'}`}>
      <span className="truncate">{page.title}</span>
      {page.page_code && <span className="text-[10px] font-mono text-muted shrink-0">{page.page_code}</span>}
    </a>
  )
}

function dirTitle(app: Application, path: string, mod: Module): string {
  const leaf = path.split('/')[1]
  return leaf ? leaf.charAt(0).toUpperCase() + leaf.slice(1) : mod.title
}

export function LeftNav() {
  const app = useStore((s) => s.app)!
  const activeId = useStore((s) => s.state?.page_id)
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [reportsOpen, setReportsOpen] = useState(false)

  const byModule = useMemo(() => {
    const m = new Map<string, Page[]>()
    for (const p of app.pages) {
      const key = p.directory.split('/')[0]
      m.set(key, [...(m.get(key) ?? []), p])
    }
    return m
  }, [app])

  const term = q.trim().toLowerCase()
  const match = (p: Page) => !term || `${p.title} ${p.page_code} ${p.keywords.join(' ')}`.toLowerCase().includes(term)
  const ops = app.modules.filter((m) => m.section === 'operations')
  const reports = app.modules.filter((m) => m.section === 'reports')
  const activeModule = app.pages.find((p) => p.page_id === activeId)?.directory.split('/')[0]

  return (
    <aside className="w-64 shrink-0 bg-surface border-r border-border flex flex-col h-full">
      <div className="px-4 py-4 border-b border-border">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-primary grid place-items-center text-white text-sm font-bold">A</div>
          <div><div className="text-sm font-semibold leading-4">AppPilot</div><div className="text-[11px] text-muted leading-4">Olist Seller Operations</div></div>
        </div>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a page…  (e.g. P-300, tickets)"
          className="mt-3 w-full border border-border rounded-lg px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30" />
      </div>
      <nav className="flex-1 overflow-auto scroll-thin px-2 py-2 space-y-3">
        {ops.map((m) => {
          const pages = (byModule.get(m.module_id) ?? []).filter(match)
          if (!pages.length) return null
          return (
            <div key={m.module_id}>
              <div className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">{m.title}</div>
              {pages.map((p) => <PageLink key={p.page_id} page={p} active={p.page_id === activeId} />)}
            </div>
          )
        })}
        <div>
          <button className="w-full px-3 pb-1 flex items-center justify-between text-[11px] font-semibold uppercase tracking-wide text-muted"
            onClick={() => setReportsOpen(!reportsOpen)}>
            <span>Reports library</span><span>{reportsOpen || term ? '▾' : '▸'}</span>
          </button>
          {(reportsOpen || term || reports.some((m) => m.module_id === activeModule)) && reports.map((m) => {
            const pages = (byModule.get(m.module_id) ?? []).filter(match)
            if (!pages.length) return null
            const isOpen = open[m.module_id] ?? (m.module_id === activeModule || !!term)
            return (
              <div key={m.module_id} className="mb-1">
                <button onClick={() => setOpen({ ...open, [m.module_id]: !isOpen })}
                  className="w-full flex items-center justify-between px-3 py-1.5 text-sm font-medium text-ink hover:bg-page rounded-md">
                  <span>{m.title}</span><span className="text-xs text-muted">{pages.length} {isOpen ? '▾' : '▸'}</span>
                </button>
                {isOpen && (
                  <div className="ml-2 border-l border-border pl-1">
                    {['', 'breakdowns', 'trends'].map((sub) => {
                      const ps = pages.filter((p) => (p.directory.split('/')[1] ?? '') === sub)
                      if (!ps.length) return null
                      return (
                        <div key={sub} className="mb-1">
                          {sub && <div className="px-3 pt-1 text-[10px] uppercase tracking-wide text-muted">{dirTitle(app, `${m.module_id}/${sub}`, m)}</div>}
                          {ps.map((p) => <PageLink key={p.page_id} page={p} active={p.page_id === activeId} />)}
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </nav>
    </aside>
  )
}
