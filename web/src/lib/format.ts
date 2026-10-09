import type { Column } from './types'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function formatMonth(raw: string): string {
  const m = /^(\d{4})-(\d{2})/.exec(raw)
  return m ? `${MONTHS[parseInt(m[2], 10) - 1] ?? m[2]} ${m[1]}` : raw
}

export function formatMoney(n: number): string {
  const abs = Math.abs(n)
  const body = abs >= 1000 ? abs.toLocaleString('en-US', { maximumFractionDigits: 0 })
    : abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return (n < 0 ? '-' : '') + 'R$ ' + body
}

/** Format a metric value by its semantic-layer unit (BRL, count, days, ratio, score). */
export function formatUnit(value: unknown, unit: string): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = typeof value === 'number' ? value : Number(value)
  if (Number.isNaN(n)) return String(value)
  switch (unit) {
    case 'BRL': return formatMoney(n)
    case 'ratio': return (n * 100).toFixed(1) + '%'
    case 'days': return n.toFixed(1) + ' days'
    case 'count': return Math.round(n).toLocaleString('en-US')
    case 'score': return n.toFixed(2)
    default: return n.toLocaleString('en-US', { maximumFractionDigits: 2 })
  }
}

/** Compact axis label: 1.2M, 340k. */
export function formatCompact(n: number, unit: string): string {
  if (unit === 'ratio') return (n * 100).toFixed(0) + '%'
  const abs = Math.abs(n)
  const s = abs >= 1e6 ? (abs / 1e6).toFixed(1) + 'M' : abs >= 1e3 ? (abs / 1e3).toFixed(0) + 'k' : String(Math.round(abs * 100) / 100)
  return (n < 0 ? '-' : '') + (unit === 'BRL' ? 'R$ ' : '') + s
}

/** Format a table cell by the declared column type. */
export function formatCell(value: unknown, col: Pick<Column, 'type' | 'unit' | 'name'>): string {
  if (value === null || value === undefined || value === '') return '—'
  switch (col.type) {
    case 'money': return formatMoney(Number(value))
    case 'percent': return Number(value).toFixed(1) + '%'
    case 'ratio': return (Number(value) * 100).toFixed(1) + '%'
    case 'number': {
      const n = Number(value)
      return Number.isInteger(n) ? n.toLocaleString('en-US') : n.toLocaleString('en-US', { maximumFractionDigits: 2 })
    }
    case 'date': return String(value).slice(0, 10)
    case 'datetime': return String(value).replace('T', ' ').slice(0, 16)
    case 'list': return Array.isArray(value) ? value.join(', ') : String(value)
    default: return col.name === 'order_month' ? formatMonth(String(value)) : String(value)
  }
}

export function humanize(s: string): string {
  return s.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
}
