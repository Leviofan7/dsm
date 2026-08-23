"use client"

import { useState } from "react"
import { Check, X, Edit, ShieldAlert, AlertTriangle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

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

  const isPrivileged = step.proposed_tool === "run_claude_coder" || step.proposed_tool === "run_terminal_command"

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
