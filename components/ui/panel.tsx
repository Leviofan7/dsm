"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { cn } from "@/lib/utils"

/**
 * Общая механика «тянущихся» боковых панелей.
 *
 * Одна реализация на левую (навигация) и правую (контекст/артефакты) панель — иначе
 * получаются два расходящихся поведения при ресайзе. Ширина и свёрнутость помнятся
 * в localStorage: раскладку пользователь настраивает один раз, а не каждый запуск.
 */

export type PanelSide = "left" | "right"

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

export function useResizablePanel(
  storageKey: string,
  { initial, min, max }: { initial: number; min: number; max: number },
) {
  const [width, setWidth] = useState(initial)
  const [collapsed, setCollapsed] = useState(false)
  const hydrated = useRef(false)

  // Читаем сохранённое ПОСЛЕ монтирования: на сервере localStorage нет, а жёсткие
  // значения в useState дали бы расхождение гидратации.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(storageKey)
      if (raw) {
        const saved = JSON.parse(raw) as { width?: number; collapsed?: boolean }
        if (typeof saved.width === "number") setWidth(clamp(saved.width, min, max))
        if (typeof saved.collapsed === "boolean") setCollapsed(saved.collapsed)
      }
    } catch {
      /* мусор в хранилище — просто остаёмся на дефолте */
    }
    hydrated.current = true
  }, [storageKey, min, max])

  useEffect(() => {
    if (!hydrated.current) return
    localStorage.setItem(storageKey, JSON.stringify({ width, collapsed }))
  }, [storageKey, width, collapsed])

  const resize = useCallback(
    (delta: number) => setWidth((w) => clamp(w + delta, min, max)),
    [min, max],
  )
  const reset = useCallback(() => setWidth(initial), [initial])

  return { width, collapsed, setCollapsed, toggle: () => setCollapsed((c) => !c), resize, reset }
}

/**
 * Полоса-разделитель. Тянется указателем (мышь/тач/стилус), двойной клик сбрасывает ширину.
 *
 * `panel` — к какой панели относится ручка: у правой панели рост ширины означает движение
 * влево, поэтому знак дельты инвертируется здесь, а не в разметке.
 */
export function ResizeHandle({
  panel,
  onResize,
  onReset,
  className,
}: {
  panel: PanelSide
  onResize: (deltaPx: number) => void
  onReset?: () => void
  className?: string
}) {
  const dragging = useRef(false)
  const lastX = useRef(0)

  const resetBody = () => {
    document.body.style.cursor = ""
    document.body.style.userSelect = ""
  }

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    dragging.current = true
    lastX.current = e.clientX
    e.currentTarget.setPointerCapture(e.pointerId)
    document.body.style.cursor = "col-resize"
    document.body.style.userSelect = "none"
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return
    const delta = e.clientX - lastX.current
    lastX.current = e.clientX
    onResize(panel === "right" ? -delta : delta)
  }

  const stop = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return
    dragging.current = false
    try {
      e.currentTarget.releasePointerCapture(e.pointerId)
    } catch {
      /* указатель уже отпущен — не наша проблема */
    }
    resetBody()
  }

  // Размонтирование во время перетаскивания не должно оставлять «залипший» курсор
  useEffect(() => resetBody, [])

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Изменить ширину панели"
      title="Потяните, чтобы изменить ширину. Двойной клик — вернуть исходную"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={stop}
      onPointerCancel={stop}
      onDoubleClick={onReset}
      className={cn(
        "group relative z-20 w-1 shrink-0 cursor-col-resize touch-none",
        "before:absolute before:inset-y-0 before:left-0 before:w-1 before:bg-transparent before:transition-colors",
        "hover:before:bg-primary/40 active:before:bg-primary/60",
        className,
      )}
    />
  )
}
