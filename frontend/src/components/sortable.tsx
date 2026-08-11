/** Column sorting shared by every data table. */

import { useMemo, useState } from 'react'

export type Dir = 'asc' | 'desc'

export interface Col<T> {
  id: string
  label: string
  /** Value used for sorting. Omit to make the column unsortable. */
  get?: (row: T) => number | string
  num?: boolean
  hint?: string
}

export function useSort<T>(rows: T[], cols: Col<T>[], initial: string, initialDir: Dir = 'desc') {
  const [by, setBy] = useState(initial)
  const [dir, setDir] = useState<Dir>(initialDir)

  const toggle = (id: string) => {
    const col = cols.find((c) => c.id === id)
    if (!col?.get) return
    if (id === by) setDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else { setBy(id); setDir(col.num === false ? 'asc' : 'desc') }
  }

  const sorted = useMemo(() => {
    const col = cols.find((c) => c.id === by)
    if (!col?.get) return rows
    const mul = dir === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const x = col.get!(a), y = col.get!(b)
      if (typeof x === 'number' && typeof y === 'number') return (x - y) * mul
      return String(x).localeCompare(String(y)) * mul
    })
  }, [rows, cols, by, dir])

  return { sorted, by, dir, toggle }
}

/** A sortable <th>. Shows the active direction, and is a real button for a11y. */
export function SortTh<T>(
  { col, by, dir, onSort }:
  { col: Col<T>; by: string; dir: Dir; onSort: (id: string) => void },
) {
  const active = by === col.id
  const sortable = !!col.get
  return (
    <th className={`th ${col.num ? 'text-right' : 'text-left'}`} title={col.hint}
        aria-sort={active ? (dir === 'asc' ? 'ascending' : 'descending') : undefined}>
      {sortable ? (
        <button onClick={() => onSort(col.id)}
                className={`inline-flex items-center gap-1 hover:text-ink transition-colors
                            duration-100 ${active ? 'text-ink' : ''}
                            ${col.num ? 'flex-row-reverse' : ''}`}>
          <span>{col.label}</span>
          <span className={`text-[9px] leading-none ${active ? 'opacity-100' : 'opacity-25'}`}>
            {active ? (dir === 'asc' ? '▲' : '▼') : '▼'}
          </span>
        </button>
      ) : col.label}
    </th>
  )
}

/** Segmented control for filters like All / Gainers / Losers. */
export function Segmented<T extends string>(
  { options, value, onChange }:
  { options: { id: T; label: string }[]; value: T; onChange: (v: T) => void },
) {
  return (
    <div className="inline-flex rounded-card border border-line overflow-hidden">
      {options.map((o, i) => (
        <button key={o.id} onClick={() => onChange(o.id)}
          className={`h-8 px-3 text-micro font-medium transition-colors duration-100
            ${i > 0 ? 'border-l border-line' : ''}
            ${value === o.id ? 'bg-surface text-ink' : 'text-muted hover:text-ink'}`}>
          {o.label}
        </button>
      ))}
    </div>
  )
}
