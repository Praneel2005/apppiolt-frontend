/** Chart colours: an English-green family with brass and earth accents, readable together and distinguishable in greyscale. */
export const PALETTE = ['#1f5c45', '#b28a35', '#5e9a76', '#3e7c8c', '#a4543b', '#8daa91', '#6b5b3e', '#2f4f4f']

/** Neutral chart furniture, matched to the page tokens in index.css. */
export const CHART = { grid: '#e6ece4', axis: '#5d6e63', label: '#14241b', border: '#dde5db', track: '#f1f6f2' }

export const TOOLTIP = {
  contentStyle: { border: `1px solid ${CHART.border}`, borderRadius: 10, fontSize: 12, boxShadow: '0 8px 24px -12px rgba(20,36,27,.25)' },
  labelStyle: { fontWeight: 600, color: CHART.label },
}
