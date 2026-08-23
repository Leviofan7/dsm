"use client"

import { useState } from "react"
import { Check, X, Terminal, FileCode2, ClipboardList, AlertTriangle, ChevronDown, ChevronUp } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

// ─── Types ────────────────────────────────────────────────────────────────────

export type ActionType = "RUN_COMMAND" | "REVIEW_PLAN" | "APPLY_DIFF"

export interface ActionRequestData {
  id: string
  coder_task_id: string
  action_type: ActionType
  payload: {
    command?: string
    reason?: string
    plan_spec?: string
    summary?: string
    files_diff?: string
  }
  supervisor_notes?: string
  created_at: string
}

interface ActionRequestCardProps {
  request: ActionRequestData
  /** Called with the request id. Should POST to /api/action-requests/{id}/approve */
  onApprove: (id: string) => Promise<void>
  /** Called with the request id and an optional rejection reason */
  onReject: (id: string, reason: string) => Promise<void>
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function DiffViewer({ diff }: { diff: string }) {
  const lines = diff.split("\n")
  return (
    <div className="font-mono text-xs overflow-x-auto rounded-md border border-white/10 bg-black/40 p-3 max-h-64 overflow-y-auto">
      {lines.map((line, i) => {
        let cls = "text-gray-400"
        if (line.startsWith("+")) cls = "text-emerald-400 bg-emerald-950/40"
        else if (line.startsWith("-")) cls = "text-red-400 bg-red-950/40"
        else if (line.startsWith("@@")) cls = "text-sky-400"
        return (
          <div key={i} className={`px-1 ${cls}`}>
            {line || "\u00a0"}
          </div>
        )
      })}
    </div>
  )
}

function MarkdownBlock({ content }: { content: string }) {
  return (
    <pre className="text-xs text-gray-300 whitespace-pre-wrap font-mono bg-black/40 border border-white/10 rounded-md p-3 max-h-64 overflow-y-auto">
      {content}
    </pre>
  )
}

// ─── Main Component ───────────────────────────────────────────────────────────

export function ActionRequestCard({ request, onApprove, onReject }: ActionRequestCardProps) {
  const [rejectReason, setRejectReason] = useState("")
  const [showRejectInput, setShowRejectInput] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [decision, setDecision] = useState<"approved" | "rejected" | null>(null)
  const [expanded, setExpanded] = useState(true)

  const { action_type, payload, supervisor_notes } = request

  const handleApprove = async () => {
    setIsSubmitting(true)
    try {
      await onApprove(request.id)
      setDecision("approved")
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleReject = async () => {
    setIsSubmitting(true)
    try {
      await onReject(request.id, rejectReason)
      setDecision("rejected")
    } finally {
      setIsSubmitting(false)
    }
  }

  // ── Icon & title by type
  const meta = {
    RUN_COMMAND: {
      icon: <Terminal className="w-4 h-4 text-amber-400" />,
      title: "Запрос на выполнение команды",
      badge: "⚠️ Опасная операция",
      badgeCls: "bg-amber-500/20 text-amber-300 border border-amber-500/30",
    },
    REVIEW_PLAN: {
      icon: <ClipboardList className="w-4 h-4 text-sky-400" />,
      title: "Утверждение плана",
      badge: "📋 Требует одобрения",
      badgeCls: "bg-sky-500/20 text-sky-300 border border-sky-500/30",
    },
    APPLY_DIFF: {
      icon: <FileCode2 className="w-4 h-4 text-violet-400" />,
      title: "Запись изменений в репозиторий",
      badge: "🔀 Критическое действие",
      badgeCls: "bg-violet-500/20 text-violet-300 border border-violet-500/30",
    },
  }[action_type]

  // ── Final state display
  if (decision === "approved") {
    return (
      <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/30 px-4 py-3 flex items-center gap-2 text-emerald-300 text-sm">
        <Check className="w-4 h-4" />
        <span>Одобрено — Кодер продолжает работу</span>
      </div>
    )
  }
  if (decision === "rejected") {
    return (
      <div className="rounded-xl border border-red-500/30 bg-red-950/30 px-4 py-3 flex items-center gap-2 text-red-300 text-sm">
        <X className="w-4 h-4" />
        <span>Отклонено — Кодер получил обратную связь</span>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-white/10 bg-white/5 backdrop-blur-sm overflow-hidden my-2">
      {/* Header */}
      <div
        className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-white/5 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          {meta.icon}
          <span className="text-sm font-medium text-white">{meta.title}</span>
          <span className={`text-xs px-2 py-0.5 rounded-full ${meta.badgeCls}`}>
            {meta.badge}
          </span>
        </div>
        <div className="flex items-center gap-2 text-gray-500">
          <span className="text-xs">{new Date(request.created_at).toLocaleTimeString()}</span>
          {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        </div>
      </div>

      {expanded && (
        <div className="px-4 pb-4 space-y-3">
          {/* Payload */}
          {action_type === "RUN_COMMAND" && (
            <div className="space-y-1.5">
              <p className="text-xs text-gray-500 uppercase tracking-wider">Команда</p>
              <code className="block text-sm text-amber-300 bg-black/50 border border-white/10 rounded-md px-3 py-2 font-mono">
                $ {payload.command}
              </code>
              {payload.reason && (
                <p className="text-xs text-gray-400">
                  <span className="text-gray-500">Обоснование: </span>{payload.reason}
                </p>
              )}
            </div>
          )}

          {action_type === "REVIEW_PLAN" && payload.plan_spec && (
            <div className="space-y-1.5">
              <p className="text-xs text-gray-500 uppercase tracking-wider">Спецификация плана</p>
              <MarkdownBlock content={payload.plan_spec} />
            </div>
          )}

          {action_type === "APPLY_DIFF" && (
            <div className="space-y-1.5">
              {payload.summary && (
                <p className="text-sm text-gray-300">
                  <span className="text-gray-500">Описание: </span>{payload.summary}
                </p>
              )}
              {payload.files_diff && (
                <>
                  <p className="text-xs text-gray-500 uppercase tracking-wider">Изменения</p>
                  <DiffViewer diff={payload.files_diff} />
                </>
              )}
            </div>
          )}

          {/* Supervisor notes */}
          {supervisor_notes && (
            <div className="rounded-lg border border-sky-500/20 bg-sky-950/20 px-3 py-2">
              <p className="text-xs text-sky-400 font-medium mb-1">Заключение Надсмотрщика (14B)</p>
              <p className="text-xs text-gray-300">{supervisor_notes}</p>
            </div>
          )}

          {/* Reject input */}
          {showRejectInput && (
            <Textarea
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="Причина отклонения (необязательно)…"
              className="text-sm bg-black/30 border-white/10 text-gray-200 resize-none h-20"
            />
          )}

          {/* Actions */}
          <div className="flex items-center gap-2 pt-1">
            <Button
              size="sm"
              onClick={handleApprove}
              disabled={isSubmitting}
              className="bg-emerald-600 hover:bg-emerald-500 text-white gap-1.5"
            >
              <Check className="w-3.5 h-3.5" />
              Утвердить
            </Button>

            {!showRejectInput ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowRejectInput(true)}
                disabled={isSubmitting}
                className="border-red-500/40 text-red-400 hover:bg-red-950/30 gap-1.5"
              >
                <X className="w-3.5 h-3.5" />
                Отклонить
              </Button>
            ) : (
              <>
                <Button
                  size="sm"
                  onClick={handleReject}
                  disabled={isSubmitting}
                  className="bg-red-700 hover:bg-red-600 text-white gap-1.5"
                >
                  <X className="w-3.5 h-3.5" />
                  Подтвердить отклонение
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setShowRejectInput(false)}
                  disabled={isSubmitting}
                  className="text-gray-500 hover:text-gray-300"
                >
                  Отмена
                </Button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
