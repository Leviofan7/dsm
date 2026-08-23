"use client"

import { useEffect, useState } from "react"
import { Check, ChevronDown, GraduationCap, Bot } from "lucide-react"
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

type AgentOption = {
  id: string
  name: string
  description: string
  type: "role" | "scenario" | "auto"
}

interface AgentSelectorProps {
  value: string
  onChange: (value: string) => void
  mode: string
  onModeChange: (value: string) => void
}

export function AgentSelector({ value, onChange, mode, onModeChange }: AgentSelectorProps) {
  const [agents, setAgents] = useState<AgentOption[]>([
    { id: "auto", name: "Auto (Doorman)", description: "Автоматический выбор агента", type: "auto" }
  ])

  useEffect(() => {
    async function fetchAgents() {
      try {
        const res = await fetch("/api/agents")
        if (res.ok) {
          const data = await res.json()
          setAgents([
            { id: "auto", name: "Auto (Doorman)", description: "Автоматический выбор агента", type: "auto" },
            ...(data.agents || [])
          ])
        }
      } catch (e) {
        console.error("Failed to fetch agents", e)
      }
    }
    fetchAgents()
  }, [])

  const selectedAgent = agents.find((a) => a.id === value) ?? agents[0]
  const roles = agents.filter(a => a.type === "role")
  const scenarios = agents.filter(a => a.type === "scenario")

  return (
    <div className="flex items-center gap-1.5">
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1.5 max-w-[160px] text-xs"
            />
          }
        >
          <Bot className="size-3 shrink-0" />
          <span className="truncate">{selectedAgent.name}</span>
          <ChevronDown className="size-3 shrink-0 opacity-50" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          <DropdownMenuGroup>
            <DropdownMenuLabel>Авто</DropdownMenuLabel>
            <DropdownMenuItem
              onClick={() => onChange("auto")}
              className="gap-2"
            >
              <Check className={cn("size-3.5", value === "auto" ? "opacity-100" : "opacity-0")} />
              Auto (Doorman)
            </DropdownMenuItem>
          </DropdownMenuGroup>

          {roles.length > 0 && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuLabel>Роли</DropdownMenuLabel>
                {roles.map((agent) => (
                  <DropdownMenuItem
                    key={agent.id}
                    onClick={() => onChange(agent.id)}
                    className="flex-col items-start gap-0.5"
                  >
                    <div className="flex items-center gap-2 w-full">
                      <Check className={cn("size-3.5 shrink-0", value === agent.id ? "opacity-100" : "opacity-0")} />
                      <span>{agent.name}</span>
                    </div>
                    {agent.description && (
                      <span className="pl-5 text-[10px] text-muted-foreground truncate w-full">
                        {agent.description}
                      </span>
                    )}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuGroup>
            </>
          )}

          {scenarios.length > 0 && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuLabel>Сценарии</DropdownMenuLabel>
                {scenarios.map((agent) => (
                  <DropdownMenuItem
                    key={agent.id}
                    onClick={() => onChange(agent.id)}
                    className="flex-col items-start gap-0.5"
                  >
                    <div className="flex items-center gap-2 w-full">
                      <Check className={cn("size-3.5 shrink-0", value === agent.id ? "opacity-100" : "opacity-0")} />
                      <span>{agent.name}</span>
                    </div>
                    {agent.description && (
                      <span className="pl-5 text-[10px] text-muted-foreground truncate w-full">
                        {agent.description}
                      </span>
                    )}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuGroup>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      <Button
        variant={mode === "apprentice" ? "default" : "outline"}
        size="sm"
        className={cn(
          "h-8 px-2 text-xs gap-1.5 transition-all",
          mode === "apprentice" && "bg-amber-600 hover:bg-amber-700 border-amber-600 text-white"
        )}
        onClick={() => onModeChange(mode === "apprentice" ? "auto" : "apprentice")}
        title="Apprentice Mode (Ученик) — каждый шаг агента требует вашего одобрения"
      >
        <GraduationCap className="size-3.5" />
        {mode === "apprentice" && <span className="hidden sm:inline">Ученик</span>}
      </Button>
    </div>
  )
}
