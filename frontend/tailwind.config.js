/** Institutional terminal palette. Neutral-first, one accent, semantic status only. */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'media',
  theme: {
    extend: {
      colors: {
        bg: 'rgb(var(--bg) / <alpha-value>)',
        surface: 'rgb(var(--surface) / <alpha-value>)',
        raised: 'rgb(var(--raised) / <alpha-value>)',
        line: 'rgb(var(--line) / <alpha-value>)',
        ink: 'rgb(var(--ink) / <alpha-value>)',
        muted: 'rgb(var(--muted) / <alpha-value>)',
        accent: 'rgb(var(--accent) / <alpha-value>)',
        pos: 'rgb(var(--pos) / <alpha-value>)',
        neg: 'rgb(var(--neg) / <alpha-value>)',
        warn: 'rgb(var(--warn) / <alpha-value>)',
      },
      fontSize: {
        label: ['11px', { lineHeight: '14px', letterSpacing: '0.05em' }],
        micro: ['12px', { lineHeight: '16px' }],
        base: ['13px', { lineHeight: '18px' }],
        kpi: ['26px', { lineHeight: '30px' }],
      },
      borderRadius: { card: '6px' },
    },
  },
  plugins: [],
}
