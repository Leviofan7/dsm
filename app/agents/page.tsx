"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { AppShell, MobileNav } from "@/components/app-shell"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card"
import { Bot, Plus, Settings2, Trash2 } from "lucide-react"

export default function AgentsPage() {
  const [roles, setRoles] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchRoles()
  }, [])

  const fetchRoles = async () => {
    try {
      const res = await fetch("/api/roles")
      // Список ролей закрыт require_admin: без сессии бэкенд отвечает 401.
      // Без этой ветки в roles попадал объект ошибки и страница падала на roles.map.
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        setError(err.detail ?? err.error ?? `Не удалось загрузить роли (HTTP ${res.status})`)
        setRoles([])
        return
      }
      const data = await res.json()
      setRoles(Array.isArray(data) ? data : [])
    } catch (e) {
      console.error(e)
      setError("Ошибка сети при загрузке ролей")
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async (id: string) => {
    if (!confirm("Вы уверены, что хотите удалить этого агента?")) return
    try {
      const res = await fetch(`/api/roles/${id}`, { method: "DELETE" })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        alert(`Ошибка удаления: ${err.detail ?? err.error ?? res.status}`)
        return
      }
      fetchRoles()
    } catch (e) {
      console.error(e)
      alert("Ошибка сети при удалении")
    }
  }

  return (
    <AppShell>
      <MobileNav />
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-border px-5 md:px-8">
        <div>
          <h1 className="text-lg font-semibold">Agents</h1>
          <p className="hidden text-sm text-muted-foreground sm:block">
            Управляйте ролями агентов и настраивайте их навыки.
          </p>
        </div>
        <Link href="/agents/new">
          <Button size="sm">
            <Plus className="mr-2 size-4" />
            Создать агента
          </Button>
        </Link>
      </header>

      <div className="flex-1 overflow-y-auto px-5 py-6 md:px-8">
        {loading ? (
          <p className="text-sm text-muted-foreground">Загрузка...</p>
        ) : error ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <h3 className="text-lg font-semibold mb-1">Роли недоступны</h3>
            <p className="text-sm text-muted-foreground max-w-sm mb-2">{error}</p>
            <p className="text-xs text-muted-foreground max-w-sm mb-4">
              Сессия живёт 7 дней. Чтобы войти заново, отправьте боту в Telegram команду{" "}
              <code>/login</code> и откройте ссылку — или вставьте токен на странице входа.
            </p>
            <Link href="/login">
              <Button size="sm">Перейти ко входу</Button>
            </Link>
          </div>
        ) : roles.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Bot className="size-12 text-muted-foreground mb-4 opacity-50" />
            <h3 className="text-lg font-semibold mb-1">Нет доступных агентов</h3>
            <p className="text-sm text-muted-foreground max-w-sm mb-4">
              Вы еще не создали ни одной кастомной роли агента. Нажмите "Создать агента", чтобы начать.
            </p>
            <Link href="/agents/new">
              <Button>Создать агента</Button>
            </Link>
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {roles.map((role) => (
              <Card key={role.id} className="flex flex-col">
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2">
                    <Bot className="size-4 text-primary" />
                    {role.name}
                  </CardTitle>
                  <CardDescription className="line-clamp-2 min-h-[2.5rem]">
                    {role.description || "Без описания"}
                  </CardDescription>
                </CardHeader>
                <CardContent className="flex-1">
                  <div className="flex flex-wrap gap-1">
                    {role.planner && (
                      <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-medium text-blue-800 dark:bg-blue-900/30 dark:text-blue-300">
                        Planner
                      </span>
                    )}
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-800 dark:bg-slate-800 dark:text-slate-300">
                      Tools: {role.tools?.length || 0}
                    </span>
                  </div>
                </CardContent>
                <CardFooter className="border-t border-border pt-4 flex gap-2">
                  <Link href={`/agents/${role.id}`} className="flex-1">
                    <Button variant="outline" size="sm" className="w-full">
                      <Settings2 className="mr-2 size-4" />
                      Настроить
                    </Button>
                  </Link>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleDelete(role.id)}
                    disabled={role.protected}
                    title={role.protected ? "Системную роль удалить нельзя" : "Удалить агента"}
                    className="text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/50"
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </CardFooter>
              </Card>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  )
}
