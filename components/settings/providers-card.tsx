"use client"

import { useCallback, useEffect, useState } from "react"
import { AlertTriangle, CheckCircle2, CloudOff, Loader2, RefreshCw } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

/**
 * Здоровье провайдеров моделей.
 *
 * Зачем в UI: флаг «модель доступна» означает лишь «задан API-ключ». Отозванный ключ,
 * исчерпанный баланс и упавший провайдер по нему не видны — из-за этого инцидент 26.09
 * выглядел как «бот молча не ответил». Здесь видно класс последнего сбоя, сколько ждать
 * до повторной попытки, и — по кнопке — живая проверка (Ollama, баланс DeepSeek).
 *
 * Ограничение, которое честно показываем: для Anthropic/OpenAI/Gemini баланса в публичном
 * API нет, поэтому по ним проверяется ТОЛЬКО наличие ключа.
 */

type Failure = {
  kind: string
  detail: string
  reason: string
  retry_in_s: number
}

type ModelHealth = {
  name: string
  provider: string
  provider_type: string | null
  available: boolean
  installed: boolean
  key_present: boolean
  degraded: boolean
  failure: Failure | null
}

type ProbeEntry = { checked: string; ok: boolean; detail: string }

type Health = {
  providers: { provider: string; models: string[]; available: boolean; degraded: string[] }[]
  models: ModelHealth[]
  degraded: Record<string, Failure>
  probe: Record<string, ProbeEntry> | null
}

const KIND_LABEL: Record<string, string> = {
  auth: "ключ",
  quota: "баланс/лимит",
  unavailable: "недоступен",
  unknown: "ошибка",
}

function failureLine(name: string, f: Failure) {
  const wait = f.retry_in_s > 0 ? ` · повтор через ${Math.ceil(f.retry_in_s / 60)} мин` : ""
  const kind = KIND_LABEL[f.kind] ?? f.kind
  return `${name} (${kind}): ${f.reason}${wait}`
}

export function ProvidersCard() {
  const [data, setData] = useState<Health | null>(null)
  const [loading, setLoading] = useState(false)
  const [probing, setProbing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (probe: boolean) => {
    probe ? setProbing(true) : setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/providers/health${probe ? "?probe=true" : ""}`, {
        cache: "no-store",
      })
      const body = await res.json().catch(() => null)
      if (!res.ok) {
        setError(body?.error || `HTTP ${res.status}`)
        return
      }
      setData(body as Health)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось получить статус")
    } finally {
      setProbing(false)
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(false)
  }, [load])

  const degraded = data ? Object.entries(data.degraded) : []

  return (
    <Card className="border-border">
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle className="text-base">Провайдеры моделей</CardTitle>
            <CardDescription>
              Кто доступен, кто в паузе и почему. «Доступен» означает наличие ключа —
              для баланса нажмите «Проверить сейчас».
            </CardDescription>
          </div>
          <Button
            size="sm"
            variant="outline"
            className="shrink-0"
            disabled={loading || probing}
            onClick={() => void load(true)}
          >
            {probing ? (
              <Loader2 className="mr-1 size-3.5 animate-spin" />
            ) : (
              <RefreshCw className="mr-1 size-3.5" />
            )}
            Проверить сейчас
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {error && <p className="text-destructive">{error}</p>}
        {loading && !data && (
          <p className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" /> Загружаю статус…
          </p>
        )}

        {data && (
          <>
            <ul className="space-y-1.5">
              {data.providers.map((p) => {
                const down = p.degraded.length > 0
                return (
                  <li key={p.provider} className="flex items-start gap-2">
                    {down ? (
                      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-500" />
                    ) : p.available ? (
                      <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                    ) : (
                      <CloudOff className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="font-medium">{p.provider}</span>
                      <span className="text-muted-foreground">
                        {" "}
                        · моделей: {p.models.length} · {p.available ? "ключ/модель есть" : "нет ключа"}
                      </span>
                      {down && (
                        <span className="mt-0.5 block text-amber-600">
                          {p.degraded.map((m) => failureLine(m, data.degraded[m])).join("; ")}
                        </span>
                      )}
                    </span>
                  </li>
                )
              })}
            </ul>

            {degraded.length === 0 && (
              <p className="text-muted-foreground">Активных пауз нет.</p>
            )}

            {data.probe && (
              <div className="rounded-md border border-border bg-muted/30 p-2">
                <p className="mb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  Живая проверка
                </p>
                <ul className="space-y-0.5">
                  {Object.entries(data.probe).map(([name, entry]) => (
                    <li key={name} className="flex items-start gap-1.5">
                      {entry.ok ? (
                        <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-600" />
                      ) : (
                        <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-500" />
                      )}
                      <span className={cn("break-words", !entry.ok && "text-amber-600")}>
                        <span className="font-medium">{name}</span> — {entry.detail}
                        {entry.checked === "key" && (
                          <span className="text-muted-foreground"> (только ключ)</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {data.probe === null && (
              <p className="text-xs text-muted-foreground">
                Паузы живут в памяти процесса API — у воркера своя копия.
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}
