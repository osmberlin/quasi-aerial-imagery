import type { StateStorage } from 'zustand/middleware'

/** localStorage that never throws (private windows, blocked site data). Failures fall back to memory. */
export const safeLocalStorage: StateStorage = {
  getItem: (name) => {
    try {
      return window.localStorage.getItem(name)
    } catch {
      return null
    }
  },
  setItem: (name, value) => {
    try {
      window.localStorage.setItem(name, value)
    } catch {
      // Storage unavailable or full: the in-memory state still works.
    }
  },
  removeItem: (name) => {
    try {
      window.localStorage.removeItem(name)
    } catch {
      // See setItem.
    }
  },
}
