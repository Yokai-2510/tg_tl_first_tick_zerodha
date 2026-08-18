/**
 * Transient notifications. Kept out of the data store so a toast never causes a
 * screen holding live prices to re-render its tables.
 */
import { create } from 'zustand'

export interface Toast { id: number; title: string; body: string }

interface ToastState {
  items: Toast[]
  push: (title: string, body: string) => void
  drop: (id: number) => void
}

export const useToasts = create<ToastState>((set) => ({
  items: [],
  push(title, body) {
    const id = Date.now() + Math.random()
    set((s) => ({ items: [...s.items, { id, title, body }] }))
    window.setTimeout(() => set((s) => ({ items: s.items.filter((t) => t.id !== id) })), 4000)
  },
  drop(id) {
    set((s) => ({ items: s.items.filter((t) => t.id !== id) }))
  },
}))

export const toast = (title: string, body: string) => useToasts.getState().push(title, body)
