import { useEffect, useEffectEvent } from 'react'

export type ShortcutHandlers = {
  selectLayerIndex: (index: number) => void
  stepLayer: (step: -1 | 1) => void
  stepScene: (step: -1 | 1) => void
  /** Moves the image by metres east (dx) and north (dy). */
  nudge: (dx: number, dy: number) => void
  clearMeasure: () => void
  setHideResults: (hidden: boolean) => void
}

const FINE_NUDGE_M = 0.05
const COARSE_NUDGE_M = 0.5

/** Unit direction of each arrow key in the map plane: x east, y north. */
const ARROW_DIRECTIONS: Record<string, [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, 1],
  ArrowDown: [0, -1],
}

/** Shortcuts stay off while the user types into a field, so arrow keys keep their normal meaning there. */
function isTextEntry(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)
}

function handleKeyDown(event: KeyboardEvent, handlers: ShortcutHandlers): void {
  if (event.ctrlKey || event.metaKey || isTextEntry(event.target)) return

  if (event.code === 'Space') {
    event.preventDefault()
    if (!event.repeat) handlers.setHideResults(true)
    return
  }
  if (event.key === 'Escape') {
    handlers.clearMeasure()
    return
  }
  if (/^[1-9]$/.test(event.key)) {
    handlers.selectLayerIndex(Number(event.key) - 1)
    return
  }
  if (event.key === '[') {
    handlers.stepScene(-1)
    return
  }
  if (event.key === ']') {
    handlers.stepScene(1)
    return
  }

  const direction = ARROW_DIRECTIONS[event.key]
  if (direction === undefined) return
  if (event.shiftKey) {
    event.preventDefault()
    const metres = event.altKey ? COARSE_NUDGE_M : FINE_NUDGE_M
    handlers.nudge(direction[0] * metres, direction[1] * metres)
    return
  }
  if (event.key === 'ArrowLeft') handlers.stepLayer(-1)
  if (event.key === 'ArrowRight') handlers.stepLayer(1)
}

/**
 * Global keyboard shortcuts of the viewer. Listeners are attached once; the latest handlers are
 * read through an Effect Event, so the caller can pass fresh closures on every render.
 */
export function useKeyboardShortcuts(handlers: ShortcutHandlers): void {
  const onKeyDown = useEffectEvent((event: KeyboardEvent) => handleKeyDown(event, handlers))
  const onKeyUp = useEffectEvent((event: KeyboardEvent) => {
    if (event.code === 'Space') handlers.setHideResults(false)
  })
  const onWindowBlur = useEffectEvent(() => handlers.setHideResults(false))

  useEffect(function listenForKeyboardShortcuts() {
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    window.addEventListener('blur', onWindowBlur)
    return function removeKeyboardShortcuts() {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
      window.removeEventListener('blur', onWindowBlur)
    }
  }, [])
}
