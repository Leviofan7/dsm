"use client"

import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { ChevronDown, ChevronUp, Info, Monitor, Zap } from "lucide-react"

export interface ModelInfo {
  name: string
  provider: string            // provider_type: ollama | openai | anthropic
  provider_name?: string      // yaml-ключ провайдера: ollama | deepseek | gemini | anthropic
  model_id?: string           // id модели у провайдера (для Ollama — tag из `ollama list`)
  source?: "yaml" | "ollama"  // ollama = найдена автоматически в установке Ollama
  available: boolean
  installed?: boolean         // для локальных: есть ли модель в Ollama прямо сейчас
  supports_tools?: boolean
  supports_vision?: boolean
  context_window?: number
  tags?: string[]
}

export interface ServerInfo {
  name: string
  tools: string[]
  active: boolean
  available?: boolean
  engine_type: "tool" | "cli_engine"
  required_provider: string | null
  requires_bin?: string | null
}

/** Настройки одного режима: приоритетный список моделей + включённые MCP-серверы. */
export interface ModeConfig {
  models: string[]
  extra_mcps: string[]
}

const providerLabel = (m: ModelInfo) => m.provider_name ?? m.provider

/** Компактное описание модели для title-подсказки: id, контекст, возможности. */
const modelTooltip = (m: ModelInfo) => {
  const parts = [m.model_id ?? m.name]
  if (m.context_window) parts.push(`контекст ${Math.round(m.context_window / 1024)}k`)
  if (m.supports_vision) parts.push("vision")
  parts.push(m.supports_tools === false ? "без tools" : "tools")
  if (m.source === "ollama") parts.push("найдена в Ollama автоматически (нет в models.yaml)")
  if (m.installed === false) parts.push("❌ не найдена в Ollama — выполните `ollama pull`")
  return parts.join(" · ")
}

/** Сортировка: доступные → установленные локально → по имени. */
const sortModels = (list: ModelInfo[]) =>
  [...list].sort((a, b) => {
    if (a.available !== b.available) return a.available ? -1 : 1
    const aInstalled = a.installed !== false
    const bInstalled = b.installed !== false
    if (aInstalled !== bInstalled) return aInstalled ? -1 : 1
    return a.name.localeCompare(b.name)
  })

export function ModeConfigSection({
  mode,
  config,
  models,
  servers,
  onChange,
}: {
  mode: "light" | "heavy"
  config: ModeConfig
  models: ModelInfo[]
  servers: ServerInfo[]
  onChange: (next: ModeConfig) => void
}) {
  const isLight = mode === "light"
  const label = isLight ? "Light (Локальные)" : "Heavy (Облачные)"
  const icon = isLight
    ? <Zap className="size-4 text-green-500" />
    : <Monitor className="size-4 text-red-500" />

  const modeModels = sortModels(
    models.filter(m => (isLight ? m.provider === "ollama" : m.provider !== "ollama")),
  )
  const missingCount = modeModels.filter(m => m.installed === false).length
  const discoveredCount = modeModels.filter(m => m.source === "ollama").length
  const selectedModels = config.models ?? []
  const selectedMcps = config.extra_mcps ?? []

  const toggleModel = (name: string) => {
    const next = selectedModels.includes(name)
      ? selectedModels.filter(m => m !== name)
      : [...selectedModels, name]
    onChange({ ...config, models: next })
  }

  const moveModel = (name: string, direction: -1 | 1) => {
    const next = [...selectedModels]
    const index = next.indexOf(name)
    const target = index + direction
    if (index < 0 || target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    onChange({ ...config, models: next })
  }

  const toggleMcp = (name: string) => {
    const next = selectedMcps.includes(name)
      ? selectedMcps.filter(s => s !== name)
      : [...selectedMcps, name]
    onChange({ ...config, extra_mcps: next })
  }

  // CLI-движки запускают свою модель отдельно: это не ошибка, но полезно знать
  const selectedProviders = new Set(
    selectedModels
      .map(name => modeModels.find(m => m.name === name))
      .map(m => (m ? providerLabel(m) : undefined)),
  )
  const cliEngineNotes = servers.filter(
    s =>
      s.engine_type === "cli_engine" &&
      selectedMcps.includes(s.name) &&
      !!s.required_provider &&
      !selectedProviders.has(s.required_provider),
  )

  return (
    <div className={`rounded-xl border p-4 space-y-4 ${isLight ? "border-green-500/20 bg-green-500/5" : "border-red-500/20 bg-red-500/5"}`}>
      <div className="flex items-center gap-2">
        {icon}
        <h4 className="font-medium text-sm">{label}</h4>
      </div>

      <div className="space-y-2">
        <div className="flex items-baseline justify-between gap-2">
          <Label className="text-xs text-muted-foreground">
            Модели — порядок = приоритет (берётся первая доступная)
          </Label>
          <span className="text-[10px] text-muted-foreground shrink-0">
            {isLight
              ? `Ollama: ${modeModels.length - missingCount}${missingCount ? ` (+${missingCount} не найдено)` : ""}`
              : `облачных: ${modeModels.length}`}
          </span>
        </div>

        {isLight && discoveredCount > 0 && (
          <p className="text-[10px] text-muted-foreground">
            {discoveredCount} из них найдены автоматически в Ollama (badge «авто»): поддержка tools/vision
            и размер контекста берутся из /api/show самой Ollama.
          </p>
        )}

        {modeModels.length === 0 && (
          <p className="text-xs text-muted-foreground">Нет моделей этого типа в реестре</p>
        )}

        <div className="space-y-2 max-h-[220px] overflow-y-auto pr-1">
          {modeModels.map(m => {
            const index = selectedModels.indexOf(m.name)
            const selected = index >= 0
            const missing = m.installed === false
            return (
              <div key={m.name} className="flex items-center gap-2">
                <Checkbox
                  id={`model-${mode}-${m.name}`}
                  checked={selected}
                  onCheckedChange={() => toggleModel(m.name)}
                />
                <label
                  htmlFor={`model-${mode}-${m.name}`}
                  className="flex flex-1 items-center gap-2 cursor-pointer min-w-0"
                  title={modelTooltip(m)}
                >
                  <span className={`size-1.5 shrink-0 rounded-full ${m.available ? "bg-green-500" : "bg-red-400"}`} />
                  <span className={`font-mono text-xs truncate ${missing && !selected ? "text-muted-foreground" : ""}`}>
                    {m.name}
                  </span>
                  <span className="text-muted-foreground text-[10px] shrink-0">
                    ({providerLabel(m)})
                  </span>
                  {m.source === "ollama" && (
                    <span
                      className="rounded bg-blue-500/10 px-1 text-[10px] text-blue-600 dark:text-blue-400 shrink-0"
                      title="Найдена в вашей установке Ollama (нет в models.yaml)"
                    >
                      авто
                    </span>
                  )}
                  {m.supports_vision && (
                    <span className="text-[10px] text-muted-foreground shrink-0" title="Поддерживает изображения">
                      👁
                    </span>
                  )}
                  {m.supports_tools === false && (
                    <span className="text-[10px] text-amber-600 dark:text-amber-400 shrink-0" title="Модель не поддерживает вызов инструментов">
                      без tools
                    </span>
                  )}
                  {missing && (
                    <span
                      className="rounded bg-red-500/10 px-1 text-[10px] text-red-600 dark:text-red-400 shrink-0"
                      title="Модель отсутствует в Ollama — выполните `ollama pull`"
                    >
                      нет в Ollama
                    </span>
                  )}
                </label>

                {selected && (
                  <div className="flex items-center gap-1 shrink-0">
                    <span className="rounded bg-muted px-1.5 text-[10px] font-medium">{index + 1}</span>
                    <button
                      type="button"
                      onClick={() => moveModel(m.name, -1)}
                      disabled={index === 0}
                      className="text-muted-foreground disabled:opacity-30"
                      title="Выше приоритет"
                    >
                      <ChevronUp className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => moveModel(m.name, 1)}
                      disabled={index === selectedModels.length - 1}
                      className="text-muted-foreground disabled:opacity-30"
                      title="Ниже приоритет"
                    >
                      <ChevronDown className="size-3.5" />
                    </button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      <div className="space-y-2">
        <Label className="text-xs text-muted-foreground">MCP-серверы</Label>
        {servers.length === 0 && <p className="text-xs text-muted-foreground">MCP-серверы не найдены</p>}
        <div className="space-y-2 max-h-[220px] overflow-y-auto pr-1">
          {servers.map(s => {
            const available = s.available !== false
            return (
              <div key={s.name} className="flex items-center gap-2">
                <Checkbox
                  id={`mcp-${mode}-${s.name}`}
                  checked={selectedMcps.includes(s.name)}
                  onCheckedChange={() => toggleMcp(s.name)}
                />
                <label
                  htmlFor={`mcp-${mode}-${s.name}`}
                  className="flex flex-1 items-center gap-2 cursor-pointer min-w-0"
                >
                  <span className={`size-1.5 shrink-0 rounded-full ${available ? "bg-green-500" : "bg-red-400"}`} />
                  <span className="font-mono text-xs truncate">{s.name}</span>
                  {s.engine_type === "cli_engine" && (
                    <span className="text-[10px] text-muted-foreground shrink-0" title="Запускает свой CLI и делает независимый инференс">
                      ⚡ CLI
                    </span>
                  )}
                  {!s.active && (
                    <span className="text-[10px] text-muted-foreground shrink-0" title="Сервер не запущен в текущем процессе">
                      (не запущен)
                    </span>
                  )}
                </label>
              </div>
            )
          })}
        </div>
      </div>

      {cliEngineNotes.length > 0 && (
        <div className="space-y-1">
          {cliEngineNotes.map(s => (
            <p key={s.name} className="flex items-start gap-1.5 text-[10px] text-muted-foreground leading-tight">
              <Info className="mt-0.5 size-3 shrink-0" />
              <span>
                <span className="font-mono">{s.name}</span> делает отдельный вызов модели провайдера{" "}
                <span className="font-medium">{s.required_provider}</span> — это независимый инференс,
                совпадение с моделью агента не требуется.
              </span>
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
