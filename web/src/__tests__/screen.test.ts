import { describe, expect, it } from 'vitest'
import { screenChanged } from '../lib/screen'
import type { UiState } from '../lib/types'

const state = (page: string, filters: Record<string, string[]>, range: [string, string] | null): UiState => ({
  route: '/', page_id: page, version: 1, selected_widget: null, sort: null,
  filters: Object.fromEntries(Object.entries(filters).map(([k, v]) => [k, { op: 'in', value: v }])),
  date_range: range ? { from: range[0], to: range[1], preset: null } : null,
} as unknown as UiState)

const answer = { page_id: 'p', filters: { customer_state: ['MG', 'RJ'] }, date_range: ['2018-07-01', '2018-07-31'] as [string, string], depends: true }

describe('an answer remembers its screen', () => {
  it('is current while the screen is the same, whatever the order of filter values', () => {
    expect(screenChanged(answer, state('p', { customer_state: ['RJ', 'MG'] }, ['2018-07-01', '2018-07-31']))).toBe(false)
  })
  it('is out of date when the page, a filter or the dates change', () => {
    expect(screenChanged(answer, state('q', { customer_state: ['MG', 'RJ'] }, ['2018-07-01', '2018-07-31']))).toBe(true)
    expect(screenChanged(answer, state('p', { customer_state: ['SP'] }, ['2018-07-01', '2018-07-31']))).toBe(true)
    expect(screenChanged(answer, state('p', { customer_state: ['MG', 'RJ'] }, ['2018-08-01', '2018-08-31']))).toBe(true)
  })
  it('never flags an answer that was not about the screen', () => {
    expect(screenChanged({ ...answer, depends: false }, state('other', {}, null))).toBe(false)
    expect(screenChanged(undefined, state('p', {}, null))).toBe(false)
  })
})
