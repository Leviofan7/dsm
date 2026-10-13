"use client"

import { useCallback, useMemo, useState } from "react"
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Play,
  Save,
  ShieldAlert,
  Sparkles,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

/**
 * Сценарий: предложение из прогона → заполнение аргументов → подтверждение активации.
 *
 * Замыкает цепочку, которой раньше не было в браузере: бэкенд-контракт (422 с отчётом,
 * обязательное `confirm=true`) существовал, но заполнить аргументы можно было только
 * правкой БД. Здесь человек видит обязательные поля инструмента, правит JSON и АКТИВИРУЕТ
 * сценарий явно — молчаливого включения нет ни в API, ни в UI.
 */

export type ScenarioStep = {
  tool: string | null
  description?: string | null
  args_template: Record<string, unknown>
  /** Обязательные поля инструмента (из живого реестра MCP). */
  required?: string[]
  properties?: string[]
}

export type ScenarioCardData = {
  id: string
  name: string | null
  description: string | null
  proposal_reason: string | null
  source_task_id: string | null
  status: string
  steps: number
  tools: (string | null)[]
  steps_detail?: ScenarioStep[]
  needs_args: boolean
  warning: string | null
  schema_checked?: boolean
  created?: boolean
}

type Report = {
  errors?: string[]
  empty_args?: string[]
  blank_values?: Record<string, string[]>
  missing_required?: Record<string, string[]>
  unknown_args?: Record<string, string[]>
  privileged_tools?: string[]
}

const STATUS_LABEL: Record<string, string> = {
  draft: "Черновик",
  active: "Активен",
  rejected: "Отклонён",
}

function prettyArgs(args: Record<string, unknown> | undefined): string {
  const obj = args && Object.keys(args).length > 0 ? args : {}
  return JSON.stringify(obj, null, 2)
}

/** Разбор тела ошибки бэкенда: `detail` может быть и строкой, и объектом с `report`. */
function readError(data: unknown, status: number): { message: string; report: Report | null } {
  const detail = (data as any)?.detail
  if (detail && typeof detail === "object") {
    return {
      message: typeof detail.message === "string" ? detail.message : `HTTP ${status}`,
      report: (detail.report as Report) || null,
    }
  }
  if (typeof detail === "string") return { message: detail, report: null }
  const err = (data as any)?.error
  return { message: typeof err === "string" ? err : `HTTP ${status}`, report: null }
}

function ReportView({ report }: { report: Report }) {
  return (
    <ul className="space-y-0.5 text-destructive">
      {(report.empty_args || []).map((t) => (
        <li key={`e-${t}`}>«{t}»: аргументы не заполнены</li>
      ))}
      {Object.entries(report.missing_required || {}).map(([tool, fields]) => (
        <li key={`m-${tool}`}>
          «{tool}»: отсутствуют обязательные поля — {fields.join(", ")}
        </li>
      ))}
      {Object.entries(report.blank_values || {}).map(([tool, fields]) => (
        <li key={`b-${tool}`}>
          «{tool}»: пустые значения — {fields.join(", ")}
        </li>
      ))}
      {Object.entries(report.unknown_args || {}).map(([tool, fields]) => (
        <li key={`u-${tool}`}>
          «{tool}»: неизвестные аргументы — {fields.join(", ")}
        </li>
      ))}
      {(report.privileged_tools || []).map((t) => (
        <li key={`p-${t}`}>«{t}» — привилегированный инструмент, активация запрещена</li>
      ))}
      {(report.errors || []).map((e, i) => (
        <li key={`x-${i}`}>{e}</li>
      ))}
    </ul>
  )
}

function ScenarioEditor({
  card,
  onCard,
}: {
  card: ScenarioCardData
  onCard: (c: ScenarioCardData) => void
}) {
  const steps = useMemo(() => card.steps_detail ?? [], [card.steps_detail])
  const [drafts, setDrafts] = useState<string[]>(() => steps.map((s) => prettyArgs(s.args_template)))
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState<null | "save" | "activate">(null)
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const active = card.status === "active"

  const buildSteps = useCallback(() => {
    const out: Record<string, unknown>[] = []
    for (let i = 0; i < steps.length; i++) {
      const tool = steps[i]?.tool
      const raw = (drafts[i] ?? "").trim() || "{}"
      let parsed: unknown
      try {
        parsed = JSON.parse(raw)
      } catch {
        throw new Error(`Шаг ${i + 1} (${tool}): аргументы — не валидный JSON`)
      }
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error(`Шаг ${i + 1} (${tool}): аргументы должны быть JSON-объектом`)
      }
      out.push({
        tool,
        description: steps[i]?.description,
        args_template: parsed as Record<string, unknown>,
      })
    }
    return out
  }, [drafts, steps])

  const call = async (which: "save" | "activate") => {
    setBusy(which)
    setReport(null)
    setError(null)
    setNotice(null)
    let body: string
    try {
      const built = buildSteps()
      body = JSON.stringify(
        which === "save" ? { steps: built } : { confirm: true, steps: built },
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось собрать шаги")
      setBusy(null)
      return
    }

    try {
      const url =
        which === "save"
          ? `/api/scenarios/${card.id}/steps`
          : `/api/scenarios/${card.id}/activate`
      const res = await fetch(url, {
        method: which === "save" ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body,
      })
      const data = await res.json().catch(() => null)
      if (!res.ok) {
        const { message, report: rep } = readError(data, res.status)
        setError(message)
        setReport(rep)
        return
      }
      if (data?.scenario) onCard(data.scenario as ScenarioCardData)
      setNotice(
        which === "save"
          ? "Аргументы проверены и сохранены (черновик). Активация — отдельным шагом."
          : "Сценарий активирован: шаги закреплены хэшем, запускать по имени из списка агентов.",
      )
      setReport((data?.report as Report) || null)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось выполнить запрос")
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-2">
      {/* Причина предложения — почему система вообще предложила именно это */}
      {card.proposal_reason && (
        <p className="rounded-md border border-border bg-background/60 p-2 text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground">Почему предложен: </span>
          {card.proposal_reason}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span
          className={cn(
            "rounded-full border px-2 py-0.5",
            active
              ? "border-emerald-500/40 text-emerald-600"
              : "border-border text-muted-foreground",
          )}
        >
          {STATUS_LABEL[card.status] ?? card.status}
        </span>
        <span className="text-muted-foreground">шагов: {card.steps}</span>
        {card.schema_checked === false && (
          <span className="inline-flex items-center gap-1 text-amber-600" title="Реестр MCP не отдал схемы">
            <AlertTriangle className="size-3" /> схемы инструментов недоступны
          </span>
        )}
      </div>

      {steps.length === 0 && (
        <p className="text-[11px] text-muted-foreground">
          Детали шагов недоступны — обновите карточку (GET /scenarios/{"{id}"}).
        </p>
      )}

      <ol className="space-y-2">
        {steps.map((s, i) => (
          <li key={`${s.tool}-${i}`} className="rounded-md border border-border bg-background/60 p-2">
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <span className="font-medium">
                {i + 1}. {s.tool}
              </span>
              {s.required?.length ? (
                <span className="text-amber-600">обязательные: {s.required.join(", ")}</span>
              ) : card.schema_checked ? (
                <span className="text-muted-foreground">обязательных полей нет</span>
              ) : null}
            </div>
            {s.description && (
              <p className="mt-0.5 text-[11px] text-muted-foreground">{s.description}</p>
            )}
            <Textarea
              value={drafts[i] ?? ""}
              onChange={(e) =>
                setDrafts((prev) => prev.map((v, idx) => (idx === i ? e.target.value : v)))
              }
              spellCheck={false}
              className="mt-1 h-20 font-mono text-[11px] leading-snug"
              placeholder='{"query": "{{query}}"}'
            />
          </li>
        ))}
      </ol>

      {steps.length > 0 && (
        <p className="text-[11px] text-muted-foreground">
          Доступна подстановка <code className="font-mono">{"{{query}}"}</code> — запрос прогона.
        </p>
      )}

      {error && (
        <div className="space-y-1 rounded-md border border-destructive/40 bg-destructive/5 p-2 text-[11px]">
          <p className="font-medium text-destructive">{error}</p>
          {report && <ReportView report={report} />}
        </div>
      )}
      {notice && (
        <p className="inline-flex items-start gap-1 text-[11px] text-emerald-600">
          <CheckCircle2 className="mt-0.5 size-3 shrink-0" />
          <span>{notice}</span>
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-[11px]"
          disabled={busy !== null || steps.length === 0}
          onClick={() => void call("save")}
        >
          {busy === "save" ? (
            <Loader2 className="mr-1 size-3 animate-spin" />
          ) : (
            <Save className="mr-1 size-3" />
          )}
          Проверить и сохранить
        </Button>

        <label className="inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground">
          <input
            type="checkbox"
            className="size-3.5 accent-primary"
            checked={confirm}
            onChange={(e) => setConfirm(e.target.checked)}
          />
          подтверждаю активацию
        </label>

        <Button
          size="sm"
          className="h-7 text-[11px]"
          disabled={busy !== null || !confirm || steps.length === 0}
          onClick={() => void call("activate")}
        >
          {busy === "activate" ? (
            <Loader2 className="mr-1 size-3 animate-spin" />
          ) : (
            <Play className="mr-1 size-3" />
          )}
          Активировать
        </Button>
      </div>
    </div>
  )
}

/**
 * Секция «Сценарий» в карточке прогона: предложить черновик или открыть редактор
 * уже предложенного сценария (шаги подтягиваются из `GET /scenarios/{id}`).
 */
export function ScenarioSection({
  taskId,
  proposed,
}: {
  taskId: string
  proposed?: string | null
}) {
  const [card, setCard] = useState<ScenarioCardData | null>(null)
  const [busy, setBusy] = useState<null | "propose" | "open">(null)
  const [error, setError] = useState<string | null>(null)

  const propose = async () => {
    setBusy("propose")
    setError(null)
    try {
      const res = await fetch(`/api/tasks/${taskId}/scenario`, { method: "POST" })
      const data = await res.json().catch(() => null)
      if (!res.ok) {
        setError(readError(data, res.status).message)
        return
      }
      setCard(data as ScenarioCardData)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось предложить сценарий")
    } finally {
      setBusy(null)
    }
  }

  const open = async () => {
    if (!proposed) return
    setBusy("open")
    setError(null)
    try {
      const res = await fetch(`/api/scenarios/${proposed}`, { cache: "no-store" })
      const data = await res.json().catch(() => null)
      if (!res.ok) {
        setError(readError(data, res.status).message)
        return
      }
      setCard(data as ScenarioCardData)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось открыть сценарий")
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-2">
      {!card && (
        <div className="flex flex-wrap items-center gap-2">
          {!proposed && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-[11px]"
              disabled={busy !== null}
              onClick={() => void propose()}
            >
              {busy === "propose" ? (
                <Loader2 className="mr-1 size-3 animate-spin" />
              ) : (
                <Sparkles className="mr-1 size-3" />
              )}
              Предложить сценарий
            </Button>
          )}
          {proposed && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-[11px]"
              disabled={busy !== null}
              onClick={() => void open()}
            >
              {busy === "open" ? (
                <Loader2 className="mr-1 size-3 animate-spin" />
              ) : (
                <Play className="mr-1 size-3" />
              )}
              Открыть редактор сценария
            </Button>
          )}
        </div>
      )}

      {card?.warning && (
        <p className="inline-flex items-start gap-1 text-[11px] text-amber-600">
          <ShieldAlert className="mt-0.5 size-3 shrink-0" />
          <span>{card.warning}</span>
        </p>
      )}

      {error && <p className="text-[11px] text-destructive">{error}</p>}

      {card && <ScenarioEditor card={card} onCard={setCard} />}
    </div>
  )
}
