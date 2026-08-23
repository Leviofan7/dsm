"use client"

import { useEffect, useState } from "react"
import { AppShell, MobileNav } from "@/components/app-shell"
import { RefreshCw, CheckCircle2, AlertTriangle, Play, X, FileCode2, MessagesSquare, Check } from "lucide-react"

type PrivilegedAction = {
  id: string
  action_type: string
  target: string
  instruction: string
  reasoning: string
  status: string
  diff_content: string | null
  created_at: string
}

function StatusBadge({ status }: { status: string }) {
  switch (status) {
    case "awaiting_approval":
      return <span className="inline-flex rounded-full px-2 py-0.5 text-xs font-semibold bg-amber-500/20 text-amber-500">Awaiting Approval</span>
    case "coder_running":
      return <span className="inline-flex rounded-full px-2 py-0.5 text-xs font-semibold bg-blue-500/20 text-blue-400 animate-pulse">Coder Running...</span>
    case "diff_ready":
      return <span className="inline-flex rounded-full px-2 py-0.5 text-xs font-semibold bg-purple-500/20 text-purple-400">Diff Ready</span>
    case "applied":
      return <span className="inline-flex rounded-full px-2 py-0.5 text-xs font-semibold bg-emerald-500/20 text-emerald-400">Applied</span>
    case "rejected":
      return <span className="inline-flex rounded-full px-2 py-0.5 text-xs font-semibold bg-red-500/20 text-red-400">Rejected</span>
    case "apply_failed":
      return <span className="inline-flex rounded-full px-2 py-0.5 text-xs font-semibold bg-red-500/20 text-red-400">Apply Failed</span>
    default:
      return <span className="inline-flex rounded-full px-2 py-0.5 text-xs font-semibold bg-muted text-muted-foreground">{status}</span>
  }
}

export default function ImprovementsPage() {
  const [actions, setActions] = useState<PrivilegedAction[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  
  const [selectedAction, setSelectedAction] = useState<PrivilegedAction | null>(null)
  const [processingId, setProcessingId] = useState<string | null>(null)

  async function fetchActions() {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch("/api/privileged-actions")
      if (!res.ok) throw new Error("Failed to load actions")
      setActions(await res.json())
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchActions()
    const interval = setInterval(fetchActions, 5000)
    return () => clearInterval(interval)
  }, [])

  async function handleApprove(id: string) {
    setProcessingId(id)
    try {
      const res = await fetch(`/api/privileged-actions/${id}/approve`, { method: "POST" })
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.error || "Approve failed")
      }
      await fetchActions()
    } catch (e: any) {
      alert("Error: " + e.message)
    } finally {
      setProcessingId(null)
    }
  }

  async function handleReject(id: string) {
    setProcessingId(id)
    try {
      const res = await fetch(`/api/privileged-actions/${id}/reject`, { method: "POST" })
      if (!res.ok) throw new Error("Reject failed")
      await fetchActions()
      if (selectedAction?.id === id) setSelectedAction(null)
    } catch (e: any) {
      alert("Error: " + e.message)
    } finally {
      setProcessingId(null)
    }
  }

  async function handleApplyDiff(id: string) {
    setProcessingId(id)
    try {
      const res = await fetch(`/api/privileged-actions/${id}/apply-diff`, { method: "POST" })
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.error || "Apply failed")
      }
      await fetchActions()
      setSelectedAction(null)
      alert("Success: Изменения применены к кодовой базе!")
    } catch (e: any) {
      alert("Error applying diff: " + e.message)
    } finally {
      setProcessingId(null)
    }
  }

  return (
    <AppShell>
      <MobileNav />
      <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-border px-5 md:px-8">
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold">Meta-Analyst Improvements</h1>
          <p className="hidden text-sm text-muted-foreground sm:block">
            Review and apply AI-suggested code and prompt updates
          </p>
        </div>
        <button
          onClick={fetchActions}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium transition-colors hover:bg-accent disabled:opacity-50"
        >
          <RefreshCw className={`size-3 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-5 py-6 md:px-8">
        <div className="mx-auto max-w-6xl space-y-5">
          
          {error && (
            <div className="flex items-center gap-2 rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-400">
              <AlertTriangle className="size-4 shrink-0" />
              Failed to load actions: {error}
            </div>
          )}

          <div className="rounded-xl border border-border bg-card shadow-sm overflow-hidden">
            <div className="border-b border-border px-5 py-3">
              <h2 className="text-sm font-semibold">Pending Actions</h2>
            </div>
            
            {actions.length === 0 && !loading && (
              <div className="px-5 py-12 text-center text-muted-foreground text-sm">
                Нет предложений от Ревизора.
              </div>
            )}

            <ul className="divide-y divide-border">
              {actions.map(action => (
                <li key={action.id} className={`p-4 transition-colors ${selectedAction?.id === action.id ? "bg-accent/50" : "hover:bg-muted/30"}`}>
                  <div className="flex flex-col md:flex-row gap-4 justify-between">
                    <div className="space-y-2 flex-1">
                      <div className="flex items-center gap-2">
                        {action.action_type === "coder_task" ? (
                          <FileCode2 className="size-4 text-blue-400" />
                        ) : (
                          <MessagesSquare className="size-4 text-purple-400" />
                        )}
                        <span className="font-semibold text-sm">
                          {action.action_type === "coder_task" ? "Code Update" : "Prompt Update"}
                        </span>
                        <span className="text-muted-foreground text-sm border-l border-border pl-2">
                          {action.target}
                        </span>
                        <StatusBadge status={action.status} />
                      </div>
                      
                      <div className="text-sm">
                        <span className="text-muted-foreground">Reasoning:</span> {action.reasoning}
                      </div>
                      <div className="text-sm bg-muted/30 p-2 rounded-md font-mono text-xs border border-border max-h-24 overflow-y-auto">
                        {action.instruction}
                      </div>
                    </div>
                    
                    <div className="flex flex-col gap-2 min-w-32 items-end justify-center">
                      {action.status === "awaiting_approval" && (
                        <>
                          <button 
                            disabled={processingId === action.id}
                            onClick={() => handleApprove(action.id)}
                            className="w-full flex items-center justify-center gap-2 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
                          >
                            <Play className="size-3.5" /> Approve
                          </button>
                          <button 
                            disabled={processingId === action.id}
                            onClick={() => handleReject(action.id)}
                            className="w-full flex items-center justify-center gap-2 rounded-md border border-border bg-background px-3 py-1.5 text-sm font-medium hover:bg-destructive hover:text-destructive-foreground disabled:opacity-50"
                          >
                            <X className="size-3.5" /> Reject
                          </button>
                        </>
                      )}
                      
                      {action.status === "diff_ready" && (
                        <button 
                          onClick={() => setSelectedAction(action)}
                          className="w-full flex items-center justify-center gap-2 rounded-md bg-purple-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-purple-700"
                        >
                          View Diff
                        </button>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          {selectedAction && selectedAction.status === "diff_ready" && (
            <div className="rounded-xl border border-purple-500/30 bg-card shadow-lg overflow-hidden mt-8 animate-in fade-in slide-in-from-bottom-4">
              <div className="border-b border-purple-500/30 bg-purple-500/10 px-5 py-3 flex justify-between items-center">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="size-4 text-purple-400" />
                  <h2 className="text-sm font-semibold text-purple-300">Review Changes: {selectedAction.target}</h2>
                </div>
                <button onClick={() => setSelectedAction(null)} className="text-muted-foreground hover:text-foreground">
                  <X className="size-4" />
                </button>
              </div>
              
              <div className="p-4 bg-[#1e1e1e] overflow-x-auto text-xs font-mono">
                <pre className="leading-relaxed">
                  {selectedAction.diff_content?.split('\\n').map((line, i) => {
                    let colorClass = "text-gray-300"
                    let bgClass = ""
                    if (line.startsWith('+')) { colorClass = "text-emerald-400"; bgClass = "bg-emerald-400/10" }
                    else if (line.startsWith('-')) { colorClass = "text-red-400"; bgClass = "bg-red-400/10" }
                    else if (line.startsWith('@@')) { colorClass = "text-purple-400" }
                    
                    return (
                      <div key={i} className={`${colorClass} ${bgClass} px-2 py-0.5 min-w-max`}>
                        {line || " "}
                      </div>
                    )
                  })}
                </pre>
              </div>
              
              <div className="p-4 border-t border-border flex justify-between items-center bg-muted/10">
                <p className="text-xs text-muted-foreground">
                  Внимание: Нажатие "Apply" навсегда применит этот патч к рабочей копии проекта.
                </p>
                <div className="flex gap-2">
                  <button 
                    disabled={processingId === selectedAction.id}
                    onClick={() => handleReject(selectedAction.id)}
                    className="flex items-center gap-2 rounded-md border border-border bg-background px-4 py-2 text-sm font-medium hover:bg-destructive hover:text-destructive-foreground disabled:opacity-50"
                  >
                    Discard
                  </button>
                  <button 
                    disabled={processingId === selectedAction.id}
                    onClick={() => handleApplyDiff(selectedAction.id)}
                    className="flex items-center gap-2 rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    <Check className="size-4" /> Apply Diff
                  </button>
                </div>
              </div>
            </div>
          )}

        </div>
      </div>
    </AppShell>
  )
}
