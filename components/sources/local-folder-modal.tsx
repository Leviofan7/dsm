"use client"

import { useState, useEffect } from "react"
import { FolderOpen, Loader2 } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

export function LocalFolderModal({
  open,
  onOpenChange,
  onConnect,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConnect: (path: string) => Promise<void>
}) {
  const [path, setPath] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<any>(null)
  const [loadingPreview, setLoadingPreview] = useState(false)

  useEffect(() => {
    const trimmed = path.trim()
    if (!trimmed || !trimmed.startsWith("/")) {
      setPreview(null)
      return
    }
    const timer = setTimeout(async () => {
      setLoadingPreview(true)
      try {
        const res = await fetch(`/api/sources/preview?path=${encodeURIComponent(trimmed)}`)
        if (res.ok) {
          const data = await res.json()
          if (!data.error) setPreview(data)
          else setPreview(null)
        } else {
          setPreview(null)
        }
      } catch (e) {
        setPreview(null)
      } finally {
        setLoadingPreview(false)
      }
    }, 600)
    return () => clearTimeout(timer)
  }, [path])

  async function handleBrowse() {
    try {
      const picker = (window as any).showDirectoryPicker
      if (picker) {
        const handle = await picker()
        // Pre-fill with a likely path or just the name
        setPath(`/home/ai-line/Projects/${handle.name}`)
        setError(null)
      } else {
        setError("Кнопка 'Обзор' не поддерживается в этом браузере. Введите абсолютный путь вручную.")
      }
    } catch (e) {
      // User cancelled or error
      console.error(e)
    }
  }

  async function handleConnect() {
    const trimmed = path.trim()
    if (!trimmed) return
    setLoading(true)
    setError(null)
    try {
      await onConnect(trimmed)
      setPath("")
      onOpenChange(false)
    } catch (e: any) {
      setError(e?.message || "Не удалось подключить директорию")
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <div className="mb-1 flex size-10 items-center justify-center rounded-lg border border-border bg-muted">
            <FolderOpen className="size-5" />
          </div>
          <DialogTitle>Подключить локальную папку</DialogTitle>
          <DialogDescription>
            Укажите абсолютный путь к директории на сервере.
            Файлы будут читаться напрямую при обращении агента (Direct Snapshot).
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-1">
          <div className="flex items-center gap-2">
            <Input
              value={path}
              onChange={(e) => { setPath(e.target.value); setError(null) }}
              placeholder="/home/user/projects/my-app"
              onKeyDown={(e) => {
                if (e.key === "Enter") handleConnect()
              }}
              className="font-mono text-sm"
            />
            <Button type="button" variant="secondary" onClick={handleBrowse}>
              Обзор
            </Button>
          </div>

          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}

          {loadingPreview ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-3 animate-spin" />
              <span>Сканирование папки...</span>
            </div>
          ) : preview ? (
            <div className="rounded-md border border-border bg-muted/30 p-2 text-xs">
              <div className="flex items-center justify-between mb-1">
                <p className="font-medium">Структура корня ({preview.name}):</p>
                <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                  preview.mode === 'direct' ? 'bg-blue-500/20 text-blue-500' : 'bg-purple-500/20 text-purple-500'
                }`}>
                  {preview.mode === 'direct' ? '⚡ Direct Snapshot' : '🛠️ Workspace Mode'}
                </span>
              </div>
              <ul className="list-inside list-disc space-y-1 text-muted-foreground">
                <li>Папок: {preview.children?.filter((c: any) => c.type === "folder").length || 0}</li>
                <li>Текстовых файлов: {preview.children?.filter((c: any) => c.type === "file").length || 0}</li>
                <li>Оценка объёма: ~{Math.round(preview.tokens / 1000)}k токенов</li>
              </ul>
            </div>
          ) : null}

          <p className="text-xs text-muted-foreground">
            Файлы читаются напрямую из файловой системы. Бинарные файлы, <code>.git</code>,{" "}
            <code>node_modules</code> и скрытые директории пропускаются автоматически.
          </p>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button onClick={handleConnect} disabled={!path.trim() || loading}>
            {loading ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Подключение…
              </>
            ) : (
              "Подключить"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
