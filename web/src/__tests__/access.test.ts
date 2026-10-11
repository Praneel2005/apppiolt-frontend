import { describe, expect, it } from 'vitest'
import { canOpen, canWrite, scopeText } from '../lib/access'
import type { Page, Role } from '../lib/types'

const page = (directory: string) => ({ directory } as Page)
const role = (r: Partial<Role>): Role => ({ id: 'x', title: 'X', description: '', modules: '*', writes: '*', reads: '*', scope: {}, ...r })

describe('what a role may open and do', () => {
  it('lets an unrestricted role (or no role yet) open and change everything', () => {
    expect(canOpen(role({}), page('ops_orders'))).toBe(true)
    expect(canOpen(null, page('ops_orders'))).toBe(true)
    expect(canWrite(role({}), 'tickets.create')).toBe(true)
  })

  it('limits a role to its modules, taking the module from the first part of the page directory', () => {
    const analyst = role({ modules: ['sales', 'customers'], writes: [] })
    expect(canOpen(analyst, page('sales'))).toBe(true)
    expect(canOpen(analyst, page('sales/revenue'))).toBe(true)
    expect(canOpen(analyst, page('ops_orders'))).toBe(false)
  })

  it('allows only the listed write APIs, and none for a read-only role', () => {
    expect(canWrite(role({ writes: [] }), 'tickets.create')).toBe(false)
    expect(canWrite(role({ writes: ['tickets.create'] }), 'tickets.create')).toBe(true)
    expect(canWrite(role({ writes: ['tickets.create'] }), 'promotions.create')).toBe(false)
  })

  it('describes the data scope in words', () => {
    expect(scopeText(role({ scope: { customer_region: ['South'] } }))).toBe('customer region = South')
    expect(scopeText(role({}))).toBe('')
  })
})
