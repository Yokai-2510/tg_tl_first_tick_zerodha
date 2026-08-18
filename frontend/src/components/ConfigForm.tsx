import { useMemo, useState } from 'react'
import { ApiError, api } from '../lib/api'
import type { Field, Section } from '../lib/sections'
import { buildPatch, docFor, getPath } from '../lib/patch'
import { MONO, V, badge, ellip, seg, segTrack } from '../lib/style'
import { useStore } from '../lib/store'
import { toast } from '../lib/toast'
import { Button, Card, Toggle } from './ui'

/**
 * Renders one curated config section against the live config object.
 *
 * Edits are held locally until Save, then sent as a single merge patch naming
 * only the paths that changed — so two sections never overwrite each other, and a
 * 422 comes back pointing at an exact JSON path, which is shown verbatim because
 * the backend's wording is more precise than anything this form could invent.
 */
export default function ConfigForm({ section }: { section: Section }) {
  const cfg = useStore((s) => s.cfg)
  const refresh = useStore((s) => s.refresh)
  const [edits, setEdits] = useState<Record<string, unknown>>({})
  const [error, setError] = useState<{ path?: string; msg: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const config = cfg?.config
  const dirtyCount = Object.keys(edits).length

  const value = (f: Field): unknown => (f.path in edits ? edits[f.path] : getPath(config, f.path))
  const set = (path: string, v: unknown) => {
    setEdits((e) => ({ ...e, [path]: v }))
    setError(null)
  }

  const save = async () => {
    if (!dirtyCount || busy) return
    setBusy(true)
    setError(null)
    try {
      const res = await api.patchConfig(buildPatch(edits))
      setEdits({})
      await refresh('config')
      toast('Config saved', `${res.changed.length || dirtyCount} path${(res.changed.length || dirtyCount) === 1 ? '' : 's'} changed.${section.structural ? ' Takes effect on the next restart.' : ''}`)
    } catch (e) {
      if (e instanceof ApiError && e.isConfigInvalid) setError({ msg: e.message })
      else if (e instanceof ApiError && e.isIllegalState) setError({ msg: e.message })
      else setError({ msg: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  if (!config) {
    return <Card><div style={{ fontSize: 13, color: V.muted }}>Configuration has not loaded.</div></Card>
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Card pad={false} style={{ overflow: 'hidden' }}>
        <div style={{ padding: '20px 24px', borderBottom: `1px solid ${V.border}` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 11, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: '-.015em' }}>{section.title}</div>
            {section.structural ? (
              <span style={badge(V.warnbg, V.warn)}>RESTART REQUIRED</span>
            ) : null}
          </div>
          {section.doc ? (
            <div style={{ fontSize: 12, color: V.muted, marginTop: 5, lineHeight: 1.55 }}>{section.doc}</div>
          ) : null}
        </div>

        {section.steps?.length ? (
          <div style={{ padding: '20px 24px', borderBottom: `1px solid ${V.border}`, background: V.sunken }}>
            <div style={{
              fontSize: 11, letterSpacing: '.07em', textTransform: 'uppercase',
              color: V.muted, marginBottom: 14,
            }}>How it works</div>
            {section.steps.map((s, i) => (
              <div key={i} style={{
                display: 'grid', gridTemplateColumns: '22px minmax(0,1fr)',
                gap: 13, alignItems: 'start', padding: '6px 0',
              }}>
                <div style={{
                  width: 22, height: 22, borderRadius: 7, background: V.chip, color: V.muted,
                  fontSize: 11, fontWeight: 600, display: 'grid', placeItems: 'center', fontFamily: MONO,
                }}>{i + 1}</div>
                <div style={{ fontSize: 12, lineHeight: 1.6, color: V.text, paddingTop: 2 }}>{s}</div>
              </div>
            ))}
          </div>
        ) : null}

        {(section.fields ?? []).map((f) => (
          <Row key={f.path} field={f} value={value(f)} config={config}
            dirty={f.path in edits} onChange={(v) => set(f.path, v)} />
        ))}
      </Card>

      {error ? (
        <div style={{
          padding: '12px 16px', borderRadius: 12, background: V.negbg,
          border: `1px solid ${V.neg}44`, color: V.neg, fontSize: 12,
          fontFamily: MONO, lineHeight: 1.55, wordBreak: 'break-word',
        }}>{error.msg}</div>
      ) : null}

      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, position: 'sticky', bottom: 18,
        padding: '14px 20px', borderRadius: 14, background: V.card,
        border: `1px solid ${dirtyCount ? V.accent : V.border}`,
        boxShadow: '0 6px 20px rgba(0,0,0,.10)',
      }}>
        <div style={{ fontSize: 12, color: dirtyCount ? V.accent : V.muted }}>
          {dirtyCount
            ? `${dirtyCount} unsaved change${dirtyCount === 1 ? '' : 's'} — sent as one merge patch`
            : 'No unsaved changes'}
        </div>
        <div style={{ flex: 1 }} />
        <Button disabled={!dirtyCount || busy} onClick={() => { setEdits({}); setError(null) }}>Revert</Button>
        <Button kind="primary" disabled={!dirtyCount || busy} onClick={() => void save()}>
          {busy ? 'Saving…' : 'Save changes'}
        </Button>
      </div>
    </div>
  )
}

function Row({ field, value, config, dirty, onChange }: {
  field: Field
  value: unknown
  config: Record<string, unknown>
  dirty: boolean
  onChange: (v: unknown) => void
}) {
  const help = field.doc || docFor(config, field.path) || ''
  const missing = value === undefined

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'minmax(0,1.1fr) minmax(230px,1fr)',
      gap: 22, padding: '16px 24px', borderBottom: `1px solid ${V.border}`, alignItems: 'start',
    }}>
      <div style={{ minWidth: 0 }}>
        <div style={{
          fontSize: 12, fontFamily: MONO, fontWeight: 500,
          color: dirty ? V.accent : V.text, wordBreak: 'break-all',
        }}>{field.label}</div>
        <div style={{ fontSize: 11, color: V.faint, marginTop: 5, lineHeight: 1.55 }}>{help}</div>
      </div>
      <div style={{ minWidth: 0 }}>
        {missing ? (
          <div style={{ fontSize: 11, color: V.faint, fontFamily: MONO }}>
            not present in this config
          </div>
        ) : (
          <Control field={field} value={value} dirty={dirty} onChange={onChange} />
        )}
      </div>
    </div>
  )
}

function Control({ field, value, dirty, onChange }: {
  field: Field
  value: unknown
  dirty: boolean
  onChange: (v: unknown) => void
}) {
  if (field.type === 'bool') {
    const on = value === true
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
        <Toggle on={on} onChange={(next) => onChange(next)} />
        <span style={{ fontSize: 12, color: V.muted, fontFamily: MONO }}>{String(on)}</span>
      </div>
    )
  }

  if (field.type === 'enum') {
    return (
      <div style={{ ...segTrack, padding: 3 }}>
        {(field.options ?? []).map((o) => (
          <button key={o} onClick={() => onChange(o)}
            style={{ ...seg(String(value) === o), fontFamily: MONO, padding: '6px 13px' }}>
            {o}
          </button>
        ))}
      </div>
    )
  }

  if (field.type === 'list') {
    const list = Array.isArray(value) ? (value as unknown[]).map(String) : []
    return (
      <textarea
        value={list.join('\n')}
        onChange={(e) => onChange(e.target.value.split('\n').map((s) => s.trim()).filter(Boolean))}
        rows={Math.max(3, list.length + 1)}
        style={{
          width: '100%', padding: '9px 12px', borderRadius: 10, background: V.sunken,
          border: `1px solid ${dirty ? V.accent : V.border2}`, fontSize: 12,
          fontFamily: MONO, outline: 'none', resize: 'vertical', lineHeight: 1.6,
        }} />
    )
  }

  const numeric = field.type === 'int' || field.type === 'float'
  return <NumberOrText field={field} value={value} dirty={dirty} numeric={numeric} onChange={onChange} />
}

function NumberOrText({ field, value, dirty, numeric, onChange }: {
  field: Field
  value: unknown
  dirty: boolean
  numeric: boolean
  onChange: (v: unknown) => void
}) {
  const [text, setText] = useState(String(value ?? ''))
  const [err, setErr] = useState<string | null>(null)

  // The field is the source of truth until it is touched; a fresh poll should
  // not overwrite what the operator is halfway through typing.
  const shown = dirty ? text : String(value ?? '')

  const hint = useMemo(() => {
    if (!numeric) return field.unit ?? ''
    if (field.min === undefined || field.max === undefined) return field.unit ?? ''
    return `${field.min}–${field.max}${field.unit ? ` ${field.unit}` : ''}`
  }, [numeric, field.min, field.max, field.unit])

  const commit = (raw: string) => {
    if (!numeric) { onChange(raw); return }
    const n = field.type === 'int' ? parseInt(raw, 10) : parseFloat(raw)
    if (Number.isNaN(n)) { setErr(`${field.label} must be a number (got "${raw}")`); return }
    if (field.min !== undefined && n < field.min) { setErr(`${field.label} must be ≥ ${field.min}`); return }
    if (field.max !== undefined && n > field.max) { setErr(`${field.label} must be ≤ ${field.max}`); return }
    setErr(null)
    onChange(n)
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
        <input
          value={shown}
          onChange={(e) => { setText(e.target.value); commit(e.target.value) }}
          onBlur={(e) => commit(e.target.value)}
          style={{
            flex: 1, minWidth: 0, maxWidth: 220, padding: '8px 12px', borderRadius: 10,
            background: V.sunken, fontSize: 12, fontFamily: MONO, outline: 'none',
            border: `1px solid ${err ? V.neg : dirty ? V.accent : V.border2}`,
          }} />
        <div style={{ fontSize: 11, color: V.faint, whiteSpace: 'nowrap', ...ellip }}>{hint}</div>
      </div>
      {err ? (
        <div style={{ marginTop: 8, fontSize: 11, color: V.neg, fontFamily: MONO, lineHeight: 1.5 }}>{err}</div>
      ) : null}
    </div>
  )
}
