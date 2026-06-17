"use client"

import {
  Camera,
  Check,
  Globe,
  Loader2,
  PhoneCall,
  Terminal,
  type LucideIcon,
} from "lucide-react"
import type { AgentMode, AgentStepKey } from "@/lib/data"
import { cn } from "@/lib/utils"

type Tone = "default" | "warning" | "success"

interface StepDef {
  key: AgentStepKey
  icon: LucideIcon
  title: string
  detail: string
  tone: Tone
}

const STEPS: Record<AgentStepKey, StepDef> = {
  init: {
    key: "init",
    icon: Loader2,
    title: "Инициализация",
    detail: "Запуск внешнего агента…",
    tone: "default",
  },
  browser: {
    key: "browser",
    icon: Globe,
    title: "Эмуляция браузера",
    detail: "Открытие браузера (Playwright)… Маскировка Stealth-режима…",
    tone: "default",
  },
  fasttrack: {
    key: "fasttrack",
    icon: Terminal,
    title: "Fast Track",
    detail: "Ввод промпта в Duck.ai… Ожидание генерации…",
    tone: "default",
  },
  vision: {
    key: "vision",
    icon: Camera,
    title: "Vision Fallback",
    detail:
      "Fast Track недоступен. Снятие скриншота… Анализ через локальную Llava…",
    tone: "warning",
  },
  success: {
    key: "success",
    icon: Check,
    title: "Успех",
    detail: "Ответ получен. Передача данных Джемме…",
    tone: "success",
  },
}

const FAST_FLOW: AgentStepKey[] = ["init", "browser", "fasttrack", "success"]
const VISION_FLOW: AgentStepKey[] = [
  "init",
  "browser",
  "fasttrack",
  "vision",
  "success",
]

type Status = "done" | "active" | "pending"

const toneRing: Record<Tone, string> = {
  default: "border-blue-500/50 text-blue-400",
  warning: "border-amber-500/60 text-amber-400",
  success: "border-emerald-500/60 text-emerald-400",
}

function StepCircle({
  def,
  status,
}: {
  def: StepDef
  status: Status
}) {
  const Icon = status === "done" ? Check : def.icon
  const spin = status === "active" && def.key === "init"

  return (
    <div className="relative grid size-9 shrink-0 place-items-center">
      {/* spinning processing arc for the active step */}
      {status === "active" && (
        <span
          className={cn(
            "absolute inset-0 animate-spin rounded-full border-2 border-transparent",
            def.tone === "warning"
              ? "border-t-amber-400"
              : def.tone === "success"
                ? "border-t-emerald-400"
                : "border-t-blue-400",
          )}
          aria-hidden
        />
      )}
      <span
        className={cn(
          "grid size-9 place-items-center rounded-full border transition-colors",
          status === "done" &&
            "border-emerald-500/40 bg-emerald-500/10 text-emerald-400",
          status === "active" &&
            cn("bg-background", toneRing[def.tone]),
          status === "pending" &&
            "border-border bg-muted/40 text-muted-foreground/60",
        )}
      >
        <Icon className={cn("size-4", spin && "animate-spin")} />
      </span>
    </div>
  )
}

export function AgentStepper({
  activeStep,
  mode,
}: {
  activeStep: AgentStepKey
  mode: AgentMode
}) {
  const flow = mode === "vision" ? VISION_FLOW : FAST_FLOW
  const activeIndex = flow.indexOf(activeStep)

  return (
    <div className="flex gap-3">
      <div className="grid size-8 shrink-0 place-items-center rounded-full border border-blue-500/30 bg-blue-500/10 text-blue-400">
        <PhoneCall className="size-4" />
      </div>

      <div className="min-w-0 flex-1 rounded-2xl rounded-tl-sm border border-blue-500/20 bg-blue-500/[0.04] p-4">
        <div className="mb-3 flex items-center gap-2">
          <span className="text-sm font-semibold">Звонок другу</span>
          <span className="rounded-full border border-blue-500/30 bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-blue-400">
            {mode === "vision" ? "Vision Fallback" : "Fast Track"}
          </span>
        </div>

        <ol className="flex flex-col">
          {flow.map((key, i) => {
            const def = STEPS[key]
            const status: Status =
              i < activeIndex ? "done" : i === activeIndex ? "active" : "pending"
            const isLast = i === flow.length - 1

            return (
              <li key={key} className="flex gap-3">
                <div className="flex flex-col items-center">
                  <StepCircle def={def} status={status} />
                  {!isLast && (
                    <span
                      className={cn(
                        "w-px flex-1 transition-colors",
                        i < activeIndex ? "bg-emerald-500/40" : "bg-border",
                      )}
                    />
                  )}
                </div>

                <div
                  className={cn(
                    "min-w-0 pb-4 pt-1.5 transition-opacity",
                    status === "pending" && "opacity-50",
                    isLast && "pb-0",
                  )}
                >
                  <p
                    className={cn(
                      "text-sm font-medium leading-none",
                      status === "active" &&
                        def.tone === "warning" &&
                        "text-amber-400",
                      status === "active" &&
                        def.tone === "success" &&
                        "text-emerald-400",
                    )}
                  >
                    {def.title}
                  </p>
                  <p className="mt-1 text-pretty text-xs leading-relaxed text-muted-foreground">
                    {def.detail}
                  </p>
                </div>
              </li>
            )
          })}
        </ol>
      </div>
    </div>
  )
}
