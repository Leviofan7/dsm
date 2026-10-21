"use client"

import { useState } from "react"
import { Check, X, Edit, ShieldAlert, AlertTriangle, ListChecks } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

/** Сентинел бэкенда: шаг «план», а не вызов инструмента (см. services/permissions.py). */
export const PLAN_TOOL = "__plan__"

export interface ApprenticeStep {
  id: string
  proposed_tool: string | null
  proposed_args: any
  proposed_reasoning: string
  proposed_response_text: string | null
}

interface ApprenticeCardProps {
  step: ApprenticeStep
  onAccept: (id: string) => Promise<{ status: string; action_id?: string }>
  onReject: (id: string) => Promise<void>
  onCorrect: (id: string, correctedArgs: any, correctedReasoning: string) => Promise<void>
}

export function ApprenticeCard({ step, onAccept, onReject, onCorrect }: ApprenticeCardProps) {
  const [isEditing, setIsEditing] = useState(false)
  const [editedArgs, setEditedArgs] = useState(JSON.stringify(step.proposed_args || {}, null, 2))
  const [editedReasoning, setEditedReasoning] = useState(step.proposed_reasoning || "")
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [elevationRequired, setElevationRequired] = useState(false)

  const isPlan = step.proposed_tool === PLAN_TOOL
  const planSteps: string[] = Array.isArray(step.proposed_args?.steps)
    ? (step.proposed_args.steps as string[])
    : []
  // План редактируется построчно: человеку понятнее список шагов, чем JSON.
  const [planText, setPlanText] = useState(planSteps.join("\n"))

  const isPrivileged = step.proposed_tool === "run_claude_coder" || step.proposed_tool === "run_terminal_command"

  const handleCorrectPlan = async () => {
    setIsSubmitting(true)
    try {
      const steps = planText
        .split("\n")
        .map((s) => s.trim().replace(/^\d+[.)]\s*/, ""))
        .filter(Boolean)
        .slice(0, 3)
      await onCorrect(step.id, { steps }, editedReasoning)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleAccept = async () => {
    setIsSubmitting(true)
    try {
      const res = await onAccept(step.id)
      if (res.status === "elevation_required") {
        setElevationRequired(true)
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleReject = async () => {
    setIsSubmitting(true)
    try {
      await onReject(step.id)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleCorrect = async () => {
    setIsSubmitting(true)
    try {
      const parsedArgs = JSON.parse(editedArgs)
      await onCorrect(step.id, parsedArgs, editedReasoning)
    } catch (e) {
      alert("Invalid JSON format for arguments")
    } finally {
      setIsSubmitting(false)
    }
  }

  if (elevationRequired) {
    return (
      <div className="rounded-xl border border-red-500/50 bg-red-500/10 p-4 mb-4 shadow-sm relative overflow-hidden flex flex-col gap-3">
        <div className="flex items-center gap-2 text-red-500 font-semibold">
          <ShieldAlert className="size-5" />
          <span>Требуется системное подтверждение (Elevation Required)</span>
        </div>
        <p className="text-sm text-muted-foreground">
          Вы одобрили использование привилегированного инструмента <strong>{step.proposed_tool}</strong>. 
          Это действие заблокировано на уровне сессии и требует полного Approve через панель администратора.
        </p>
        <Button variant="outline" className="w-full mt-2 border-red-500 text-red-500 hover:bg-red-500/20" disabled>
          Ожидание проверки безопасности...
        </Button>
      </div>
    )
  }

  // ── План перед действием ──────────────────────────────────────────
  // Показываем намерение ДО первого инструмента: человек видит 2–3 шага и решает,
  // пускать ли агента к работе. Раньше первым, что он видел, был уже готовый вызов.
  if (isPlan) {
    return (
      <div className="rounded-xl border border-blue-500/40 bg-blue-500/5 p-4 mb-4 shadow-sm">
        <div className="flex items-center gap-2 mb-3">
          <ListChecks className="size-5 text-blue-500" />
          <h3 className="font-semibold text-sm">Агент предлагает план</h3>
          <span className="ml-auto text-[10px] uppercase tracking-wide text-muted-foreground">
            до первого действия
          </span>
        </div>

        {!isEditing ? (
          <ol className="space-y-1.5 text-sm">
            {planSteps.map((s, i) => (
              <li key={i} className="flex gap-2 rounded-lg border border-border/50 bg-background/50 px-3 py-2">
                <span className="font-mono text-xs text-blue-500 shrink-0">{i + 1}.</span>
                <span className="min-w-0">{s}</span>
              </li>
            ))}
            {planSteps.length === 0 && (
              <li className="text-sm text-muted-foreground">Модель не сформулировала план.</li>
            )}
          </ol>
        ) : (
          <div className="space-y-2 text-sm">
            <label className="font-semibold text-muted-foreground block text-xs">
              Шаги плана (по одному в строке, максимум 3):
            </label>
            <Textarea
              value={planText}
              onChange={(e) => setPlanText(e.target.value)}
              className="text-xs font-mono min-h-24"
            />
          </div>
        )}

        <div className="flex items-center gap-2 mt-4 pt-3 border-t border-border/50">
          {!isEditing ? (
            <>
              <Button
                onClick={handleAccept}
                disabled={isSubmitting}
                size="sm"
                className="bg-green-600 hover:bg-green-700"
              >
                <Check className="size-4 mr-1" />
                Разрешить выполнение
              </Button>
              <Button onClick={() => setIsEditing(true)} disabled={isSubmitting} size="sm" variant="outline">
                <Edit className="size-4 mr-1" />
                Изменить план
              </Button>
              <Button onClick={handleReject} disabled={isSubmitting} size="sm" variant="destructive" className="ml-auto">
                <X className="size-4 mr-1" />
                Отклонить
              </Button>
            </>
          ) : (
            <>
              <Button onClick={handleCorrectPlan} disabled={isSubmitting} size="sm" className="bg-blue-600 hover:bg-blue-700 text-white">
                <Check className="size-4 mr-1" />
                Утвердить изменённый план
              </Button>
              <Button onClick={() => setIsEditing(false)} disabled={isSubmitting} size="sm" variant="ghost">
                Отмена
              </Button>
            </>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className={`rounded-xl border ${isPrivileged ? 'border-amber-500/50 bg-amber-500/10' : 'border-border bg-card'} p-4 mb-4 shadow-sm`}>
      <div className="flex items-center gap-2 mb-3">
        {isPrivileged ? (
          <ShieldAlert className="size-5 text-amber-500" />
        ) : (
          <AlertTriangle className="size-5 text-blue-500" />
        )}
        <h3 className="font-semibold text-sm">
          Ученик предлагает: {step.proposed_tool ? `Вызов инструмента ${step.proposed_tool}` : "Финальный ответ"}
        </h3>
      </div>
      
      {!isEditing ? (
        <div className="space-y-3 text-sm">
          <div className="bg-background/50 p-3 rounded-lg border border-border/50">
            <span className="font-semibold text-muted-foreground block mb-1">Мысли / Аргументация:</span>
            {step.proposed_reasoning}
          </div>
          
          {step.proposed_tool && (
            <div className="bg-background/50 p-3 rounded-lg border border-border/50 font-mono text-xs overflow-x-auto">
              <span className="font-semibold text-muted-foreground block mb-1">Аргументы ({step.proposed_tool}):</span>
              <pre>{JSON.stringify(step.proposed_args, null, 2)}</pre>
            </div>
          )}

          {step.proposed_response_text && (
            <div className="bg-background/50 p-3 rounded-lg border border-border/50">
              <span className="font-semibold text-muted-foreground block mb-1">Текст ответа:</span>
              {step.proposed_response_text}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-3 text-sm mt-3">
          <div>
            <label className="font-semibold text-muted-foreground block mb-1 text-xs">Скорректировать мысли:</label>
            <Textarea 
              value={editedReasoning} 
              onChange={(e) => setEditedReasoning(e.target.value)} 
              className="text-xs font-mono"
            />
          </div>
          {step.proposed_tool && (
            <div>
              <label className="font-semibold text-muted-foreground block mb-1 text-xs">Скорректировать аргументы (JSON):</label>
              <Textarea 
                value={editedArgs} 
                onChange={(e) => setEditedArgs(e.target.value)} 
                className="text-xs font-mono h-32"
              />
            </div>
          )}
        </div>
      )}

      <div className="flex items-center gap-2 mt-4 pt-3 border-t border-border/50">
        {!isEditing ? (
          <>
            <Button 
              onClick={handleAccept} 
              disabled={isSubmitting}
              size="sm" 
              variant="default"
              className={isPrivileged ? "bg-amber-600 hover:bg-amber-700 text-white" : "bg-green-600 hover:bg-green-700"}
            >
              <Check className="size-4 mr-1" />
              {isPrivileged ? "Отправить на верификацию" : step.proposed_tool ? "Принять" : "Отправить ответ"}
            </Button>
            {/* Кнопка "Исправить" только для tool_calls — для текстовых ответов нет JSON-аргументов */}
            {step.proposed_tool && (
              <Button onClick={() => setIsEditing(true)} disabled={isSubmitting} size="sm" variant="outline">
                <Edit className="size-4 mr-1" />
                Исправить
              </Button>
            )}
            <Button onClick={handleReject} disabled={isSubmitting} size="sm" variant="destructive" className="ml-auto">
              <X className="size-4 mr-1" />
              Отклонить
            </Button>
          </>
        ) : (
          <>
            <Button onClick={handleCorrect} disabled={isSubmitting} size="sm" className="bg-blue-600 hover:bg-blue-700 text-white">
              <Check className="size-4 mr-1" />
              Сохранить исправления
            </Button>
            <Button onClick={() => setIsEditing(false)} disabled={isSubmitting} size="sm" variant="ghost">
              Отмена
            </Button>
          </>
        )}
      </div>
    </div>
  )
}
