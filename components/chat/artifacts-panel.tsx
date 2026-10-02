"use client"

import { useCallback, useEffect, useState } from "react"
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  FileText,
  Loader2,
  RefreshCw,
  Trash2,
} from "lucide-react"
import { cn } from "@/lib/utils"

/**
 * Вкладка «Артефакты»: история прогонов беседы и всё, что агент произвёл.
 *
 * Замысел (обсуждение 02.10): чат не должен зарастать портянками. Результаты живут
 * здесь, привязанные к задаче, вместе с планом и журналом — чтобы можно было вернуться
 * к ним позже и проанализировать, что система делала и где спотыкалась.
 */

type RunSummary = {
  task_id: string
  query: string
  status: string
  created_at: string | null
  artifacts_count: number
  plan_steps: number
  journal: { status: string; summary: string | null } | null
}

type PlanStep = {
  order: number
  topic: string
  role: string | null
  status: string
  result_preview?: string | null
}

type ArtifactCard = {
  id: string
  kind: string
  title: string
  mime: string | null
  size_bytes: number | null
  storage: string
  origin: string
  url: string | null
  created_at: string | null
  content_url: string
  content?: string
  truncated?: boolean
}

type RunCard = {
  task: { id: string; query: string; status: string; created_at: string | null }
  plan: { steps: PlanStep[]; markdown: string } | null
  artifacts: ArtifactCard[]
  journal: {
    status: string
    summary: string | null
    errors: string[] | null
    achievements: string[] | null
    metrics: Record<string, number | null> | null
    updated_at: string | null
  } | null
}

/** Браузер не ходит в FastAPI напрямую: cookie сессии живёт на origin Next. */
const contentUrl = (id: string) => `/api/artifacts/${id}/content`

const STATUS_ICON: Record<string, { icon: typeof CheckCircle2; className: string }> = {
  completed: { icon: CheckCircle2, className: "text-emerald-500" },
  success: { icon: CheckCircle2, className: "text-emerald-500" },
  failed: { icon: AlertTriangle, className: "text-red-500" },
  cancelled: { icon: AlertTriangle, className: "text-amber-500" },
  running: { icon: Loader2, className: "animate-spin text-blue-500" },
  pending: { icon: CircleDashed, className: "text-muted-foreground" },
}

function StatusMark({ status }: { status: string }) {
  const cfg = STATUS_ICON[status] ?? STATUS_ICON.pending
  const Icon = cfg.icon
  return <Icon className={cn("size-3.5 shrink-0", cfg.className)} />
}

function formatSize(bytes: number | null) {
  if (!bytes) return ""
  if (bytes < 1024) return `${bytes} Б`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`
}

function formatTime(iso: string | null) {
  if (!iso) return ""
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" })
}

function ArtifactView({ a, onDeleted }: { a: ArtifactCard; onDeleted: () => void }) {
  const [busy, setBusy] = useState(false)

  const remove = async () => {
    setBusy(true)
    try {
      await fetch(`/api/artifacts/${a.id}`, { method: "DELETE" })
      onDeleted()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-md border border-border bg-background/60 p-2.5">
      <div className="flex items-start gap-2">
        <FileText className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-medium">{a.title}</div>
          <div className="text-[11px] text-muted-foreground">
            {a.kind}
            {a.size_bytes ? ` · ${formatSize(a.size_bytes)}` : ""}
            {a.origin ? ` · ${a.origin}` : ""}
          </div>
        </div>
        <button
          type="button"
          onClick={remove}
          disabled={busy}
          title="Убрать из панели (история сохранится)"
          className="text-muted-foreground transition-colors hover:text-destructive disabled:opacity-50"
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>

      {a.content !== undefined && (
        <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-2 text-[11px] leading-relaxed">
          {a.content}
          {a.truncated ? "\n… (показана часть, полный текст — по ссылке)" : ""}
        </pre>
      )}

      {a.mime?.startsWith("image/") && (
        // eslint-disable-next-line @next/next/no-img-element -- картинка приходит из прокси как есть
        <img
          src={contentUrl(a.id)}
          alt={a.title}
          className="mt-2 max-h-56 w-full rounded border border-border object-contain"
        />
      )}

      <a
        href={a.storage === "url" && a.url ? a.url : contentUrl(a.id)}
        target="_blank"
        rel="noreferrer"
        className="mt-2 inline-block text-[11px] text-primary hover:underline"
      >
        {a.storage === "url" ? "Открыть источник" : "Скачать / открыть"}
      </a>
    </div>
  )
}

function RunDetail({ taskId, onToast }: { taskId: string; onToast?: (msg: string) => void }) {
  const [card, setCard] = useState<RunCard | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const res = await fetch(`/api/tasks/${taskId}/artifacts`, { cache: "no-store" })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setCard(await res.json())
    } catch {
      onToast?.("Не удалось загрузить карточку прогона")
    } finally {
      setLoading(false)
    }
  }, [taskId, onToast])

  useEffect(() => {
    void load()
  }, [load])

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" /> Загружаю карточку…
      </div>
    )
  }
  if (!card) return <p className="px-2 py-3 text-xs text-muted-foreground">Карточка недоступна</p>

  return (
    <div className="space-y-3 border-t border-border/60 px-2 py-3">
      {card.plan && card.plan.steps.length > 0 && (
        <section>
          <h4 className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            План
          </h4>
          <ol className="space-y-1">
            {card.plan.steps.map((s) => (
              <li key={`${s.order}-${s.topic}`} className="flex items-start gap-2 text-[11px]">
                <StatusMark status={s.status} />
                <span className="min-w-0 flex-1">
                  <span className={cn(s.status === "completed" && "text-muted-foreground line-through")}>
                    {s.order}. {s.topic}
                  </span>
                  {s.role && <span className="ml-1 text-muted-foreground">({s.role})</span>}
                  {s.result_preview && (
                    <span className="mt-0.5 block truncate text-muted-foreground">{s.result_preview}</span>
                  )}
                </span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {card.journal && (
        <section>
          <h4 className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            Журнал
          </h4>
          <div className="rounded-md border border-border bg-background/60 p-2 text-[11px]">
            <div className="flex items-center gap-2">
              <StatusMark status={card.journal.status} />
              <span className="font-medium">{card.journal.status}</span>
              {card.journal.metrics?.duration_ms ? (
                <span className="text-muted-foreground">
                  {(card.journal.metrics.duration_ms / 1000).toFixed(1)} с
                </span>
              ) : null}
            </div>
            {card.journal.summary && <p className="mt-1 text-muted-foreground">{card.journal.summary}</p>}
            {card.journal.errors?.length ? (
              <ul className="mt-1 space-y-0.5 text-red-500">
                {card.journal.errors.map((e, i) => (
                  <li key={i} className="break-words">
                    {e}
                  </li>
                ))}
              </ul>
            ) : null}
            {card.journal.achievements?.length ? (
              <ul className="mt-1 space-y-0.5 text-emerald-600">
                {card.journal.achievements.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            ) : null}
          </div>
        </section>
      )}

      <section>
        <h4 className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          Артефакты{card.artifacts.length ? ` · ${card.artifacts.length}` : ""}
        </h4>
        {card.artifacts.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">Прогон ничего не оставил</p>
        ) : (
          <div className="space-y-2">
            {card.artifacts.map((a) => (
              <ArtifactView key={a.id} a={a} onDeleted={load} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

export function ArtifactsPanel({
  conversationId,
  live,
  onToast,
}: {
  conversationId: string | null
  /** Прогон идёт — обновляем историю сама, без кнопки */
  live?: boolean
  onToast?: (msg: string) => void
}) {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [openTask, setOpenTask] = useState<string | null>(null)

  const loadRuns = useCallback(async () => {
    if (!conversationId) {
      setRuns([])
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/conversations/${conversationId}/runs`, { cache: "no-store" })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setRuns(Array.isArray(data) ? data : [])
    } catch {
      setError("Не удалось загрузить историю прогонов")
    } finally {
      setLoading(false)
    }
  }, [conversationId])

  useEffect(() => {
    void loadRuns()
  }, [loadRuns])

  useEffect(() => {
    if (!live || !conversationId) return
    const timer = setInterval(() => void loadRuns(), 5000)
    return () => clearInterval(timer)
  }, [live, conversationId, loadRuns])

  if (!conversationId) {
    return (
      <p className="px-4 py-6 text-xs text-muted-foreground">
        Выберите беседу — здесь появится история прогонов и их результаты.
      </p>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between px-4 py-2">
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Прогоны{loading ? " · обновляю…" : ""}
        </span>
        <button
          type="button"
          onClick={() => void loadRuns()}
          className="text-xs text-muted-foreground hover:text-foreground"
          title="Обновить"
        >
          <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {error && <p className="px-2 py-3 text-xs text-destructive">{error}</p>}
        {!error && runs.length === 0 && !loading && (
          <p className="px-2 py-3 text-xs text-muted-foreground">Прогонов пока нет</p>
        )}

        <div className="space-y-1.5">
          {runs.map((run) => {
            const open = openTask === run.task_id
            return (
              <div key={run.task_id} className="rounded-md border border-border bg-background/40">
                <button
                  type="button"
                  onClick={() => setOpenTask(open ? null : run.task_id)}
                  className="flex w-full items-start gap-2 px-2 py-2 text-left"
                >
                  {open ? (
                    <ChevronDown className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium">
                      {run.query || "Без описания"}
                    </span>
                    <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
                      <span className="inline-flex items-center gap-1">
                        <StatusMark status={run.journal?.status || run.status} />
                        {run.journal?.status || run.status}
                      </span>
                      <span suppressHydrationWarning>{formatTime(run.created_at)}</span>
                      {run.plan_steps > 0 && <span>шагов: {run.plan_steps}</span>}
                      {run.artifacts_count > 0 && <span>артефактов: {run.artifacts_count}</span>}
                    </span>
                    {run.journal?.summary && (
                      <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                        {run.journal.summary}
                      </span>
                    )}
                  </span>
                </button>
                {open && <RunDetail taskId={run.task_id} onToast={onToast} />}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
