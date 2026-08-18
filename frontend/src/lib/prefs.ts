/**
 * Operator preferences. Theme, accent, display name and the sign-in greeting are
 * the operator's own, not the backend's — they live in localStorage and apply
 * before the first paint (see applyPrefs, called from main.tsx).
 *
 * Dark is the default: this console is read for hours during market sessions.
 */
import { create } from 'zustand'

export type Theme = 'dark' | 'light'
export type GreetMode = 'rotate' | 'fixed'

export const ACCENTS: { label: string; value: string }[] = [
  { label: 'Blue', value: '#2563eb' },
  { label: 'Emerald', value: '#0e9f6e' },
  { label: 'Violet', value: '#7c5cfc' },
  { label: 'Amber', value: '#d97706' },
  { label: 'Slate', value: '#475569' },
]

/** One line per sign-in. {n} is replaced with the display name. */
export const GREETINGS: [string, string][] = [
  ['Good morning, {n}', 'Nothing is armed until the settlement snapshot at 09:09.'],
  ['Welcome back, {n}', 'Yesterday’s realised P&L and charges are on the dashboard.'],
  ['Morning, {n}', 'The feed reconnects on its own; Status shows every attempt.'],
  ['Ready when you are, {n}', 'Contracts refresh at 08:45, before the pre-open.'],
  ['Hello, {n}', 'Check the trading mode in Strategy before 09:15.'],
  ['Good to see you, {n}', 'The recorder stops itself before the disk fills.'],
  ['Back again, {n}', 'Rejections are grouped by position under Positions → Orders.'],
  ['Morning, {n}', 'Exits keep running even when entries are disarmed.'],
]

const KEY = 'ft.prefs'

interface Prefs {
  theme: Theme
  accent: string
  displayName: string
  greetMode: GreetMode
  greetCustom: string
}

const DEFAULTS: Prefs = {
  theme: 'dark',
  accent: ACCENTS[0].value,
  displayName: '',
  greetMode: 'rotate',
  greetCustom: 'Console ready.',
}

function read(): Prefs {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? { ...DEFAULTS, ...(JSON.parse(raw) as Partial<Prefs>) } : DEFAULTS
  } catch {
    return DEFAULTS
  }
}

function write(p: Prefs) {
  try { localStorage.setItem(KEY, JSON.stringify(p)) } catch { /* private mode */ }
}

/** Paint the theme before React mounts, so there is no light flash on a dark setup. */
export function applyPrefs() {
  const p = read()
  document.documentElement.setAttribute('data-theme', p.theme)
  document.documentElement.style.setProperty('--accent', p.accent)
}

interface PrefsState extends Prefs {
  set: <K extends keyof Prefs>(key: K, value: Prefs[K]) => void
}

export const usePrefs = create<PrefsState>((set, get) => ({
  ...read(),
  set(key, value) {
    set({ [key]: value } as unknown as Partial<PrefsState>)
    const { theme, accent, displayName, greetMode, greetCustom } = get()
    const next = { theme, accent, displayName, greetMode, greetCustom, [key]: value } as Prefs
    write(next)
    if (key === 'theme') document.documentElement.setAttribute('data-theme', next.theme)
    if (key === 'accent') document.documentElement.style.setProperty('--accent', next.accent)
  },
}))

/** Rotates once per sign-in, not per render. */
export function pickGreeting(): number {
  return Math.floor(Math.random() * GREETINGS.length)
}
