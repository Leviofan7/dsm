"use client"

import { useEffect, useState, use } from "react"
import { AppShell, MobileNav } from "@/components/app-shell"
import { AgentForm } from "../_components/agent-form"

export default function EditAgentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const [initialData, setInitialData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  useEffect(() => {
    fetchRole()
  }, [id])

  const fetchRole = async () => {
    try {
      const res = await fetch(`/api/roles/${id}`)
      if (!res.ok) {
        throw new Error("Failed to load agent")
      }
      const data = await res.json()
      setInitialData(data)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <AppShell>
      <MobileNav />
      <div className="flex-1 overflow-y-auto px-5 py-6 md:px-8">
        {loading ? (
          <p className="text-sm text-muted-foreground">Загрузка данных агента...</p>
        ) : error ? (
          <div className="text-red-500 bg-red-500/10 p-4 rounded-xl">Ошибка: {error}</div>
        ) : initialData ? (
          <AgentForm initialData={initialData} isNew={false} />
        ) : null}
      </div>
    </AppShell>
  )
}
