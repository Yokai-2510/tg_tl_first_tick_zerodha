import { useEffect, useState } from 'react'
import { API_BASE, api, apiBaseIsOverridden, configProblem, setApiBase } from '../lib/api'
import { GREETINGS, pickGreeting, usePrefs } from '../lib/prefs'
import { MONO, V, inputStyle } from '../lib/style'
import { useStore } from '../lib/store'
import { Banner, Button } from '../components/ui'

/**
 * The sign-in screen. One card, the operator's own greeting, and the two fields
 * that matter. Everything that only occasionally matters — an API token instead
 * of a password, a different backend URL — is behind a disclosure, because on a
 * normal morning it is username, password, enter.
 */
export default function SignIn() {
  const signIn = useStore((s) => s.signIn)
  const signInWithToken = useStore((s) => s.signInWithToken)
  const displayName = usePrefs((s) => s.displayName)
  const greetMode = usePrefs((s) => s.greetMode)
  const greetCustom = usePrefs((s) => s.greetCustom)
  const setPref = usePrefs((s) => s.set)

  const [user, setUser] = useState('')
  const [pass, setPass] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [advanced, setAdvanced] = useState(false)
  const [tokenMode, setTokenMode] = useState(false)
  const [tokenVal, setTokenVal] = useState('')
  const [baseVal, setBaseVal] = useState(API_BASE)

  // Rotates once per mount, not per keystroke.
  const [greetIdx] = useState(pickGreeting)
  const [health, setHealth] = useState<null | { ok: boolean; text: string }>(null)

  useEffect(() => {
    let alive = true
    api.health()
      .then((h) => alive && setHealth({ ok: true, text: `Engine up · ${h.phase}` }))
      .catch(() => alive && setHealth({ ok: false, text: 'Engine unreachable' }))
    return () => { alive = false }
  }, [])

  const name = displayName.trim() || 'operator'
  const [gTitle, gBody] = GREETINGS[greetIdx % GREETINGS.length]
  const title = greetMode === 'rotate' ? gTitle.replace('{n}', name) : (greetCustom || 'Console ready.')
  const body = greetMode === 'rotate'
    ? gBody
    : `Signing in as ${name}. Everything below runs against the configured backend.`

  const canSubmit = tokenMode ? !!tokenVal.trim() : (!!user.trim() && !!pass)

  const go = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit || busy) return
    setBusy(true)
    setErr(null)
    try {
      if (tokenMode) await signInWithToken(tokenVal.trim())
      else await signIn(user.trim(), pass)
    } catch (e2) {
      // The backend deliberately does not say which field was wrong.
      setErr(e2 instanceof Error ? e2.message : 'Sign-in failed.')
    } finally {
      setBusy(false)
    }
  }

  const problem = configProblem()

  return (
    <div style={{
      minHeight: '100vh', background: V.page, color: V.text,
      display: 'grid', placeItems: 'center', padding: 24,
    }}>
      <div style={{ width: 430, maxWidth: '100%' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 22 }}>
          <div style={{
            width: 34, height: 34, borderRadius: 11, background: V.accent, color: '#fff',
            display: 'grid', placeItems: 'center', fontSize: 13, fontWeight: 700,
          }}>FT</div>
          <div>
            <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: '-.015em' }}>First-Tick</div>
            <div style={{ fontSize: 11, color: V.faint, marginTop: 2 }}>
              {new Date().toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' })}
            </div>
          </div>
          <div style={{ flex: 1 }} />
          <div style={{
            display: 'flex', alignItems: 'center', gap: 7, padding: '5px 11px',
            borderRadius: 9, border: `1px solid ${V.border}`, background: V.card,
          }}>
            <div style={{
              width: 6, height: 6, borderRadius: '50%',
              background: health === null ? V.muted : health.ok ? V.pos : V.neg,
            }} />
            <div style={{ fontSize: 11, color: V.muted }}>{health?.text ?? 'Checking…'}</div>
          </div>
        </div>

        {problem ? <div style={{ marginBottom: 14 }}><Banner tone="neg">{problem}</Banner></div> : null}

        <form onSubmit={go} style={{
          border: `1px solid ${V.border}`, borderRadius: 20, background: V.card,
          padding: '30px 30px 26px', boxShadow: V.shadow,
        }}>
          <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-.025em', lineHeight: 1.25 }}>{title}</div>
          <div style={{ fontSize: 13, color: V.muted, lineHeight: 1.55, marginTop: 8, marginBottom: 24 }}>{body}</div>

          {tokenMode ? (
            <Field label="API token" hint="required">
              <input type="password" value={tokenVal} autoFocus placeholder="Paste operator token"
                onChange={(e) => { setTokenVal(e.target.value); setErr(null) }} style={splashInput} />
            </Field>
          ) : (
            <>
              <Field label="Username" hint="Kite ID">
                <input value={user} autoFocus autoComplete="username" placeholder="AB1234"
                  onChange={(e) => { setUser(e.target.value); setErr(null) }} style={splashInput} />
              </Field>
              <Field label="Password" hint="">
                <input type="password" value={pass} autoComplete="current-password" placeholder="Account password"
                  onChange={(e) => { setPass(e.target.value); setErr(null) }} style={splashInput} />
              </Field>
            </>
          )}

          <Field label="Display name" hint="shown in greetings">
            <input value={displayName} placeholder="Operator"
              onChange={(e) => setPref('displayName', e.target.value)} style={splashInput} />
          </Field>

          {err ? (
            <div style={{ marginTop: 2, marginBottom: 10, fontSize: 12, color: V.neg }}>{err}</div>
          ) : null}

          <button type="submit" disabled={busy || !canSubmit} style={{
            width: '100%', marginTop: 8, padding: 12, border: 'none', borderRadius: 11,
            background: V.accent, color: '#fff', fontSize: 13, fontWeight: 600,
            opacity: busy || !canSubmit ? 0.5 : 1,
          }}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>

          <div style={{
            marginTop: 22, paddingTop: 16, borderTop: `1px solid ${V.border}`,
            display: 'flex', gap: 18,
          }}>
            <Note k="Validated with GET /status" v="A 401 anywhere clears the token and returns to this screen." />
            <Note k="Held for this tab only" v="The token lives in sessionStorage; nothing is written to disk." />
          </div>

          <div style={{
            marginTop: 16, paddingTop: 14, borderTop: `1px solid ${V.border}`,
            display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
          }}>
            <button type="button" onClick={() => { setTokenMode(!tokenMode); setErr(null) }}
              style={linkBtn}>
              {tokenMode ? 'Use username & password' : 'Use an API token'}
            </button>
            <button type="button" onClick={() => setAdvanced(!advanced)} style={linkBtn}>
              {advanced ? 'Hide backend URL' : 'Backend URL'}
            </button>
          </div>

          {advanced ? (
            <div style={{ marginTop: 14, paddingTop: 14, borderTop: `1px solid ${V.border}` }}>
              <Field label="Backend URL" hint="saved in this browser">
                <input value={baseVal} onChange={(e) => setBaseVal(e.target.value)}
                  placeholder="https://host/api/v1" style={splashInput} />
              </Field>
              <div style={{ fontSize: 11, color: V.faint, lineHeight: 1.55, marginBottom: 12 }}>
                Used instead of the compiled-in default, so pointing at another server needs no
                rebuild. The live-stream URL is derived from it.
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <Button onClick={() => setApiBase(baseVal)}
                  disabled={!baseVal.trim() || baseVal.trim() === API_BASE}
                  style={{ flex: 1, justifyContent: 'center' }}>
                  Save & reload
                </Button>
                {apiBaseIsOverridden ? (
                  <Button onClick={() => setApiBase(null)} style={{ flex: 1, justifyContent: 'center' }}>
                    Reset to default
                  </Button>
                ) : null}
              </div>
            </div>
          ) : null}
        </form>

        {/* Which backend this build talks to — the fastest way to spot a console
            deployed against the wrong server. */}
        <div style={{
          marginTop: 14, fontSize: 11, color: V.faint, textAlign: 'center',
          fontFamily: MONO, wordBreak: 'break-all',
        }}>
          {API_BASE}{apiBaseIsOverridden ? ' (overridden)' : ''}
        </div>
      </div>
    </div>
  )
}

const splashInput: React.CSSProperties = {
  ...inputStyle,
  width: '100%',
  padding: '11px 13px',
  border: `1px solid ${V.border2}`,
  borderRadius: 11,
  fontFamily: MONO,
  fontSize: 13,
}

const linkBtn: React.CSSProperties = {
  border: 'none', background: 'none', padding: 0,
  fontSize: 11, color: V.muted, textAlign: 'left',
}

function Field({ label, hint, children }: { label: string; hint: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{
        display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
        gap: 10, marginBottom: 7,
      }}>
        <div style={{ fontSize: 11, letterSpacing: '.06em', textTransform: 'uppercase', color: V.muted }}>
          {label}
        </div>
        <div style={{ fontSize: 11, color: V.faint }}>{hint}</div>
      </div>
      {children}
    </div>
  )
}

function Note({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: V.text }}>{k}</div>
      <div style={{ fontSize: 11, color: V.faint, lineHeight: 1.55, marginTop: 4 }}>{v}</div>
    </div>
  )
}

/** Shown while the first status + config round trip is in flight. */
export function Booting() {
  const error = useStore((s) => s.error)
  const bootstrap = useStore((s) => s.bootstrap)
  const signOut = useStore((s) => s.signOut)
  return (
    <div style={{
      minHeight: '100vh', background: V.page, color: V.text,
      display: 'grid', placeItems: 'center', padding: 24,
    }}>
      <div style={{
        width: 460, maxWidth: '100%', border: `1px solid ${V.border}`, borderRadius: 18,
        background: V.card, padding: 26, boxShadow: V.shadow,
      }}>
        <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-.015em' }}>
          {error ? 'Cannot reach the backend' : 'Loading…'}
        </div>
        {error ? (
          <div style={{ fontSize: 13, color: V.neg, lineHeight: 1.6, marginTop: 10 }}>{error}</div>
        ) : (
          <div style={{ fontSize: 13, color: V.muted, lineHeight: 1.6, marginTop: 10 }}>
            Reading status and configuration.
          </div>
        )}
        {error ? (
          <div style={{ display: 'flex', gap: 9, marginTop: 20 }}>
            <Button kind="primary" onClick={() => void bootstrap()}>Retry</Button>
            <Button onClick={signOut}>Sign out</Button>
          </div>
        ) : null}
      </div>
    </div>
  )
}
