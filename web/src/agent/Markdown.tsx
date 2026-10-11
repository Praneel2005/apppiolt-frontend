/** Tiny, dependency-free markdown for assistant answers: **bold**, *italic*, `code`, [E1] citations, "- " and "1. " lists,
 *  "> " quotes. It never injects HTML (everything is rendered as React text), so answers containing data are safe. */
import type { ReactNode } from 'react'

const TOKEN = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[E\d+(?:,\s*E\d+)*\])/g

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  let i = 0
  for (const m of text.matchAll(TOKEN)) {
    const idx = m.index ?? 0
    if (idx > last) out.push(text.slice(last, idx))
    const t = m[0]
    const k = `${key}-${i++}`
    if (t.startsWith('**')) out.push(<strong key={k}>{t.slice(2, -2)}</strong>)
    else if (t.startsWith('`')) out.push(<code key={k} className="bg-page rounded px-1 text-[12px]">{t.slice(1, -1)}</code>)
    else if (t.startsWith('[E')) out.push(<span key={k} className="font-mono text-[10px] text-primary bg-primary-soft rounded px-1 mx-0.5">{t.slice(1, -1)}</span>)
    else out.push(<em key={k}>{t.slice(1, -1)}</em>)
    last = idx + t.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  let list: { ordered: boolean; items: string[] } | null = null
  const flush = () => {
    if (!list) return
    const n = blocks.length
    const items = list.items.map((t, j) => <li key={j}>{inline(t, `l${n}-${j}`)}</li>)
    blocks.push(list.ordered
      ? <ol key={`b${n}`} className="list-decimal pl-5 space-y-0.5">{items}</ol>
      : <ul key={`b${n}`} className="list-disc pl-5 space-y-0.5">{items}</ul>)
    list = null
  }
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd()
    const bullet = /^\s*[-•]\s+(.*)$/.exec(line)
    const ordered = /^\s*\d+\.\s+(.*)$/.exec(line)
    if (bullet) {
      if (!list || list.ordered) { flush(); list = { ordered: false, items: [] } }
      list.items.push(bullet[1])
    } else if (ordered) {
      if (!list || !list.ordered) { flush(); list = { ordered: true, items: [] } }
      list.items.push(ordered[1])
    } else {
      flush()
      if (!line.trim()) continue
      const n = blocks.length
      if (line.startsWith('>')) blocks.push(<div key={`b${n}`} className="border-l-2 border-warning pl-2 text-[13px] text-muted">{inline(line.replace(/^>\s?/, ''), `q${n}`)}</div>)
      else blocks.push(<p key={`b${n}`}>{inline(line, `p${n}`)}</p>)
    }
  }
  flush()
  return <div className="space-y-1.5 text-sm leading-relaxed">{blocks}</div>
}
