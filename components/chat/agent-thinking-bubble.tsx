"use client"

import { useState, useEffect } from "react"
import {
  ChevronDown, Brain, CheckCircle2, XCircle, Globe,
  Camera, Zap, Loader2, ShieldAlert, ImageIcon,
} from "lucide-react"
import { cn } from "@/lib/utils"

export interface AgentThinkingStep {
  id: string
  type: "step" | "error" | "done"
  message: string
  timestamp: number
  /** Base64 screenshot attached to block/error events */
  screenshot?: string
}

interface AgentThinkingBubbleProps {
  steps: AgentThinkingStep[]
  isWorking: boolean
}

function stepIcon(message: string, type: AgentThinkingStep["type"]) {
  if (type === "error") return <XCircle className="size-3.5 shrink-0 text-red-400" />
  if (type === "done") return <CheckCircle2 className="size-3.5 shrink-0 text-emerald-400" />
  const m = message.toLowerCase()
  if (m.includes("блокировк") || m.includes("403") || m.includes("captcha") || m.includes("resilience"))
    return <ShieldAlert className="size-3.5 shrink-0 text-amber-400" />
  if (m.includes("браузер") || m.includes("browser") || m.includes("playwright"))
    return <Globe className="size-3.5 shrink-0 text-blue-400" />
  if (m.includes("vision") || m.includes("скриншот") || m.includes("анализ"))
    return <Camera className="size-3.5 shrink-0 text-purple-400" />
  if (m.includes("ответ") || m.includes("fast") || m.includes("генер"))
    return <Zap className="size-3.5 shrink-0 text-amber-400" />
  return <Loader2 className="size-3.5 shrink-0 animate-spin text-muted-foreground" />
}

function ScreenshotPreview({ base64 }: { base64: string }) {
  const [expanded, setExpanded] = useState(false)
  const src = `data:image/png;base64,${base64}`

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setExpanded(v => !v)}
        className="inline-flex items-center gap-1.5 rounded-md border border-red-500/20 bg-red-500/5 px-2 py-1 text-[10px] text-red-400 hover:bg-red-500/10 transition-colors"
      >
        <ImageIcon className="size-3" />
        {expanded ? "Свернуть скриншот" : "Показать скриншот блокировки"}
      </button>
      {expanded && (
        <div className="mt-2 rounded-lg border border-red-500/20 overflow-hidden animate-in slide-in-from-top-2 duration-200">
          <img
            src={src}
            alt="Скриншот блокировки"
            className="w-full max-h-[300px] object-contain bg-black/50"
          />
        </div>
      )}
    </div>
  )
}

export function AgentThinkingBubble({ steps, isWorking }: AgentThinkingBubbleProps) {
  const [expanded, setExpanded] = useState(isWorking)

  useEffect(() => {
    setExpanded(isWorking)
  }, [isWorking])


  const lastStep = steps[steps.length - 1]
  const hasError = steps.some(s => s.type === "error")
  const isDone = !isWorking

  const headerColor = hasError
    ? "border-red-500/30 bg-red-500/5 text-red-400"
    : isDone
    ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-400"
    : "border-purple-500/30 bg-purple-500/5 text-purple-400"

  const headerBadge = hasError
    ? "Ошибка"
    : isDone
    ? "Завершено"
    : "Выполняется…"

  return (
    <div className="flex gap-3 animate-in fade-in-50 duration-300">
      {/* Avatar */}
      <div className={cn(
        "grid size-8 shrink-0 place-items-center rounded-full border",
        hasError
          ? "border-red-500/30 bg-red-500/10 text-red-400"
          : isDone
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
          : "border-purple-500/30 bg-purple-500/10 text-purple-400",
      )}>
        <Brain className={cn("size-4", isWorking && !hasError && "animate-pulse")} />
      </div>

      {/* Card */}
      <div className={cn(
        "min-w-0 flex-1 rounded-2xl rounded-tl-sm border overflow-hidden",
        hasError
          ? "border-red-500/20 bg-red-500/[0.03]"
          : isDone
          ? "border-emerald-500/20 bg-emerald-500/[0.03]"
          : "border-purple-500/20 bg-purple-500/[0.03]",
      )}>
        {/* Header — always visible, click to toggle */}
        <button
          type="button"
          onClick={() => setExpanded(v => !v)}
          className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-white/5"
        >
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm font-semibold truncate">
              {lastStep ? lastStep.message : "Запуск агента…"}
            </span>
            <span className={cn(
              "shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider",
              headerColor,
            )}>
              {headerBadge}
            </span>
            {isWorking && !hasError && (
              <span className="relative flex size-2 shrink-0">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-purple-400 opacity-75" />
                <span className="relative inline-flex size-2 rounded-full bg-purple-500" />
              </span>
            )}
          </div>
          <ChevronDown className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform duration-200",
            expanded && "rotate-180",
          )} />
        </button>

        {/* Expandable log */}
        {expanded && (
          <div className="border-t border-white/5 px-4 pb-4 pt-3">
            <ol className="flex flex-col gap-2.5">
              {steps.map((step, i) => (
                <li key={step.id} className="flex items-start gap-2.5">
                  {/* Connector line + icon */}
                  <div className="flex flex-col items-center gap-1 pt-0.5">
                    {stepIcon(step.message, step.type)}
                    {i < steps.length - 1 && (
                      <span className="w-px flex-1 min-h-[10px] bg-border/60" />
                    )}
                  </div>
                  {/* Text + optional screenshot */}
                  <div className="flex-1 min-w-0 pb-1">
                    <p className={cn(
                      "text-xs leading-relaxed",
                      step.type === "error" && "text-red-400",
                      step.type === "done" && "text-emerald-400",
                      step.type === "step" && "text-muted-foreground",
                      // Last step if working — highlight it
                      i === steps.length - 1 && isWorking && step.type === "step" && "text-foreground",
                    )}>
                      {step.message}
                      {i === steps.length - 1 && isWorking && step.type === "step" && (
                        <span className="ml-1 inline-block w-1.5 h-3 align-middle bg-purple-400 animate-pulse rounded-sm" />
                      )}
                    </p>
                    {step.screenshot && (
                      <ScreenshotPreview base64={step.screenshot} />
                    )}
                  </div>
                </li>
              ))}
            </ol>
            <p className="mt-3 text-right text-[10px] text-muted-foreground/50">
              {steps.length} {steps.length === 1 ? "шаг" : steps.length < 5 ? "шага" : "шагов"}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
