"use client"

import { Check, ChevronDown, Shield, AlertTriangle, Rocket, ShieldCheck } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

/**
 * Уровни доступа агента.
 *
 * Было: бинарный «Ученик» — либо подтверждай КАЖДЫЙ вызов (включая `list_dir` и
 * `read_file`, которые ничего не меняют), либо не подтверждай ничего. Первое давало
 * 6 карточек на простой осмотр репозитория, второе — полное отсутствие контроля.
 *
 * Значения совпадают с `backend/services/permissions.py`: `ask` | `allow_all` | `autopilot`.
 * Историческое `auto` трактуется бэкендом как `allow_all`, `apprentice` — как `ask`.
 */
export type PermissionLevel = "ask" | "allow_all" | "autopilot"

interface LevelOption {
  id: PermissionLevel
  label: string
  description: string
  icon: typeof Shield
}

const LEVELS: LevelOption[] = [
  {
    id: "ask",
    label: "Спрашивать",
    description: "План перед действием; подтверждение для записи и привилегированных вызовов",
    icon: Shield,
  },
  {
    id: "allow_all",
    label: "Разрешить всё",
    description: "Инструменты без вопросов; план виден для контроля",
    icon: AlertTriangle,
  },
  {
    id: "autopilot",
    label: "Автопилот",
    description: "Автономно в рамках прав роли: без плана и без подтверждений",
    icon: Rocket,
  },
]

/** Приводит сохранённое/старое значение к одному из трёх уровней. */
export function normalizeLevel(value: string | null | undefined): PermissionLevel {
  if (value === "ask" || value === "apprentice") return "ask"
  if (value === "autopilot") return "autopilot"
  return "allow_all"
}

interface PermissionSelectorProps {
  value: PermissionLevel
  onChange: (value: PermissionLevel) => void
}

export function PermissionSelector({ value, onChange }: PermissionSelectorProps) {
  const active = LEVELS.find((l) => l.id === value) ?? LEVELS[1]
  const ActiveIcon = active.id === "ask" ? ShieldCheck : active.icon

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant={active.id === "ask" ? "default" : "outline"}
            size="sm"
            className={cn(
              "h-8 gap-1.5 max-w-[170px] text-xs",
              active.id === "ask" && "bg-amber-600 hover:bg-amber-700 border-amber-600 text-white",
            )}
            title={`Доступ: ${active.label} — ${active.description}`}
          />
        }
      >
        <ActiveIcon className="size-3.5 shrink-0" />
        <span className="truncate">{active.label}</span>
        <ChevronDown className="size-3 shrink-0 opacity-50" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        {/* Base UI требует, чтобы Label/Item жили внутри Menu.Group — иначе Runtime Error. */}
        <DropdownMenuGroup>
          <DropdownMenuLabel className="text-[11px] uppercase tracking-wide text-muted-foreground">
            Разрешения агента
          </DropdownMenuLabel>
          {LEVELS.map((level) => {
            const Icon = level.icon
            const selected = level.id === active.id
            return (
              <DropdownMenuItem
                key={level.id}
                onClick={() => onChange(level.id)}
                className="flex-col items-start gap-0.5 py-2"
              >
                <div className="flex w-full items-center gap-2">
                  <Icon className={cn("size-4 shrink-0", selected ? "text-amber-500" : "text-muted-foreground")} />
                  <span className="font-medium">{level.label}</span>
                  <Check className={cn("ml-auto size-3.5", selected ? "opacity-100 text-amber-500" : "opacity-0")} />
                </div>
                <span className="pl-6 text-[10px] leading-snug text-muted-foreground">
                  {level.description}
                </span>
              </DropdownMenuItem>
            )
          })}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <div className="px-2 py-1.5 text-[10px] leading-snug text-muted-foreground">
          Границы роли (какие инструменты разрешены и есть ли право записи) действуют на любом уровне.
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
