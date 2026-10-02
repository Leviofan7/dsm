"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Checkbox } from "@/components/ui/checkbox"
import { ArrowLeft, Save, ShieldAlert, RefreshCw, CheckCircle2, Trash2 } from "lucide-react"
import {
  ModeConfigSection,
  type ModeConfig,
  type ModelInfo,
  type ServerInfo,
} from "./mode-config-section"

interface ToolDef {
  name: string
  description: string
  is_privileged: boolean
}
interface AgentConfig { light: ModeConfig; heavy: ModeConfig }

const EMPTY_MODE_CONFIG: ModeConfig = { models: [], extra_mcps: [] }

/** Приводит конфиг с бэкенда к актуальному виду (legacy: model: "..." → models: [...]) */
function normalizeModeConfig(raw: any): ModeConfig {
  const models: string[] = Array.isArray(raw?.models)
    ? raw.models.filter((m: unknown) => typeof m === "string")
    : typeof raw?.model === "string" && raw.model
      ? [raw.model]
      : []
  const extra_mcps: string[] = Array.isArray(raw?.extra_mcps)
    ? raw.extra_mcps.filter((s: unknown) => typeof s === "string")
    : []
  return { models, extra_mcps }
}

function normalizeAgentConfig(raw: any): AgentConfig {
  return {
    light: normalizeModeConfig(raw?.light),
    heavy: normalizeModeConfig(raw?.heavy),
  }
}

export function AgentForm({ initialData = null, isNew = false }: { initialData?: any, isNew?: boolean }) {
  const router = useRouter()

  // Role fields
  const [id, setId] = useState(initialData?.id || "")
  const [name, setName] = useState(initialData?.name || "")
  const [description, setDescription] = useState(initialData?.description || "")
  const [planner, setPlanner] = useState(initialData?.planner || false)
  const [systemInstruction, setSystemInstruction] = useState(initialData?.system_instruction || "")
  const [selectedTools, setSelectedTools] = useState<string[]>(initialData?.tools || [])
  const [availableTools, setAvailableTools] = useState<ToolDef[]>([])

  // Orchestrator config fields
  const [models, setModels] = useState<ModelInfo[]>([])
  const [servers, setServers] = useState<ServerInfo[]>([])
  const [refreshingModels, setRefreshingModels] = useState(false)
  const [agentConfig, setAgentConfig] = useState<AgentConfig>({
    light: EMPTY_MODE_CONFIG,
    heavy: EMPTY_MODE_CONFIG,
  })

  const [loading, setLoading] = useState(false)
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved">("idle")
  const [deleteStatus, setDeleteStatus] = useState<"idle" | "deleting">("idle")

  const isProtected = initialData?.protected === true

  useEffect(() => {
    fetchTools()
    fetchOrchestratorData()
  }, [])

  const fetchTools = async () => {
    try {
      const res = await fetch("/api/tools")
      const data = await res.json()
      setAvailableTools(data || [])
    } catch (e) { console.error(e) }
  }

  const fetchOrchestratorData = async () => {
    try {
      const agentId = initialData?.id || id
      const [modelsRes, serversRes, configsRes] = await Promise.all([
        fetch("/api/models", { cache: "no-store" }).then(r => r.json()),
        fetch("/api/mcp-servers").then(r => r.json()),
        fetch("/api/agents/configs").then(r => r.json()),
      ])
      setModels(modelsRes.models || [])
      setServers(serversRes.servers || [])
      if (agentId && configsRes[agentId]) {
        setAgentConfig(normalizeAgentConfig(configsRes[agentId]))
      }
    } catch (e) { console.error(e) }
  }

  /**
   * Переспрашивает Ollama (`/api/tags` + `/api/show`) и перезагружает список моделей:
   * после `ollama pull` / `ollama rm` обновлять страницу целиком не нужно.
   */
  const refreshModels = async () => {
    setRefreshingModels(true)
    try {
      const res = await fetch("/api/models/refresh?deep=true", { method: "POST" })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        alert(`Не удалось обновить список моделей: ${err.detail ?? err.error ?? res.status}`)
        return
      }
      await fetchOrchestratorData()
    } catch (e) {
      console.error(e)
      alert("Ошибка сети при обновлении списка моделей")
    } finally {
      setRefreshingModels(false)
    }
  }

  const toggleTool = (toolName: string) => {
    setSelectedTools(prev =>
      prev.includes(toolName) ? prev.filter(t => t !== toolName) : [...prev, toolName]
    )
  }

  const setModeConfig = (mode: "light" | "heavy", next: ModeConfig) => {
    setAgentConfig(prev => ({ ...prev, [mode]: next }))
  }

  const handleDelete = async () => {
    if (!id) return
    if (!confirm(`Удалить агента «${name || id}»? Действие необратимо.`)) return

    setDeleteStatus("deleting")
    try {
      const res = await fetch(`/api/roles/${id}`, { method: "DELETE" })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        alert(`Ошибка удаления: ${err.detail ?? err.error ?? res.status}`)
        setDeleteStatus("idle")
        return
      }
      router.push("/agents")
    } catch (e) {
      console.error(e)
      alert("Ошибка сети при удалении")
      setDeleteStatus("idle")
    }
  }

  const handleSave = async () => {
    if (!id || !name) {
      alert("ID и Название обязательны")
      return
    }

    setSaveStatus("saving")
    setLoading(true)
    try {
      // 1. Роль (описание, инструкция, инструменты)
      const roleRes = await fetch(`/api/roles/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, name, description, planner, tools: selectedTools, system_instruction: systemInstruction }),
      })
      if (!roleRes.ok) {
        const err = await roleRes.json().catch(() => ({}))
        alert(`Ошибка сохранения роли: ${err.detail ?? err.error ?? roleRes.status}`)
        setSaveStatus("idle")
        return
      }

      // 2. Save orchestrator config (модели и MCP по режимам)
      const configRes = await fetch(`/api/agents/${id}/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(agentConfig),
      })
      if (!configRes.ok) {
        const err = await configRes.json().catch(() => ({}))
        alert(`Роль сохранена, но настройки моделей/MCP — нет: ${err.detail ?? err.error ?? configRes.status}`)
        setSaveStatus("idle")
        return
      }

      // 3. Refresh orchestrator (hot reload — no restart needed).
      // Best-effort: сама настройка уже сохранена, поэтому не роняем сохранение.
      const refreshRes = await fetch("/api/models/refresh", { method: "POST" })
      if (!refreshRes.ok) {
        console.warn("Не удалось обновить реестр моделей/MCP:", refreshRes.status)
      }

      setSaveStatus("saved")
      setTimeout(() => {
        setSaveStatus("idle")
        // Новый агент — сразу открываем его карточку; существующий — возвращаемся к списку
        if (isNew) {
          router.replace(`/agents/${id}`)
        } else {
          router.push("/agents")
        }
      }, 1000)
    } catch (e) {
      console.error(e)
      alert("Ошибка сети при сохранении")
      setSaveStatus("idle")
    } finally {
      setLoading(false)
    }
  }

  const renderModeSection = (mode: "light" | "heavy") => (
    <ModeConfigSection
      mode={mode}
      config={agentConfig[mode]}
      models={models}
      servers={servers}
      onChange={next => setModeConfig(mode, next)}
    />
  )

  return (
    <div className="mx-auto max-w-4xl space-y-8 pb-10">
      <div className="flex items-center gap-4">
        <Button variant="outline" size="icon" onClick={() => router.back()}>
          <ArrowLeft className="size-4" />
        </Button>
        <h2 className="text-xl font-semibold">
          {isNew ? "Создание нового агента" : `Настройка агента: ${initialData?.name}`}
        </h2>
        {isProtected && (
          <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-medium text-blue-800 dark:bg-blue-900/30 dark:text-blue-300">
            Системная роль
          </span>
        )}
        {!isNew && (
          <Button
            variant="ghost"
            className="ml-auto text-red-500 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/50"
            onClick={handleDelete}
            disabled={deleteStatus === "deleting" || isProtected}
            title={isProtected ? "Системную роль удалить нельзя" : "Удалить агента"}
          >
            <Trash2 className="mr-2 size-4" />
            {deleteStatus === "deleting" ? "Удаление..." : "Удалить"}
          </Button>
        )}
        <Button className={isNew ? "ml-auto" : ""} onClick={handleSave} disabled={loading}>
          {saveStatus === "saving" && <RefreshCw className="mr-2 size-4 animate-spin" />}
          {saveStatus === "saved" && <CheckCircle2 className="mr-2 size-4" />}
          {saveStatus === "idle" && <Save className="mr-2 size-4" />}
          {saveStatus === "saving" ? "Сохранение..." : saveStatus === "saved" ? "Сохранено!" : "Сохранить"}
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
        {/* Left: Role definition */}
        <div className="md:col-span-2 space-y-6">
          <div className="space-y-4 rounded-xl border border-border bg-card p-5">
            <h3 className="font-medium">Основные настройки</h3>
            <div className="grid gap-2">
              <Label htmlFor="id">ID (имя файла)</Label>
              <Input
                id="id"
                value={id}
                onChange={e => setId(e.target.value)}
                disabled={!isNew}
                placeholder="my_coder"
                className="font-mono"
              />
              <p className="text-xs text-muted-foreground">Используется как внутренний идентификатор (сохранится как .yaml)</p>
            </div>

            <div className="grid gap-2">
              <Label htmlFor="name">Название роли</Label>
              <Input id="name" value={name} onChange={e => setName(e.target.value)} placeholder="Frontend Developer" />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="description">Описание (для UI)</Label>
              <Textarea
                id="description"
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="Эксперт по Next.js и React..."
                rows={2}
              />
            </div>
          </div>

          <div className="space-y-4 rounded-xl border border-border bg-card p-5">
            <h3 className="font-medium">Системный Промпт (System Instruction)</h3>
            <div className="grid gap-2">
              <Textarea
                value={systemInstruction}
                onChange={e => setSystemInstruction(e.target.value)}
                placeholder="Ты — автономный ИИ-агент..."
                rows={12}
                className="font-mono text-sm leading-relaxed"
              />
              <p className="text-xs text-muted-foreground">Здесь задаются основные правила, тон и контекст агента.</p>
            </div>
          </div>

          {/* Orchestrator config (Model + MCP) */}
          <div className="space-y-4 rounded-xl border border-border bg-card p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="font-medium">Модели и MCP-серверы по режиму</h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Порядок моделей = приоритет: при выполнении берётся первая доступная (есть ключ / модель в Ollama).
                  Если ни одна не доступна — используется стандартная цепочка маршрутизации.
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="shrink-0"
                onClick={refreshModels}
                disabled={refreshingModels}
                title="Перечитать список моделей из Ollama (после ollama pull / ollama rm)"
              >
                <RefreshCw className={`mr-2 size-3.5 ${refreshingModels ? "animate-spin" : ""}`} />
                {refreshingModels ? "Обновление..." : "Обновить из Ollama"}
              </Button>
            </div>
            <div className="grid md:grid-cols-2 gap-4">
              {renderModeSection("light")}
              {renderModeSection("heavy")}
            </div>
          </div>
        </div>

        {/* Right: Execution engine + Tools */}
        <div className="space-y-6">
          <div className="space-y-4 rounded-xl border border-border bg-card p-5">
            <h3 className="font-medium">Движок выполнения</h3>
            <div className="flex items-center justify-between gap-4 py-2">
              <div className="space-y-0.5">
                <Label htmlFor="planner" className="text-sm font-medium">Planner Mode</Label>
                <p className="text-xs text-muted-foreground">Агент будет строить план действий перед выполнением задач.</p>
              </div>
              <Switch id="planner" checked={planner} onCheckedChange={setPlanner} />
            </div>
          </div>

          <div className="space-y-4 rounded-xl border border-border bg-card p-5">
            <h3 className="font-medium">Инструменты (Tools)</h3>
            <p className="text-xs text-muted-foreground">Выберите инструменты, к которым у агента будет доступ.</p>
            <div className="space-y-3 max-h-[400px] overflow-y-auto pr-2">
              {availableTools.map(tool => (
                <div key={tool.name} className="flex items-start space-x-3">
                  <Checkbox
                    id={`tool-${tool.name}`}
                    checked={selectedTools.includes(tool.name)}
                    onCheckedChange={() => toggleTool(tool.name)}
                  />
                  <div className="grid gap-1 leading-none">
                    <label
                      htmlFor={`tool-${tool.name}`}
                      className="text-sm font-medium leading-none cursor-pointer flex items-center gap-1.5"
                    >
                      {tool.name}
                      {tool.is_privileged && (
                        <span title="Привилегированный инструмент">
                          <ShieldAlert className="size-3 text-amber-500" />
                        </span>
                      )}
                    </label>
                    <p className="text-xs text-muted-foreground line-clamp-2">{tool.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
