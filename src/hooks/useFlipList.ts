import { useLayoutEffect, useRef } from 'react'

type ElementMap = Record<string, HTMLElement | null>

export const useFlipList = (ids: string[], durationMs = 180) => {
  const elementsRef = useRef<ElementMap>({})
  const positionsRef = useRef<Map<string, DOMRect>>(new Map())

  const setRef = (id: string) => (el: HTMLElement | null) => {
    if (el) {
      elementsRef.current[id] = el
    } else {
      delete elementsRef.current[id]
    }
  }

  useLayoutEffect(() => {
    const nextPositions = new Map<string, DOMRect>()

    ids.forEach((id) => {
      const el = elementsRef.current[id]
      if (!el) return
      const rect = el.getBoundingClientRect()
      nextPositions.set(id, rect)

      const prev = positionsRef.current.get(id)
      if (!prev) return
      const dx = prev.left - rect.left
      const dy = prev.top - rect.top
      if (dx === 0 && dy === 0) return

      el.animate(
        [
          { transform: `translate(${dx}px, ${dy}px)` },
          { transform: 'translate(0, 0)' }
        ],
        { duration: durationMs, easing: 'ease-out' }
      )
    })

    positionsRef.current = nextPositions
  }, [ids.join('|'), durationMs])

  return setRef
}
