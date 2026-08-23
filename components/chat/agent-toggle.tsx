"use client"

import { useState } from "react"
import { Sparkles, Settings2, Globe, Key, User, ShieldAlert, Plus, Trash2, ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"

export interface AgentAccount {
    id: string
    label: string
    url: string
    username: string
    password: string
}

interface AgentToggleProps {
    enabled: boolean
    onToggle: (value: boolean) => void
    accounts: AgentAccount[]
    onAccountsChange: (accounts: AgentAccount[]) => void
}

const defaultAccount = (): AgentAccount => ({
    id: `acc_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
    label: "",
    url: "https://duck.ai/chat",
    username: "",
    password: "",
})

export function AgentToggle({ enabled, onToggle, accounts = [], onAccountsChange }: AgentToggleProps) {
    const [isOpen, setIsOpen] = useState(false)
    const [expandedId, setExpandedId] = useState<string | null>(accounts[0]?.id ?? null)

    const handleSave = () => setIsOpen(false)

    function addAccount() {
        const acc = defaultAccount()
        onAccountsChange([...accounts, acc])
        setExpandedId(acc.id)
    }

    function removeAccount(id: string) {
        const next = accounts.filter(a => a.id !== id)
        if (expandedId === id) setExpandedId(next[next.length - 1]?.id ?? null)
        onAccountsChange(next)
    }

    function updateAccount(id: string, field: keyof AgentAccount, value: string) {
        onAccountsChange(accounts.map(a => a.id === id ? { ...a, [field]: value } : a))
    }

    return (
        <Dialog open={isOpen} onOpenChange={setIsOpen}>
            <button
                type="button"
                title={enabled ? "Настройки Агента — автономный режим активен" : "Настройки Агента"}
                onClick={() => setIsOpen(true)}
                className={cn(
                    "relative inline-flex size-9 shrink-0 items-center justify-center rounded-xl transition-all duration-300",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
                    "disabled:pointer-events-none disabled:opacity-50",
                    !enabled && "text-muted-foreground hover:text-foreground hover:bg-muted",
                    enabled && [
                        "bg-gradient-to-r from-purple-600 to-blue-600",
                        "text-white border-none",
                        "shadow-[0_0_15px_rgba(168,85,247,0.4)]",
                        "hover:opacity-90 hover:scale-105 active:scale-95"
                    ]
                )}
            >
                {enabled ? <Sparkles className="size-4 animate-pulse" /> : <Settings2 className="size-4" />}
                {enabled && (
                    <span className="absolute inset-0 rounded-xl bg-white/10 animate-ping pointer-events-none [animation-duration:3s]" />
                )}
            </button>

            <DialogContent className="sm:max-w-[480px] border-border/60 bg-background/95 backdrop-blur-xl">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2 text-xl">
                        <Sparkles className="size-5 text-purple-500" />
                        Звонок Другу
                    </DialogTitle>
                    <DialogDescription className="text-xs">
                        Настройте аккаунты для внешнего Playwright-воркера.
                        Джемма сможет использовать их для обхода экранов логина.
                    </DialogDescription>
                </DialogHeader>

                <div className="grid gap-5 py-2">
                    {/* Главный рубильник */}
                    <div className="flex items-center justify-between rounded-lg border border-purple-500/20 bg-purple-500/5 p-4 shadow-sm">
                        <div className="space-y-0.5">
                            <Label className="text-sm font-bold text-purple-400">Автономный режим</Label>
                            <p className="text-[11px] text-muted-foreground">
                                Разрешить Джемме вызывать внешнего агента
                            </p>
                        </div>
                        <Switch
                            checked={enabled}
                            onCheckedChange={onToggle}
                            className="data-[state=checked]:bg-purple-600"
                        />
                    </div>

                    {/* Список аккаунтов */}
                    <div className={cn(
                        "grid gap-2 transition-opacity duration-300",
                        !enabled && "opacity-50 pointer-events-none grayscale-[50%]"
                    )}>
                        <div className="flex items-center justify-between">
                            <Label className="text-xs text-muted-foreground uppercase tracking-wider">
                                Аккаунты ({accounts.length})
                            </Label>
                            <button
                                type="button"
                                onClick={addAccount}
                                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-purple-400 hover:bg-purple-500/10 transition-colors"
                            >
                                <Plus className="size-3" /> Добавить
                            </button>
                        </div>

                        <div className="grid gap-2 max-h-64 overflow-y-auto pr-0.5">
                            {accounts.map((acc, i) => {
                                const isExpanded = expandedId === acc.id
                                const displayLabel = acc.label || acc.url || `Аккаунт ${i + 1}`
                                return (
                                    <div key={acc.id} className="rounded-lg border border-border overflow-hidden">
                                        {/* Заголовок аккаунта */}
                                        <div className="flex items-center gap-2 bg-muted/40 px-3 py-2">
                                            <button
                                                type="button"
                                                onClick={() => setExpandedId(isExpanded ? null : acc.id)}
                                                className="flex flex-1 items-center gap-2 text-left min-w-0"
                                            >
                                                <ChevronDown className={cn(
                                                    "size-3.5 shrink-0 text-muted-foreground transition-transform duration-200",
                                                    isExpanded && "rotate-180"
                                                )} />
                                                <span className="truncate text-xs font-medium">{displayLabel}</span>
                                                {acc.username && (
                                                    <span className="shrink-0 text-[10px] text-muted-foreground">
                                                        · {acc.username}
                                                    </span>
                                                )}
                                            </button>
                                            {accounts.length > 1 && (
                                                <button
                                                    type="button"
                                                    onClick={() => removeAccount(acc.id)}
                                                    className="shrink-0 rounded p-1 text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
                                                >
                                                    <Trash2 className="size-3" />
                                                </button>
                                            )}
                                        </div>

                                        {/* Поля аккаунта */}
                                        {isExpanded && (
                                            <div className="grid gap-3 p-3">
                                                <div className="grid gap-1.5">
                                                    <Label htmlFor={`label-${acc.id}`} className="text-[11px] text-muted-foreground">
                                                        Название (необязательно)
                                                    </Label>
                                                    <Input
                                                        id={`label-${acc.id}`}
                                                        value={acc.label}
                                                        onChange={e => updateAccount(acc.id, "label", e.target.value)}
                                                        placeholder="Например: ChatGPT рабочий"
                                                        className="h-7 text-xs bg-muted/50"
                                                    />
                                                </div>
                                                <div className="grid gap-1.5">
                                                    <Label htmlFor={`url-${acc.id}`} className="text-[11px] flex items-center gap-1 text-muted-foreground">
                                                        <Globe className="size-3" /> URL
                                                    </Label>
                                                    <Input
                                                        id={`url-${acc.id}`}
                                                        value={acc.url}
                                                        onChange={e => updateAccount(acc.id, "url", e.target.value)}
                                                        placeholder="https://chatgpt.com"
                                                        className="h-7 text-xs font-mono bg-muted/50"
                                                    />
                                                </div>
                                                <div className="grid grid-cols-2 gap-3">
                                                    <div className="grid gap-1.5">
                                                        <Label htmlFor={`user-${acc.id}`} className="text-[11px] flex items-center gap-1 text-muted-foreground">
                                                            <User className="size-3" /> Логин
                                                        </Label>
                                                        <Input
                                                            id={`user-${acc.id}`}
                                                            value={acc.username}
                                                            onChange={e => updateAccount(acc.id, "username", e.target.value)}
                                                            placeholder="agent@ai.com"
                                                            className="h-7 text-xs bg-muted/50"
                                                        />
                                                    </div>
                                                    <div className="grid gap-1.5">
                                                        <Label htmlFor={`pass-${acc.id}`} className="text-[11px] flex items-center gap-1 text-muted-foreground">
                                                            <Key className="size-3" /> Пароль
                                                        </Label>
                                                        <Input
                                                            id={`pass-${acc.id}`}
                                                            type="password"
                                                            value={acc.password}
                                                            onChange={e => updateAccount(acc.id, "password", e.target.value)}
                                                            placeholder="••••••••"
                                                            className="h-7 text-xs bg-muted/50"
                                                        />
                                                    </div>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                )
                            })}
                        </div>
                    </div>

                    <div className="flex items-start gap-2 rounded-md bg-amber-500/10 p-3 text-[11px] text-amber-500/80">
                        <ShieldAlert className="size-4 shrink-0 mt-0.5" />
                        <p>
                            Данные передаются только на ваш локальный бэкенд.
                            Не используйте личные пароли, создавайте отдельные аккаунты для ИИ.
                        </p>
                    </div>
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={() => setIsOpen(false)} className="h-8 text-xs">
                        Отмена
                    </Button>
                    <Button
                        onClick={handleSave}
                        className="h-8 text-xs bg-gradient-to-r from-purple-600 to-blue-600 hover:opacity-90"
                    >
                        Сохранить и закрыть
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}