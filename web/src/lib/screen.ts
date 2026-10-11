/** Has the screen changed since an answer was given? Compares the page, filters and dates the answer was about with the current state. */
import type { UiState } from './types'

export interface AnswerScreen { page_id: string; filters: Record<string, string[]>; date_range: [string, string] | null; depends: boolean }

const norm = (page: string, filters: Record<string, string[]>, range: [string, string] | null) =>
  JSON.stringify([page, Object.keys(filters).sort().map((k) => [k, [...filters[k]].sort()]), range])

export function screenOf(state: UiState | null): string {
  if (!state) return ''
  const filters = Object.fromEntries(Object.entries(state.filters ?? {}).map(([k, v]) => [k, v.value.map(String)]))
  return norm(state.page_id, filters, state.date_range ? [state.date_range.from, state.date_range.to] : null)
}

export const answerScreen = (s: AnswerScreen) => norm(s.page_id, s.filters, s.date_range)

/** True when the answer was about the screen and the screen is now different. */
export const screenChanged = (s: AnswerScreen | undefined, state: UiState | null) =>
  !!s && s.depends && !!state && answerScreen(s) !== screenOf(state)
