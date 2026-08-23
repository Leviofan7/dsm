"use client"

import { useState, useRef, useEffect } from "react"
import { Database, ChevronDown, Check } from "lucide-react"
import type { ConnectedSource } from "@/lib/data"
import { cn } from "@/lib/utils"

export function SourceSelector({
  allSources,
  activeSourceIds,
  onChange,
}: {
  allSources: ConnectedSource[]
  activeSourceIds: string[]
  onChange: (ids: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // Indexed or ready sources
  const indexedSources = allSources.filter((s) => s.status === "indexed" || s.status === "ready")

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [])

  function toggle(id: string) {
    if (activeSourceIds.includes(id)) {
      onChange(activeSourceIds.filter((s) => s !== id))
    } else {
      onChange([...activeSourceIds, id])
    }
  }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
      >
        <Database className="size-3.5" />
        {activeSourceIds.length === 0
          ? "Выбрать источники"
          : `${activeSourceIds.length} источник${activeSourceIds.length > 1 ? "ов" : ""}`}
        <ChevronDown className={cn("size-3 transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 w-72 rounded-lg border border-border bg-popover p-1 shadow-lg">
          {indexedSources.length === 0 ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">
              Нет проиндексированных источников
            </p>
          ) : (
            indexedSources.map((src) => {
              const active = activeSourceIds.includes(src.id)
              return (
                <button
                  key={src.id}
                  onClick={() => toggle(src.id)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-accent",
                    active && "bg-accent/60"
                  )}
                >
                  <div
                    className={cn(
                      "flex size-4 shrink-0 items-center justify-center rounded border",
                      active
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border"
                    )}
                  >
                    {/* Always render Check, toggle visibility via CSS to prevent React hydration removeChild errors */}
                    <Check className={cn("size-3", !active && "opacity-0")} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-medium">{src.name}</p>
                    <p className="truncate text-[10px] text-muted-foreground">{src.detail}</p>
                  </div>
                  <span className="shrink-0 text-[10px] text-muted-foreground">
                    {src.files} files
                  </span>
                </button>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
