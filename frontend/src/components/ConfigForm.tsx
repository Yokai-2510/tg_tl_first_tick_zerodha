/**
 * Schema-driven config editor.
 *
 * Fields are generated from the JSON Schema the backend ships at GET /config, so a
 * new backend setting appears here with no frontend release. Values come from
 * `config`; `_doc` strings in the config become inline help.
 *
 * Structural sections cannot be changed mid-session — the backend returns 409 and
 * we surface that plainly rather than pretending the save worked.
 */

import { useMemo, useState } from 'react'
import { api, ApiError } from '../lib/api'
import { humanise } from '../lib/format'
import { useStore } from '../lib/store'
import { Banner, Card, Field } from './ui'

/** Changing these requires a restart; the backend refuses them mid-session. */
const STRUCTURAL = new Set(['schedule', 'universe', 'instruments', 'api', 'broker'])

type Props = { sections: string[]; title: string; note?: string }

export default function ConfigForm({ sections, title, note }: Props) {
  const cfg = useStore((s) => s.cfg)
  const refresh = useStore((s) => s.refresh)
  const [draft, setDraft] = useState<Record<string, any>>({})
  const [err, setErr] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const defs = cfg?.schema?.$defs ?? {}
  const props = cfg?.schema?.properties ?? {}
  const dirty = Object.keys(draft).length > 0

  const resolve = (node: any): any => {
    if (!node) return {}
    if (node.$ref) {
      const name = String(node.$ref).split('/').pop()!
      return resolve(defs[name])
    }
    if (node.anyOf) {
      const first = node.anyOf.find((n: any) => n.type !== 'null') ?? node.anyOf[0]
      return { ...resolve(first), nullable: true }
    }
    return node
  }

  const patch = useMemo(() => buildPatch(draft), [draft])

  const setPath = (path: string[], value: unknown) =>
    setDraft((d) => ({ ...d, [path.join('.')]: value }))

  const save = async () => {
    setBusy(true); setErr(null); setOk(null)
    try {
      const res = await api.patchConfig(patch)
      setDraft({})
      setOk(res.changed.length ? `Saved: ${res.changed.join(', ')}` : 'No changes.')
      await refresh('config')
    } catch (e) {
      if (e instanceof ApiError) {
        setErr(e.isIllegalState
          ? `${e.message} — this field needs a restart to take effect.`
          : e.message)
      } else setErr(String(e))
    } finally { setBusy(false) }
  }

  const validate = async () => {
    if (!dirty) return
    try { await api.validateConfig(patch); setErr(null) }
    catch (e) { if (e instanceof ApiError) setErr(e.message) }
  }

  if (!cfg) return null

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h2 className="text-[15px] font-semibold">{title}</h2>
          {note && <div className="text-micro text-muted mt-0.5">{note}</div>}
        </div>
        <div className="flex gap-2">
          <button className="btn" disabled={!dirty || busy} onClick={() => { setDraft({}); setErr(null) }}>
            Revert
          </button>
          <button className="btn btn-primary" disabled={!dirty || busy} onClick={save}>
            {busy ? 'Saving…' : `Save${dirty ? ` (${Object.keys(draft).length})` : ''}`}
          </button>
        </div>
      </div>

      {err && <Banner tone="neg">{err}</Banner>}
      {ok && <Banner tone="accent">{ok}</Banner>}

      {sections.map((sec) => {
        const schema = resolve(props[sec])
        const value = cfg.config?.[sec] ?? {}
        if (!schema?.properties) return null
        return (
          <Card key={sec} label={humanise(sec)} hint={value?._doc}
            right={STRUCTURAL.has(sec)
              ? <span className="text-label uppercase tracking-wider text-warn">restart required</span>
              : undefined}>
            {value?._doc && (
              <div className="text-[11px] text-muted mb-3 leading-snug">{value._doc}</div>
            )}
            <Group schema={schema} value={value} path={[sec]} draft={draft}
                   resolve={resolve} setPath={setPath} onBlur={validate} />
          </Card>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------- group

function Group(
  { schema, value, path, draft, resolve, setPath, onBlur }:
  {
    schema: any; value: any; path: string[]
    draft: Record<string, any>
    resolve: (n: any) => any
    setPath: (p: string[], v: unknown) => void
    onBlur: () => void
  },
) {
  const entries = Object.entries(schema.properties ?? {}) as [string, any][]
  const leaves = entries.filter(([, s]) => !resolve(s).properties)
  const nested = entries.filter(([, s]) => resolve(s).properties)

  return (
    <div className="space-y-3">
      {leaves.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {leaves.map(([key, raw]) => (
            <Leaf key={key} name={key} schema={resolve(raw)} path={[...path, key]}
                  value={value?.[key]} draft={draft} setPath={setPath} onBlur={onBlur} />
          ))}
        </div>
      )}
      {nested.map(([key, raw]) => {
        const s = resolve(raw)
        const v = value?.[key] ?? {}
        return (
          <div key={key} className="border-l-2 border-line pl-3">
            <div className="lbl mb-2">{humanise(key)}</div>
            {v?._doc && <div className="text-[11px] text-muted mb-2">{v._doc}</div>}
            <Group schema={s} value={v} path={[...path, key]} draft={draft}
                   resolve={resolve} setPath={setPath} onBlur={onBlur} />
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------- leaf

function Leaf(
  { name, schema, path, value, draft, setPath, onBlur }:
  {
    name: string; schema: any; path: string[]; value: any
    draft: Record<string, any>
    setPath: (p: string[], v: unknown) => void
    onBlur: () => void
  },
) {
  const key = path.join('.')
  const current = key in draft ? draft[key] : value
  const changed = key in draft
  const enumVals: string[] | undefined = schema.enum
  const type = schema.type
  const hint = [
    schema.description,
    schema.minimum !== undefined ? `min ${schema.minimum}` : null,
    schema.maximum !== undefined ? `max ${schema.maximum}` : null,
  ].filter(Boolean).join(' · ') || undefined

  const label = (
    <>{humanise(name)}{changed && <span className="ml-1 text-accent">•</span>}</>
  )

  // objects/arrays we cannot render safely: show read-only JSON
  if (type === 'array' || type === 'object' || (Array.isArray(current) || (current && typeof current === 'object'))) {
    return (
      <Field label={humanise(name)} hint="Read-only here — edit in config.json">
        <div className="mono text-[11px] text-muted border border-line rounded-card px-2 py-1.5
                        bg-raised overflow-x-auto whitespace-pre">
          {JSON.stringify(current ?? (type === 'array' ? [] : {}))}
        </div>
      </Field>
    )
  }

  if (type === 'boolean') {
    return (
      <label className="flex items-center justify-between gap-3 h-full border border-line
                        rounded-card px-3 py-2 bg-raised cursor-pointer">
        <span className="text-micro">{label}</span>
        <input type="checkbox" checked={!!current}
               onChange={(e) => setPath(path, e.target.checked)} />
      </label>
    )
  }

  if (enumVals?.length) {
    return (
      <Field label={<>{label}</> as unknown as string} hint={hint}>
        <select className="inp" value={String(current ?? '')}
                onChange={(e) => setPath(path, e.target.value)} onBlur={onBlur}>
          {enumVals.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
      </Field>
    )
  }

  const numeric = type === 'integer' || type === 'number'
  return (
    <Field label={<>{label}</> as unknown as string} hint={hint}>
      <input className="inp" type={numeric ? 'number' : 'text'}
             step={type === 'integer' ? 1 : 'any'}
             value={current ?? ''}
             onChange={(e) => {
               const raw = e.target.value
               setPath(path, numeric ? (raw === '' ? null : Number(raw)) : raw)
             }}
             onBlur={onBlur} />
    </Field>
  )
}

/** Flat "a.b.c" keys -> nested merge-patch object. */
function buildPatch(draft: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {}
  for (const [flat, v] of Object.entries(draft)) {
    const parts = flat.split('.')
    let node = out
    parts.forEach((p, i) => {
      if (i === parts.length - 1) node[p] = v
      else node = (node[p] ??= {})
    })
  }
  return out
}
