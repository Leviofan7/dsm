"use client"

import { useState } from "react"
import {
  AlertTriangle,
  Brain,
  ChevronDown,
  ChevronRight,
  Lightbulb,
  Wrench,
} from "lucide-react"
import { cn } from "@/lib/utils"

/**
 * Самоанализ прогона: класс проблемы, подсказки, чем работали и ревизия аналитика.
 *
 * Данные приходят из `journal.self_review` (services/review.py). Класс проблемы — уже
 * посчитанный на бэкенде по фактам, поэтому здесь нет «магии»: только показ. Если
 * `llm_review` нет — значит ревизия не запускалась, и это честно видно (не рисуем пустую
 * секцию «аналитика»).
 */

export type SelfReview = {
  outcome?: { status?: string; summary?: string | null }
  failure_mode?: string
  used_tools?: string[]
  models?: string[]
  signals?: Record<string, any>
  improvement_hints?: string[]
  generated_at?: string
  llm_review?: { text?: string; generated_at?: string }
}

const MODE: Record<string, { label: string; className: string }> = {
  none: { label: "Отклонений нет", className: "text-emerald-600" },
  cancelled: { label: "Прогон отменён", className: "text-amber-600" },
  stub_response: { label: "Модель ответила заглушкой", className: "text-red-500" },
  timeout: { label: "Превышен лимит раундов", className: "text-amber-600" },
  provider_unavailable: { label: "Провайдер недоступен", className: "text-red-500" },
  verification_failed: { label: "Инструмент не дал эффекта", className: "text-red-500" },
  no_traces: { label: "Прогон умер до первого шага", className: "text-red-500" },
  model_declined_tools: { label: "Модель не вызвала инструменты", className: "text-amber-600" },
  unknown: { label: "Причина не классифицирована", className: "text-muted-foreground" },
}

export function SelfReviewCard({ review }: { review?: SelfReview | null }) {
  const [showLlm, setShowLlm] = useState(false)
  if (!review) return null

  const mode = MODE[review.failure_mode || "unknown"] ?? MODE.unknown
  const signals = review.signals || {}
  const llmText = review.llm_review?.text
  const ok = (review.failure_mode || "none") === "none"

  return (
    <section>
      <h4 className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        Самоанализ
      </h4>
      <div className="space-y-1.5 rounded-md border border-border bg-background/60 p-2 text-[11px]">
        <div className="flex items-center gap-1.5">
          {ok ? (
            <Brain className="size-3.5 shrink-0 text-emerald-600" />
          ) : (
            <AlertTriangle className={cn("size-3.5 shrink-0", mode.className)} />
          )}
          <span className={cn("font-medium", mode.className)}>{mode.label}</span>
          {review.failure_mode && (
            <span className="text-muted-foreground">{review.failure_mode}</span>
          )}
        </div>

        {review.improvement_hints?.length ? (
          <ul className="space-y-0.5">
            {review.improvement_hints.map((h, i) => (
              <li key={i} className="flex items-start gap-1.5">
                <Lightbulb className="mt-0.5 size-3 shrink-0 text-amber-500" />
                <span className="break-words">{h}</span>
              </li>
            ))}
          </ul>
        ) : null}

        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-muted-foreground">
          {review.used_tools?.length ? (
            <span className="inline-flex items-center gap-1">
              <Wrench className="size-3 shrink-0" />
              <span className="break-all">{review.used_tools.join(" → ")}</span>
            </span>
          ) : null}
          {review.models?.length ? <span>модель: {review.models.join(", ")}</span> : null}
          {signals.tool_calls != null ? <span>вызовов: {signals.tool_calls}</span> : null}
          {signals.duration_ms ? (
            <span>{(Number(signals.duration_ms) / 1000).toFixed(1)} с</span>
          ) : null}
          {signals.verification_failures ? (
            <span className="text-red-500">провалов проверки: {signals.verification_failures}</span>
          ) : null}
        </div>

        {llmText ? (
          <div className="pt-0.5">
            <button
              type="button"
              onClick={() => setShowLlm((v) => !v)}
              className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
            >
              {showLlm ? (
                <ChevronDown className="size-3" />
              ) : (
                <ChevronRight className="size-3" />
              )}
              Ревизия аналитика
            </button>
            {showLlm && (
              <p className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">{llmText}</p>
            )}
          </div>
        ) : null}
      </div>
    </section>
  )
}
