"use client"

import { useEffect, useState, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { Shield, KeyRound, Loader2, CheckCircle2, AlertCircle } from "lucide-react"
import { Button } from "@/components/ui/button"

function LoginForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const tokenFromUrl = searchParams.get("token") || ""
  
  const [token, setToken] = useState(tokenFromUrl)
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle")
  const [errorMessage, setErrorMessage] = useState("")

  async function handleLogin(tokenToUse: string) {
    if (!tokenToUse.trim()) return
    setLoading(true)
    setStatus("loading")
    setErrorMessage("")

    try {
      const res = await fetch("/api/auth/login-with-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: tokenToUse.trim() }),
      })

      const data = await res.json()
      if (!res.ok) {
        throw new Error(data.detail || data.error || "Неверный или истёкший токен")
      }

      setStatus("success")
      setTimeout(() => {
        router.push("/")
        router.refresh()
      }, 1000)
    } catch (e: any) {
      setStatus("error")
      setErrorMessage(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (tokenFromUrl) {
      handleLogin(tokenFromUrl)
    }
  }, [tokenFromUrl])

  return (
    <div className="flex min-h-screen items-center justify-center p-4 bg-background">
      <div className="w-full max-w-md p-8 space-y-6 rounded-xl border border-border bg-card shadow-lg">
        <div className="flex flex-col items-center text-center space-y-2">
          <div className="p-3 rounded-full bg-primary/10 text-primary">
            <Shield className="size-8" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight">Вход в Contextus Web UI</h1>
          <p className="text-sm text-muted-foreground">
            Авторизация по одноразовой ссылке или токену из Telegram-бота.
          </p>
        </div>

        {status === "loading" && (
          <div className="flex flex-col items-center justify-center p-6 space-y-3">
            <Loader2 className="size-8 animate-spin text-primary" />
            <p className="text-sm text-muted-foreground">Проверка токена и авторизация...</p>
          </div>
        )}

        {status === "success" && (
          <div className="flex flex-col items-center justify-center p-6 space-y-3 text-green-600 dark:text-green-400">
            <CheckCircle2 className="size-10" />
            <p className="text-sm font-medium">Успешный вход! Перенаправление...</p>
          </div>
        )}

        {status !== "loading" && status !== "success" && (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              handleLogin(token)
            }}
            className="space-y-4"
          >
            {status === "error" && (
              <div className="p-3 rounded-md bg-destructive/10 text-destructive text-sm flex items-center gap-2 border border-destructive/20">
                <AlertCircle className="size-4 shrink-0" />
                <span>{errorMessage}</span>
              </div>
            )}

            <div className="space-y-2">
              <label className="text-sm font-medium">Токен авторизации</label>
              <input
                type="text"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="Вставьте токен из Telegram"
                className="w-full px-3 py-2 text-sm rounded-md border border-input bg-background focus:outline-none focus:ring-2 focus:ring-primary"
                required
              />
            </div>

            <Button type="submit" className="w-full gap-2" disabled={loading || !token.trim()}>
              <KeyRound className="size-4" />
              Войти
            </Button>

            <div className="pt-4 border-t border-border text-center text-xs text-muted-foreground">
              Чтобы получить токен, отправьте команду <code className="bg-muted px-1.5 py-0.5 rounded font-mono">/login</code> в Telegram-бот.
            </div>
          </form>
        )}
      </div>
    </div>
  )
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="flex min-h-screen items-center justify-center">Загрузка...</div>}>
      <LoginForm />
    </Suspense>
  )
}
