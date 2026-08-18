import { useMemo, useState } from 'react'
import { ApiError, api } from '../lib/api'
import { CREDENTIAL_GROUPS, SETTINGS } from '../lib/sections'
import { ACCENTS, GREETINGS, usePrefs, type GreetMode, type Theme } from '../lib/prefs'
import { MONO, V, badge, cardTitleLg, ellip, seg, segTrack } from '../lib/style'
import { useStore } from '../lib/store'
import { toast } from '../lib/toast'
import ConfigForm from '../components/ConfigForm'
import { Button, Card, CardHead, Pill, Segmented, Toggle } from '../components/ui'

export default function Settings() {
  const [tab, setTab] = useState(SETTINGS[0].id)
  const section = SETTINGS.find((s) => s.id === tab) ?? SETTINGS[0]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <Segmented value={tab} onChange={setTab}
        options={SETTINGS.map((s) => ({ key: s.id, label: s.title }))} />
      {section.custom === 'appearance' ? <Appearance /> : null}
      {section.custom === 'credentials' ? <Credentials /> : null}
      {section.custom === 'raw' ? <Raw /> : null}
      {!section.custom ? <ConfigForm section={section} /> : null}
    </div>
  )
}

/* ------------------------------------------------------------------ appearance */

function Appearance() {
  const theme = usePrefs((s) => s.theme)
  const accent = usePrefs((s) => s.accent)
  const displayName = usePrefs((s) => s.displayName)
  const greetMode = usePrefs((s) => s.greetMode)
  const greetCustom = usePrefs((s) => s.greetCustom)
  const set = usePrefs((s) => s.set)

  const name = displayName.trim() || 'operator'
  const sample = greetMode === 'rotate'
    ? GREETINGS[0][0].replace('{n}', name)
    : (greetCustom || 'Console ready.')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Card>
        <CardHead
          title="Appearance"
          sub="Saved in this browser, not on the server, so two operators on the same backend can each have their own."
        />
        <div style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 22 }}>
          <Setting k="Theme" help="Dark is the default — this console is read for hours during a session.">
            <div style={{ ...segTrack, padding: 3 }}>
              {(['dark', 'light'] as Theme[]).map((t) => (
                <button key={t} onClick={() => set('theme', t)}
                  style={{ ...seg(theme === t), padding: '6px 16px', textTransform: 'capitalize' }}>
                  {t}
                </button>
              ))}
            </div>
          </Setting>

          <Setting k="Accent" help="Used for the active nav item, primary buttons and the P&L curve.">
            <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap' }}>
              {ACCENTS.map((a) => {
                const on = accent === a.value
                return (
                  <button key={a.value} onClick={() => set('accent', a.value)} title={a.label}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px 6px 9px',
                      borderRadius: 10, background: on ? V.chip : V.card,
                      border: `1px solid ${on ? a.value : V.border}`,
                      fontSize: 11, color: on ? V.text : V.muted, fontWeight: on ? 600 : 500,
                    }}>
                    <span style={{ width: 13, height: 13, borderRadius: 5, background: a.value, flex: 'none' }} />
                    {a.label}
                  </button>
                )
              })}
            </div>
          </Setting>

          <Setting k="Display name" help="Used in the sign-in greeting. Nothing is sent to the backend.">
            <input value={displayName} placeholder="Operator"
              onChange={(e) => set('displayName', e.target.value)}
              style={{
                width: 260, maxWidth: '100%', padding: '9px 12px', borderRadius: 10,
                border: `1px solid ${V.border2}`, background: V.sunken, fontSize: 12, outline: 'none',
              }} />
          </Setting>

          <Setting k="Greeting" help="Rotate picks a different line each sign-in; fixed always shows your own.">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ ...segTrack, padding: 3 }}>
                {(['rotate', 'fixed'] as GreetMode[]).map((m) => (
                  <button key={m} onClick={() => set('greetMode', m)}
                    style={{ ...seg(greetMode === m), padding: '6px 16px', textTransform: 'capitalize' }}>
                    {m}
                  </button>
                ))}
              </div>
              {greetMode === 'fixed' ? (
                <input value={greetCustom} placeholder="Console ready."
                  onChange={(e) => set('greetCustom', e.target.value)}
                  style={{
                    width: 340, maxWidth: '100%', padding: '9px 12px', borderRadius: 10,
                    border: `1px solid ${V.border2}`, background: V.sunken, fontSize: 12, outline: 'none',
                  }} />
              ) : null}
              <div style={{
                padding: '13px 15px', borderRadius: 12, background: V.sunken,
                border: `1px solid ${V.border}`,
              }}>
                <div style={{ fontSize: 10, letterSpacing: '.08em', textTransform: 'uppercase', color: V.faint }}>
                  Next sign-in
                </div>
                <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: '-.015em', marginTop: 7 }}>
                  {sample}
                </div>
              </div>
            </div>
          </Setting>
        </div>
      </Card>
    </div>
  )
}

function Setting({ k, help, children }: { k: string; help: string; children: React.ReactNode }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(260px,1.1fr)',
      gap: 22, alignItems: 'start',
    }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{k}</div>
        <div style={{ fontSize: 11, color: V.faint, marginTop: 5, lineHeight: 1.55 }}>{help}</div>
      </div>
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  )
}

/* ------------------------------------------------------------------ credentials */

interface Check { label: string; state: 'pass' | 'fail' | 'skip'; detail: string }

function Credentials() {
  const status = useStore((s) => s.status)
  const username = useStore((s) => s.username)
  const [checks, setChecks] = useState<Check[] | null>(null)
  const [busy, setBusy] = useState(false)

  /**
   * Credentials are never returned by the API — credentials.json is gitignored and
   * chmod 600 on the host. So the only honest test is a live round trip: each row
   * below is a real request, not a mock.
   */
  const test = async () => {
    if (busy) return
    setBusy(true)
    setChecks(null)
    const out: Check[] = []
    try {
      const h = await api.health()
      out.push({ label: 'Backend reachable', state: 'pass', detail: `${h.status} · v${h.version} · ${h.phase}` })
    } catch (e) {
      out.push({
        label: 'Backend reachable', state: 'fail',
        detail: e instanceof Error ? e.message : String(e),
      })
      setChecks(out)
      setBusy(false)
      return
    }

    try {
      const s = await api.status()
      out.push({ label: 'Session token accepted', state: 'pass', detail: username ? `signed in as ${username}` : 'token valid' })
      out.push({
        label: 'Broker session', state: s.phase === 'PHASE_1_FAIL' ? 'fail' : 'pass',
        detail: s.phase === 'PHASE_1_FAIL'
          ? (s.last_error ?? 'Phase 1 failed — check api_key, password and totp_key.')
          : `authenticated · trade mode ${s.mode}`,
      })
      out.push({
        label: 'Market feed', state: s.feed.connected ? 'pass' : 'skip',
        detail: s.feed.connected
          ? `${s.feed.subscribed} subscribed · ${s.feed.reconnects} reconnects`
          : 'not connected — normal outside the session window',
      })
    } catch (e) {
      out.push({
        label: 'Session token accepted', state: 'fail',
        detail: e instanceof ApiError && e.status === 401
          ? 'Token rejected — sign in again.'
          : e instanceof Error ? e.message : String(e),
      })
    }

    try {
      const cfg = await api.config()
      const key = String((cfg.config as Record<string, { api_key?: string }>).broker?.api_key ?? '')
      out.push({
        label: 'Configuration readable', state: 'pass',
        detail: key ? `broker.api_key ends …${key.slice(-4)}` : 'broker.api_key is empty',
      })
    } catch (e) {
      out.push({
        label: 'Configuration readable', state: 'fail',
        detail: e instanceof Error ? e.message : String(e),
      })
    }

    // The real thing: the backend authenticates against Kite and calls profile,
    // margins and the instrument master. Inferring "broker session ok" from the
    // phase only tells you what happened at 08:45; this proves the credentials
    // work right now, and is the only way to see true capital before phase 1.
    try {
      const r = await api.brokerTest()
      for (const c of r.checks) {
        out.push({
          label: c.name.charAt(0).toUpperCase() + c.name.slice(1),
          state: c.ok ? 'pass' : 'fail',
          detail: c.ms != null ? `${c.detail} (${c.ms}ms)` : c.detail,
        })
      }
    } catch (e) {
      out.push({
        label: 'Broker connection test', state: 'fail',
        detail: e instanceof ApiError && e.status === 409
          ? e.message
          : e instanceof Error ? e.message : String(e),
      })
    }

    setChecks(out)
    setBusy(false)
  }

  const rateLimited = Object.values(status?.rate_limits ?? {}).some((b) => (b.rejected ?? 0) > 0)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Card>
        <CardHead
          title="Credentials"
          sub="Secrets live in credentials.json on the host — gitignored, chmod 600, and never returned by the API. This screen documents the shape and tests the round trip; it cannot read or write the values."
          right={<Button kind="primary" disabled={busy} onClick={() => void test()}>
            {busy ? 'Testing…' : 'Test connection'}
          </Button>}
        />

        {checks ? (
          <div style={{ marginTop: 20, border: `1px solid ${V.border}`, borderRadius: 14, overflow: 'hidden' }}>
            {checks.map((c, i) => {
              const [bg, fg, word] = c.state === 'pass' ? [V.posbg, V.pos, 'PASS']
                : c.state === 'fail' ? [V.negbg, V.neg, 'FAIL'] : [V.chip, V.muted, 'SKIP']
              return (
                <div key={c.label} style={{
                  display: 'grid', gridTemplateColumns: '54px minmax(0,220px) minmax(0,1fr)',
                  gap: 14, alignItems: 'center', padding: '12px 16px',
                  borderTop: i ? `1px solid ${V.border}` : 'none',
                  background: c.state === 'fail' ? V.negbg : V.card,
                }}>
                  <span style={badge(bg, fg)}>{word}</span>
                  <div style={{ fontSize: 12, fontWeight: 500, ...ellip }}>{c.label}</div>
                  <div style={{ fontSize: 11, color: V.muted, fontFamily: MONO, ...ellip }}>{c.detail}</div>
                </div>
              )
            })}
          </div>
        ) : (
          <div style={{ fontSize: 12, color: V.faint, lineHeight: 1.65, marginTop: 16 }}>
            Five checks run in order: backend reachable, session token accepted, broker session,
            market feed, configuration readable. Each one is a real request.
          </div>
        )}

        {rateLimited ? (
          <div style={{
            marginTop: 14, padding: '11px 15px', borderRadius: 12, background: V.warnbg,
            border: `1px solid ${V.warn}44`, color: V.warn, fontSize: 12, lineHeight: 1.5,
          }}>
            The broker has rejected requests for rate limiting this session — see Status → Rate limits.
          </div>
        ) : null}
      </Card>

      {CREDENTIAL_GROUPS.map((g) => (
        <Card key={g.title} pad={false} style={{ overflow: 'hidden' }}>
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            gap: 12, padding: '17px 22px', borderBottom: `1px solid ${V.border}`,
          }}>
            <div style={cardTitleLg}>{g.title}</div>
            <div style={{ fontSize: 11, color: V.faint, fontFamily: MONO, ...ellip }}>{g.file}</div>
          </div>
          {g.rows.map(([key, doc]) => (
            <div key={key} style={{
              display: 'grid', gridTemplateColumns: 'minmax(0,180px) minmax(0,1fr) 96px',
              gap: 16, alignItems: 'center', padding: '13px 22px',
              borderBottom: `1px solid ${V.border}`,
            }}>
              <div style={{ fontSize: 12, fontFamily: MONO, fontWeight: 500 }}>{key}</div>
              <div style={{ fontSize: 11, color: V.muted, lineHeight: 1.55 }}>{doc}</div>
              <div style={{ textAlign: 'right', fontSize: 11, color: V.faint, fontFamily: MONO }}>
                ••••••••
              </div>
            </div>
          ))}
        </Card>
      ))}

      <Card>
        <CardHead title="Changing a secret"
          sub="Edit credentials.json on the host and restart the service. There is deliberately no write path over HTTP: an API that can rewrite its own credentials is an API that can be made to hand them over." />
        <pre style={{
          margin: '16px 0 0', padding: '15px 16px', border: `1px solid ${V.border}`,
          borderRadius: 12, background: V.sunken, fontFamily: MONO, fontSize: 11,
          lineHeight: 1.75, color: V.muted, overflowX: 'auto',
        }}>{`nano config/credentials.json      # chmod 600, gitignored
systemctl restart first-tick      # picked up at boot only`}</pre>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------ raw */

function Raw() {
  const cfg = useStore((s) => s.cfg)
  const refresh = useStore((s) => s.refresh)
  const original = useMemo(() => JSON.stringify(cfg?.config ?? {}, null, 2), [cfg])
  const [text, setText] = useState(original)
  const [touched, setTouched] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const shown = touched ? text : original
  const parsed = useMemo(() => {
    try { return { ok: true as const, value: JSON.parse(shown) as Record<string, unknown> } }
    catch (e) { return { ok: false as const, msg: e instanceof Error ? e.message : String(e) } }
  }, [shown])

  const save = async () => {
    if (!parsed.ok || busy) return
    setBusy(true)
    setError(null)
    try {
      const res = await api.patchConfig(parsed.value)
      setTouched(false)
      await refresh('config')
      toast('Config saved', `${res.changed.length} path${res.changed.length === 1 ? '' : 's'} changed.`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Card>
        <CardHead
          title="Raw configuration"
          sub="The whole config object as the backend returns it. Sent as a merge patch, so keys you delete here are not removed — set them explicitly instead."
          right={<Pill bg={parsed.ok ? V.chip : V.negbg} fg={parsed.ok ? V.muted : V.neg}>
            {parsed.ok ? 'valid JSON' : 'invalid JSON'}
          </Pill>}
        />
        <textarea
          value={shown}
          spellCheck={false}
          onChange={(e) => { setTouched(true); setText(e.target.value); setError(null) }}
          rows={26}
          style={{
            width: '100%', marginTop: 16, padding: '14px 15px', borderRadius: 12,
            background: V.sunken, border: `1px solid ${parsed.ok ? V.border2 : V.neg}`,
            fontFamily: MONO, fontSize: 11.5, lineHeight: 1.65, outline: 'none', resize: 'vertical',
          }} />
        {!parsed.ok ? (
          <div style={{ marginTop: 10, fontSize: 11, color: V.neg, fontFamily: MONO }}>{parsed.msg}</div>
        ) : null}
        {error ? (
          <div style={{
            marginTop: 12, padding: '12px 15px', borderRadius: 12, background: V.negbg,
            border: `1px solid ${V.neg}44`, color: V.neg, fontSize: 12, fontFamily: MONO,
            lineHeight: 1.55, wordBreak: 'break-word',
          }}>{error}</div>
        ) : null}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 16 }}>
          <div style={{ fontSize: 12, color: touched ? V.accent : V.muted }}>
            {touched ? 'Edited — not yet sent' : 'Matches the server'}
          </div>
          <div style={{ flex: 1 }} />
          <Button disabled={!touched || busy} onClick={() => { setTouched(false); setError(null) }}>
            Revert
          </Button>
          <Button kind="primary" disabled={!touched || !parsed.ok || busy} onClick={() => void save()}>
            {busy ? 'Saving…' : 'Save configuration'}
          </Button>
        </div>
      </Card>

      <SchemaNote />
    </div>
  )
}

function SchemaNote() {
  const cfg = useStore((s) => s.cfg)
  const refresh = useStore((s) => s.refresh)
  const [on, setOn] = useState(false)
  const keys = Object.keys((cfg?.schema as { properties?: Record<string, unknown> })?.properties ?? {})

  return (
    <Card>
      <CardHead
        title="Schema"
        sub={keys.length
          ? `The backend also returns a JSON Schema with ${keys.length} top-level sections; a rejected patch names the exact path that failed.`
          : 'The backend returns a JSON Schema alongside the config. It is used to explain validation failures.'}
        right={<div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 11, color: V.muted }}>Show</span>
          <Toggle on={on} onChange={setOn} size="sm" />
        </div>}
      />
      {on ? (
        <pre style={{
          margin: '16px 0 0', padding: '14px 15px', border: `1px solid ${V.border}`,
          borderRadius: 12, background: V.sunken, fontFamily: MONO, fontSize: 11,
          lineHeight: 1.6, color: V.muted, maxHeight: 420, overflow: 'auto',
        }}>{JSON.stringify(cfg?.schema ?? {}, null, 2)}</pre>
      ) : null}
      <div style={{ marginTop: 14 }}>
        <Button onClick={() => void refresh('config')}>Re-read from server</Button>
      </div>
    </Card>
  )
}
