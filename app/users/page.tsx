"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Shield, KeyRound, Copy, Send, Check } from "lucide-react"

export default function UsersPage() {
  const [users, setUsers] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [copiedId, setCopiedId] = useState<number | null>(null)

  async function fetchUsers() {
    try {
      const res = await fetch("/api/users")
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || "Не удалось загрузить пользователей (требуются права администратора)")
      }
      const data = await res.json()
      setUsers(data)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchUsers()
  }, [])

  async function toggleRole(id: number, currentRole: string) {
    try {
      const newRole = currentRole === "admin" ? "user" : "admin"
      const res = await fetch(`/api/users/${id}/role?role=${newRole}`, { method: "POST" })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        alert(err.detail || "Ошибка при изменении роли")
      }
      fetchUsers()
    } catch (e: any) {
      alert(e.message)
    }
  }

  async function generateLink(userId: number) {
    try {
      const res = await fetch(`/api/users/${userId}/link-telegram`, { method: "POST" })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || "Ошибка генерации ссылки")
      }
      const data = await res.json()
      if (data.link) {
        navigator.clipboard.writeText(data.link)
        setCopiedId(userId)
        setTimeout(() => setCopiedId(null), 3000)
        alert("Ссылка для привязки скопирована в буфер:\n" + data.link)
      }
    } catch (e: any) {
      alert(e.message)
    }
  }

  if (loading) return <div className="p-8">Загрузка...</div>
  if (error) return (
    <div className="p-8 space-y-4">
      <div className="text-red-500 font-medium">Ошибка: {error}</div>
      <Button variant="outline" onClick={() => window.location.href = "/login"}>
        Перейти на страницу входа
      </Button>
    </div>
  )

  return (
    <div className="flex-1 p-8 overflow-auto h-screen flex flex-col">
      <div className="flex justify-between items-center mb-8">
        <h1 className="text-2xl font-bold flex items-center gap-2"><Shield className="size-6 text-primary" /> Управление пользователями</h1>
      </div>

      <div className="rounded-md border border-border bg-card">
        <table className="w-full text-sm text-left">
          <thead className="bg-muted text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">ID</th>
              <th className="px-4 py-3 font-medium">Telegram ID</th>
              <th className="px-4 py-3 font-medium">Роль</th>
              <th className="px-4 py-3 font-medium text-right">Действия</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {users.map(u => (
              <tr key={u.id} className="hover:bg-muted/50">
                <td className="px-4 py-3">{u.id}</td>
                <td className="px-4 py-3 font-mono">
                  {u.telegram_chat_id ? (
                    <span className="inline-flex items-center gap-1 text-sky-500">
                      <Send className="size-3.5" />
                      {u.telegram_chat_id}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <span className={`inline-flex items-center px-2 py-1 rounded text-xs font-medium ${u.role === 'admin' ? 'bg-primary/10 text-primary' : 'bg-secondary text-secondary-foreground'}`}>
                    {u.role}
                  </span>
                </td>
                <td className="px-4 py-3 text-right space-x-2">
                  <Button 
                    variant="outline" 
                    size="sm" 
                    onClick={() => generateLink(u.id)}
                    className="gap-1.5"
                  >
                    {copiedId === u.id ? <Check className="size-3.5 text-green-500" /> : <KeyRound className="size-3.5" />}
                    {copiedId === u.id ? "Скопировано!" : "Привязать TG"}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => toggleRole(u.id, u.role)}>
                    Сделать {u.role === "admin" ? "user" : "admin"}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {users.length === 0 && <div className="p-4 text-center text-muted-foreground">Пользователей нет</div>}
      </div>
    </div>
  )
}

