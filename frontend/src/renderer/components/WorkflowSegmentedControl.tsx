import { useCallback, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'

export type WorkflowMode = 'speech_logic' | 'random' | 'dedup' | 'link_watermark' | 'subtitle'

const modes: { value: WorkflowMode; label: string }[] = [
  { value: 'speech_logic', label: '口播逻辑（测试版v0.1）' },
  { value: 'random', label: '随机混剪' },
  { value: 'dedup', label: '独立去重变换' },
  { value: 'link_watermark', label: '短视频链接一键去水印' },
  { value: 'subtitle', label: '成片转字幕' },
]

interface Props {
  value: WorkflowMode
  onChange: (mode: WorkflowMode) => void
}

interface DragState {
  pointerId: number
  startX: number
  moved: boolean
  lastIndex: number
  originMode: WorkflowMode
}

export function WorkflowSegmentedControl({ value, onChange }: Props) {
  const selectedIndex = modes.findIndex(mode => mode.value === value)
  const groupRef = useRef<HTMLDivElement>(null)
  const buttonRefs = useRef<(HTMLButtonElement | null)[]>([])
  const dragRef = useRef<DragState | null>(null)
  const suppressClickRef = useRef(false)
  const [thumb, setThumb] = useState({ left: 0, width: 0 })
  const [dragLeft, setDragLeft] = useState<number | null>(null)

  const measure = useCallback(() => {
    const button = buttonRefs.current[selectedIndex]
    if (!button) return
    setThumb(current => current.left === button.offsetLeft && current.width === button.offsetWidth
      ? current : { left: button.offsetLeft, width: button.offsetWidth })
  }, [selectedIndex])

  useLayoutEffect(() => {
    measure()
    const group = groupRef.current
    if (!group || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(group)
    buttonRefs.current.forEach(button => { if (button) observer.observe(button) })
    return () => observer.disconnect()
  }, [measure])

  const indexAt = (clientX: number) => {
    const group = groupRef.current
    if (!group) return selectedIndex
    const x = clientX - group.getBoundingClientRect().left
    let nearest = 0
    let distance = Infinity
    buttonRefs.current.forEach((button, index) => {
      if (!button) return
      const delta = Math.abs(button.offsetLeft + button.offsetWidth / 2 - x)
      if (delta < distance) { distance = delta; nearest = index }
    })
    return nearest
  }

  const followPointer = (clientX: number, index: number) => {
    const group = groupRef.current
    const button = buttonRefs.current[index]
    const first = buttonRefs.current[0]
    const last = buttonRefs.current[modes.length - 1]
    if (!group || !button || !first || !last) return
    const localX = clientX - group.getBoundingClientRect().left
    const min = first.offsetLeft
    const max = last.offsetLeft + last.offsetWidth - button.offsetWidth
    setDragLeft(Math.max(min, Math.min(max, localX - button.offsetWidth / 2)))
  }

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    const button = (event.target as HTMLElement).closest('button[data-workflow-mode]')
    if (!button) return
    const index = indexAt(event.clientX)
    dragRef.current = { pointerId: event.pointerId, startX: event.clientX, moved: false, lastIndex: index, originMode: value }
    event.currentTarget.setPointerCapture(event.pointerId)
    followPointer(event.clientX, index)
  }

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    if (!drag.moved && Math.abs(event.clientX - drag.startX) < 4) return
    drag.moved = true
    const index = indexAt(event.clientX)
    followPointer(event.clientX, index)
    if (index !== drag.lastIndex) {
      drag.lastIndex = index
      onChange(modes[index].value)
    }
  }

  const finishDrag = (event: PointerEvent<HTMLDivElement>, cancelled = false) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    dragRef.current = null
    setDragLeft(null)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    if (cancelled) {
      onChange(drag.originMode)
    } else {
      const index = indexAt(event.clientX)
      onChange(modes[index].value)
      if (drag.moved) {
        suppressClickRef.current = true
        window.setTimeout(() => { suppressClickRef.current = false }, 0)
      }
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    let index = selectedIndex
    if (event.key === 'ArrowRight') index = Math.min(modes.length - 1, selectedIndex + 1)
    else if (event.key === 'ArrowLeft') index = Math.max(0, selectedIndex - 1)
    else if (event.key === 'Home') index = 0
    else if (event.key === 'End') index = modes.length - 1
    else return
    event.preventDefault()
    onChange(modes[index].value)
    buttonRefs.current[index]?.focus()
  }

  return (
    <div ref={groupRef} role="group" aria-label="你想让我怎么做？" className="vm-mode-switch"
      onPointerDown={onPointerDown} onPointerMove={onPointerMove}
      onPointerUp={event => finishDrag(event)} onPointerCancel={event => finishDrag(event, true)}
      onKeyDown={onKeyDown}>
      <span aria-hidden="true" data-testid="workflow-thumb" className={`vm-mode-thumb ${dragLeft !== null ? 'is-dragging' : ''}`}
        style={{ left: dragLeft ?? thumb.left, width: thumb.width }} />
      {modes.map((mode, index) => (
        <button key={mode.value} ref={button => { buttonRefs.current[index] = button }}
          type="button" data-workflow-mode={mode.value} aria-pressed={value === mode.value}
          onClick={() => {
            if (suppressClickRef.current) { suppressClickRef.current = false; return }
            onChange(mode.value)
          }}>
          {mode.label}
        </button>
      ))}
    </div>
  )
}
