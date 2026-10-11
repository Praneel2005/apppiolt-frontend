import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Markdown } from '../agent/Markdown'

const html = (t: string) => renderToStaticMarkup(<Markdown text={t} />)

describe('assistant answer formatting', () => {
  it('renders bold, citations, lists and quotes', () => {
    const out = html('**Revenue fell 3.0%** in July [E1, E2]\n> You mentioned a rise.\n- first\n- second\n1. one\n2. two')
    expect(out).toContain('<strong>Revenue fell 3.0%</strong>')
    expect(out).toContain('E1, E2')
    expect(out).toContain('border-l-2')
    expect(out.match(/<li>/g)?.length).toBe(4)
    expect(out).toContain('<ul')
    expect(out).toContain('<ol')
  })

  it('never injects HTML from the text', () => {
    const out = html('<img src=x onerror=alert(1)> and **<b>bold</b>**')
    expect(out).not.toContain('<img')
    expect(out).toContain('&lt;img')
    expect(out).not.toContain('<b>bold</b>')
  })

  it('keeps blank lines out and handles italics', () => {
    const out = html('*Assumptions: last month.*\n\n\nDone.')
    expect(out).toContain('<em>Assumptions: last month.</em>')
    expect(out.match(/<p>/g)?.length).toBe(2)
  })
})
