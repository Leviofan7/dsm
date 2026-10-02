"use client"

import * as React from "react"
import { Monitor, Zap } from "lucide-react"

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Tooltip, TooltipContent, TooltipTrigger, TooltipProvider } from "@/components/ui/tooltip"

export function ComplexityToggle({
  value,
  onChange,
}: {
  value: "light" | "heavy" | "auto"
  onChange: (val: "light" | "heavy" | "auto") => void
}) {
  const [mounted, setMounted] = React.useState(false)

  React.useEffect(() => {
    setMounted(true)
  }, [])

  if (!mounted) {
    return <div className="h-9 w-[150px] animate-pulse rounded-md bg-muted" />
  }

  return (
    <TooltipProvider>
      <ToggleGroup 
        type="single" 
        value={value} 
        onValueChange={(v) => {
          if (v) onChange(v as "light" | "heavy" | "auto")
        }}
        size="sm"
        className="border rounded-md px-1"
      >
        <Tooltip>
          {/* Base UI не поддерживает asChild: с ним Trigger рендерит свой <button> вокруг
              ToggleGroupItem → <button> внутри <button> и ошибка гидратации. Используем render. */}
          <TooltipTrigger render={<ToggleGroupItem value="auto" aria-label="Auto" />}>
            <span>Auto</span>
          </TooltipTrigger>
          <TooltipContent>
            <p>Автоматический выбор модели</p>
          </TooltipContent>
        </Tooltip>
        
        <Tooltip>
          <TooltipTrigger render={<ToggleGroupItem value="light" aria-label="Light mode" className="data-[state=on]:text-green-500" />}>
            <Zap className="h-4 w-4 mr-1" />
            <span>Лёгкий</span>
          </TooltipTrigger>
          <TooltipContent>
            <p>Локальные модели и инструменты</p>
          </TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger render={<ToggleGroupItem value="heavy" aria-label="Heavy mode" className="data-[state=on]:text-red-500" />}>
            <Monitor className="h-4 w-4 mr-1" />
            <span>Тяжёлый</span>
          </TooltipTrigger>
          <TooltipContent>
            <p>Облачные модели и инструменты</p>
          </TooltipContent>
        </Tooltip>
      </ToggleGroup>
    </TooltipProvider>
  )
}
