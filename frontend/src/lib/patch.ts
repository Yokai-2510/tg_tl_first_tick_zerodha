/**
 * Dotted-path access into the config object, and the RFC 7386 merge patch the
 * backend expects.
 *
 * The console never PUTs the whole config: two operators editing different
 * sections would then silently overwrite each other, and a stale read would undo
 * a change made on the host. Instead each edit is sent as the smallest nested
 * object that names its own path, which is also why a 422 can point at an exact
 * JSON path and be shown verbatim.
 */

export function getPath(obj: unknown, path: string): unknown {
  let cur: unknown = obj
  for (const key of path.split('.')) {
    if (cur === null || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[key]
  }
  return cur
}

/** Build one nested object from dotted-path edits: {"a.b": 1} -> {a:{b:1}} */
export function buildPatch(edits: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [path, value] of Object.entries(edits)) {
    const parts = path.split('.')
    let cur = out
    for (let i = 0; i < parts.length - 1; i++) {
      const k = parts[i]
      const next = cur[k]
      if (next === undefined || next === null || typeof next !== 'object') cur[k] = {}
      cur = cur[k] as Record<string, unknown>
    }
    cur[parts[parts.length - 1]] = value
  }
  return out
}

/** The `_doc` string sitting beside a value, used as fallback help text. */
export function docFor(config: unknown, path: string): string | null {
  const parts = path.split('.')
  parts.pop()
  const parent = parts.length ? getPath(config, parts.join('.')) : config
  if (parent && typeof parent === 'object') {
    const d = (parent as Record<string, unknown>)._doc
    if (typeof d === 'string') return d
  }
  return null
}
