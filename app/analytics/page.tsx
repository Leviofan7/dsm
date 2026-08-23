"use client"

import { useEffect, useState } from "react"
import { AppShell, MobileNav } from "@/components/app-shell"
import { RefreshCw, AlertTriangle, CheckCircle2, TrendingUp, Clock, Users, Search } from "lucide-react"

type SilentMissRow = { task_type_classified: string; total: number; silent_miss: number }
type ModelOutcomeRow = {
  model_selected: string; task_type_final: string; total: number
  success_count: number; correction_count: number; stub_count: number
}
type HumanCorrectionRow = { task_type_final: string; total_tasks: number; corrected_tasks: number }
type LatencyRow = { task_type_final: string; avg_total_ms: number; avg_executor_ms: number }
type UnverifiedRow = { task_type_final: string; total: number; unverified_success: number; details: string | null }

type Metrics = {
  silent_miss: SilentMissRow[]
  model_outcome: ModelOutcomeRow[]
  human_correction: HumanCorrectionRow[]
  latency: LatencyRow[]
  unverified_success: UnverifiedRow[]
}

function pct(n: number, d: number) {
  return d > 0 ? ((n / d) * 100).toFixed(1) : "0.0"
}

function Badge({ value, danger }: { value: string; danger?: boolean }) {
  const isHigh = parseFloat(value) > 0
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${
        danger && isHigh
          ? "bg-red-500/20 text-red-400"
          : isHigh
          ? "bg-emerald-500/20 text-emerald-400"
          : "bg-emerald-500/20 text-emerald-400"
      }`}
    >
      {value}%
    </span>
  )
}

function Card({ title, icon: Icon, children, description }: {
  title: string; icon: React.ElementType; children: React.ReactNode; description?: string
}) {
  return (
    <div className="rounded-xl border border-border bg-card shadow-sm overflow-hidden">
      <div className="border-b border-border px-5 py-3">
        <div className="flex items-center gap-2">
          <Icon className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-semibold">{title}</h2>
        </div>
        {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
      </div>
      <div className="overflow-x-auto">{children}</div>
    </div>
  )
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground bg-muted/40">
      {children}
    </th>
  )
}
function Td({ children }: { children: React.ReactNode }) {
  return <td className="px-4 py-3 text-sm">{children}</td>
}

export default function AnalyticsPage() {
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fromDate, setFromDate] = useState("")
  const [toDate, setToDate] = useState("")
  const [selectedTask, setSelectedTask] = useState<string>("All")

  async function loadData(from?: string, to?: string) {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams()
    if (from) params.set("from_date", from + " 00:00:00")
    if (to) params.set("to_date", to + " 23:59:59")
    const url = `/api/analytics/metrics${params.toString() ? "?" + params.toString() : ""}`
    try {
      const res = await fetch(url, { cache: "no-store" })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (data.error) throw new Error(data.error)
      setMetrics(data)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadData() }, [])

  // Получаем уникальные типы задач для фильтра
  const uniqueTaskTypes = Array.from(new Set([
    ...(metrics?.silent_miss.map(r => r.task_type_classified) || []),
    ...(metrics?.model_outcome.map(r => r.task_type_final) || []),
    ...(metrics?.human_correction.map(r => r.task_type_final) || []),
    ...(metrics?.latency.map(r => r.task_type_final) || []),
    ...(metrics?.unverified_success.map(r => r.task_type_final) || []),
  ])).filter(Boolean)

  const filterRow = (taskType: string) => selectedTask === "All" || taskType === selectedTask

  const totalUnverified = metrics?.unverified_success.filter(r => filterRow(r.task_type_final)).reduce((s, r) => s + r.unverified_success, 0) ?? 0

  const generateCsv = () => {
    if (!metrics) return ""
    let csv = ""
    
    csv += "--- 1. Routing Accuracy (Silent Misses) ---\\n"
    csv += "Task Type,Total,Silent Misses,Miss Rate (%)\\n"
    metrics.silent_miss.filter(r => filterRow(r.task_type_classified)).forEach(r => {
      csv += `"${r.task_type_classified}",${r.total},${r.silent_miss},${pct(r.silent_miss, r.total)}\\n`
    })
    
    csv += "\\n--- 2. Per-Model Outcome Tracking ---\\n"
    csv += "Model,Task Type,Total,Success %,Correction %,Stub %\\n"
    metrics.model_outcome.filter(r => filterRow(r.task_type_final)).forEach(r => {
      csv += `"${r.model_selected}","${r.task_type_final}",${r.total},${pct(r.success_count, r.total)},${pct(r.correction_count, r.total)},${pct(r.stub_count, r.total)}\\n`
    })
    
    csv += "\\n--- 3. Human Correction Rate ---\\n"
    csv += "Task Type,Total Tasks,Corrected,Rate (%)\\n"
    metrics.human_correction.filter(r => filterRow(r.task_type_final)).forEach(r => {
      csv += `"${r.task_type_final}",${r.total_tasks},${r.corrected_tasks},${pct(r.corrected_tasks, r.total_tasks)}\\n`
    })
    
    csv += "\\n--- 4. Latency per Stage ---\\n"
    csv += "Task Type,Avg Total (ms),Avg Executor (ms)\\n"
    metrics.latency.filter(r => filterRow(r.task_type_final)).forEach(r => {
      csv += `"${r.task_type_final}",${Math.round(r.avg_total_ms)},${Math.round(r.avg_executor_ms)}\\n`
    })
    
    csv += "\\n--- 5. Unverified Successes (Fake Executions) ---\\n"
    csv += "Task Type,Verified Attempts,Failed Verification,Failure Rate (%),Details\\n"
    metrics.unverified_success.filter(r => filterRow(r.task_type_final)).forEach(r => {
      csv += `"${r.task_type_final}",${r.total},${r.unverified_success},${pct(r.unverified_success, r.total)},"${(r.details || '').replace(/"/g, '""')}"\\n`
    })
    
    return csv
  }

  const handleDownload = () => {
    const csv = generateCsv()
    if (!csv) return
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.href = url
    link.setAttribute("download", `analytics_${selectedTask}_${new Date().toISOString().slice(0,10)}.csv`)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  const handleCopy = () => {
    const csv = generateCsv()
    if (csv) navigator.clipboard.writeText(csv)
  }

  return (
    <AppShell>
      <MobileNav />
      <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-border px-5 md:px-8">
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold">Agent Analytics</h1>
          <p className="hidden text-sm text-muted-foreground sm:block">
            Routing accuracy, model outcomes, and side-effect verification
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleCopy}
            disabled={loading || !metrics}
            className="rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium transition-colors hover:bg-accent disabled:opacity-50"
          >
            Copy CSV
          </button>
          <button
            onClick={handleDownload}
            disabled={loading || !metrics}
            className="rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium transition-colors hover:bg-accent disabled:opacity-50"
          >
            Download CSV
          </button>
          <button
            onClick={() => loadData(fromDate, toDate)}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium transition-colors hover:bg-accent disabled:opacity-50"
          >
            <RefreshCw className={`size-3 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-5 py-6 md:px-8">
        <div className="mx-auto max-w-6xl space-y-5">

          {/* Controls row */}
          <div className="flex flex-wrap items-end gap-4 rounded-xl border border-border bg-card px-4 py-3">
            <div className="flex flex-col gap-1 w-full sm:w-auto">
              <label className="text-xs text-muted-foreground">Filter by Task</label>
              <select
                value={selectedTask}
                onChange={(e) => setSelectedTask(e.target.value)}
                className="rounded-md border border-border bg-background px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              >
                <option value="All">All Tasks</option>
                {uniqueTaskTypes.map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
            
            <div className="h-8 w-px bg-border hidden sm:block mx-1"></div>

            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted-foreground">From</label>
              <input
                id="analytics-from"
                type="date"
                value={fromDate}
                onChange={(e) => setFromDate(e.target.value)}
                className="rounded-md border border-border bg-background px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted-foreground">To</label>
              <input
                id="analytics-to"
                type="date"
                value={toDate}
                onChange={(e) => setToDate(e.target.value)}
                className="rounded-md border border-border bg-background px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              />
            </div>
            <button
              id="analytics-apply"
              onClick={() => loadData(fromDate, toDate)}
              disabled={loading}
              className="flex items-center gap-2 rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              <Search className="size-3.5" />
              Apply Filters
            </button>
            {(fromDate || toDate) && (
              <button
                onClick={() => { setFromDate(""); setToDate(""); loadData() }}
                className="text-xs text-muted-foreground underline hover:text-foreground mb-1.5"
              >
                Clear Dates
              </button>
            )}
          </div>

          {/* Error */}
          {error && (
            <div className="flex items-center gap-2 rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-400">
              <AlertTriangle className="size-4 shrink-0" />
              Failed to load analytics: {error}
            </div>
          )}

          {/* Loading skeleton */}
          {loading && (
            <div className="flex items-center justify-center py-20 text-sm text-muted-foreground">
              <RefreshCw className="mr-2 size-4 animate-spin" />
              Loading analytics…
            </div>
          )}

          {!loading && metrics && (
            <>
              {/* 1. Silent Miss */}
              <Card
                title="1. Routing Accuracy — Silent Misses"
                icon={AlertTriangle}
                description="Tasks where tools were available but the agent never called them"
              >
                <table className="w-full">
                  <thead>
                    <tr><Th>Task Type (Classified)</Th><Th>Total</Th><Th>Silent Misses</Th><Th>Miss Rate</Th></tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {metrics.silent_miss.filter(r => filterRow(r.task_type_classified)).length ? metrics.silent_miss.filter(r => filterRow(r.task_type_classified)).map((r, i) => (
                      <tr key={i} className="hover:bg-muted/20 transition-colors">
                        <Td><code className="rounded bg-muted px-1.5 py-0.5 text-xs">{r.task_type_classified}</code></Td>
                        <Td>{r.total}</Td>
                        <Td>{r.silent_miss}</Td>
                        <Td><Badge value={pct(r.silent_miss, r.total)} danger /></Td>
                      </tr>
                    )) : (
                      <tr><td colSpan={4} className="px-4 py-6 text-center text-sm text-muted-foreground">No data for selected task</td></tr>
                    )}
                  </tbody>
                </table>
              </Card>

              {/* 2. Model Outcome */}
              <Card title="2. Per-Model Outcome Tracking" icon={TrendingUp}>
                <table className="w-full">
                  <thead>
                    <tr><Th>Model</Th><Th>Task Type</Th><Th>Total</Th><Th>Success %</Th><Th>Correction %</Th><Th>Stub %</Th></tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {metrics.model_outcome.filter(r => filterRow(r.task_type_final)).length ? metrics.model_outcome.filter(r => filterRow(r.task_type_final)).map((r, i) => (
                      <tr key={i} className="hover:bg-muted/20 transition-colors">
                        <Td><span className="font-mono text-xs">{r.model_selected}</span></Td>
                        <Td><code className="rounded bg-muted px-1.5 py-0.5 text-xs">{r.task_type_final}</code></Td>
                        <Td>{r.total}</Td>
                        <Td><Badge value={pct(r.success_count, r.total)} /></Td>
                        <Td><Badge value={pct(r.correction_count, r.total)} danger /></Td>
                        <Td><Badge value={pct(r.stub_count, r.total)} danger /></Td>
                      </tr>
                    )) : (
                      <tr><td colSpan={6} className="px-4 py-6 text-center text-sm text-muted-foreground">No data for selected task</td></tr>
                    )}
                  </tbody>
                </table>
              </Card>

              {/* 3. Human Correction */}
              <Card title="3. Human Correction Rate" icon={Users}>
                <table className="w-full">
                  <thead>
                    <tr><Th>Task Type</Th><Th>Total Tasks</Th><Th>Corrected</Th><Th>Rate</Th></tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {metrics.human_correction.filter(r => filterRow(r.task_type_final)).length ? metrics.human_correction.filter(r => filterRow(r.task_type_final)).map((r, i) => (
                      <tr key={i} className="hover:bg-muted/20 transition-colors">
                        <Td><code className="rounded bg-muted px-1.5 py-0.5 text-xs">{r.task_type_final}</code></Td>
                        <Td>{r.total_tasks}</Td>
                        <Td>{r.corrected_tasks}</Td>
                        <Td><Badge value={pct(r.corrected_tasks, r.total_tasks)} danger /></Td>
                      </tr>
                    )) : (
                      <tr><td colSpan={4} className="px-4 py-6 text-center text-sm text-muted-foreground">No data for selected task</td></tr>
                    )}
                  </tbody>
                </table>
              </Card>

              {/* 4. Latency */}
              <Card title="4. Latency per Stage" icon={Clock}>
                <table className="w-full">
                  <thead>
                    <tr><Th>Task Type</Th><Th>Avg Total (ms)</Th><Th>Avg Executor (ms)</Th></tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {metrics.latency.filter(r => filterRow(r.task_type_final)).length ? metrics.latency.filter(r => filterRow(r.task_type_final)).map((r, i) => (
                      <tr key={i} className="hover:bg-muted/20 transition-colors">
                        <Td><code className="rounded bg-muted px-1.5 py-0.5 text-xs">{r.task_type_final}</code></Td>
                        <Td>{Math.round(r.avg_total_ms).toLocaleString()} ms</Td>
                        <Td>{Math.round(r.avg_executor_ms).toLocaleString()} ms</Td>
                      </tr>
                    )) : (
                      <tr><td colSpan={3} className="px-4 py-6 text-center text-sm text-muted-foreground">No data for selected task</td></tr>
                    )}
                  </tbody>
                </table>
              </Card>

              {/* 5. Unverified Successes */}
              <Card
                title="5. Unverified Successes (Fake Executions)"
                icon={AlertTriangle}
                description="Agent returned 'success' but post-execution side-effect verification failed"
              >
                <table className="w-full">
                  <thead>
                    <tr>
                      <Th>Task Type</Th>
                      <Th>Verified Attempts</Th>
                      <Th>Failed Verification</Th>
                      <Th>Failure Rate</Th>
                      <Th>Details</Th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {metrics.unverified_success.filter(r => filterRow(r.task_type_final)).length ? metrics.unverified_success.filter(r => filterRow(r.task_type_final)).map((r, i) => (
                      <tr key={i} className="hover:bg-muted/20 transition-colors">
                        <Td><code className="rounded bg-muted px-1.5 py-0.5 text-xs">{r.task_type_final}</code></Td>
                        <Td>{r.total}</Td>
                        <Td>
                          <span className={r.unverified_success > 0 ? "font-semibold text-red-400" : "text-emerald-400"}>
                            {r.unverified_success}
                          </span>
                        </Td>
                        <Td><Badge value={pct(r.unverified_success, r.total)} danger /></Td>
                        <Td><span className="max-w-xs truncate text-xs text-muted-foreground">{r.details ?? "—"}</span></Td>
                      </tr>
                    )) : (
                      <tr>
                        <td colSpan={5} className="px-4 py-8 text-center text-sm text-emerald-400">
                          ✓ No unverified successes — all tool side-effects confirmed
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </Card>
            </>
          )}
        </div>
      </div>
    </AppShell>
  )
}
