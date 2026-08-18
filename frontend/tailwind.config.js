/**
 * The v3 surface is authored with inline styles reading the CSS custom properties
 * in src/index.css, so Tailwind is kept only for the handful of layout utilities
 * the pages still use. The colour names below mirror those tokens, so a class
 * like `text-muted` resolves to the same value an inline `var(--muted)` does.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        page: 'var(--page)',
        card: 'var(--card)',
        sunken: 'var(--sunken)',
        line: 'var(--border)',
        line2: 'var(--border2)',
        ink: 'var(--text)',
        muted: 'var(--muted)',
        faint: 'var(--faint)',
        accent: 'var(--accent)',
        pos: 'var(--pos)',
        neg: 'var(--neg)',
        warn: 'var(--warn)',
        chip: 'var(--chip)',
      },
      fontSize: {
        label: ['11px', { lineHeight: '14px', letterSpacing: '0.05em' }],
        micro: ['12px', { lineHeight: '16px' }],
        base: ['13px', { lineHeight: '18px' }],
      },
      borderRadius: { card: '18px', ctl: '10px' },
      boxShadow: { card: 'var(--shadow)' },
    },
  },
  plugins: [],
}
