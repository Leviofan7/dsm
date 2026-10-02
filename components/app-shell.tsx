"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import type { ReactNode } from "react"
import { Boxes, BarChart2, Database, MessagesSquare, Settings, Sparkles, Bot, Shield, ChevronsLeft, ChevronsRight } from "lucide-react"
import { cn } from "@/lib/utils"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { ResizeHandle, useResizablePanel } from "@/components/ui/panel"

const nav = [
  { href: "/", label: "Data Sources", icon: Database },
  { href: "/chat", label: "Chat", icon: MessagesSquare },
  { href: "/analytics", label: "Analytics", icon: BarChart2 },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/users", label: "Users", icon: Shield },
  { href: "/settings", label: "Settings", icon: Settings },
]

export function AppShell({
  children,
  contentClassName,
}: {
  children: ReactNode
  contentClassName?: string
}) {
  const pathname = usePathname()
  // Ширина и свёрнутость навигации помнятся между запусками (см. components/ui/panel.tsx)
  const rail = useResizablePanel("contextus_nav", { initial: 240, min: 176, max: 400 })

  return (
    <div className="flex h-dvh w-full overflow-hidden bg-background text-foreground">
      {rail.collapsed && (
        <aside className="hidden w-12 shrink-0 flex-col items-center border-r border-border bg-sidebar py-4 md:flex">
          <button
            type="button"
            onClick={() => rail.setCollapsed(false)}
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground"
            title="Показать навигацию"
          >
            <ChevronsRight className="size-4" />
          </button>
        </aside>
      )}

      <aside
        className={cn(
          "hidden w-60 shrink-0 flex-col border-r border-border bg-sidebar md:flex",
          rail.collapsed && "md:hidden",
        )}
        style={rail.collapsed ? undefined : { width: rail.width }}
      >
        <div className="flex h-16 items-center gap-2.5 border-b border-sidebar-border px-4">
          <div className="flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Boxes className="size-5" />
          </div>
          <div className="flex min-w-0 flex-1 flex-col leading-tight">
            <span className="text-sm font-semibold">Contextus</span>
            <span className="truncate text-xs text-muted-foreground">RAG Console</span>
          </div>
          <button
            type="button"
            onClick={() => rail.setCollapsed(true)}
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground"
            title="Свернуть навигацию"
          >
            <ChevronsLeft className="size-4" />
          </button>
        </div>

        <nav className="flex-1 space-y-1 p-3">
          <p className="px-2 pb-2 pt-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Workspace
          </p>
          {nav.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href)
            const Icon = item.icon
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  active
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                )}
              >
                <Icon className="size-4" />
                {item.label}
              </Link>
            )
          })}
        </nav>

        <div className="m-3 rounded-lg border border-sidebar-border bg-sidebar-accent/40 p-3">
          <div className="mb-2 flex items-center gap-2 text-sm font-medium">
            <Sparkles className="size-4 text-primary" />
            Pro indexing
          </div>
          <p className="text-xs leading-relaxed text-muted-foreground">
            12,140 / 50,000 vectors used this month.
          </p>
          <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div className="h-full w-1/4 rounded-full bg-primary" />
          </div>
        </div>

        <div className="flex items-center gap-3 border-t border-sidebar-border p-3">
          <Avatar className="size-8">
            <AvatarFallback className="bg-muted text-xs">LN</AvatarFallback>
          </Avatar>
          <div className="flex flex-col leading-tight">
            <span className="text-sm font-medium">Lena Novak</span>
            <span className="text-xs text-muted-foreground">lena@acme.dev</span>
          </div>
        </div>
      </aside>

      {!rail.collapsed && (
        <ResizeHandle
          panel="left"
          className="hidden md:block"
          onResize={rail.resize}
          onReset={rail.reset}
        />
      )}

      <main className={cn("flex min-w-0 flex-1 flex-col overflow-hidden", contentClassName)}>
        {children}
      </main>
    </div>
  )
}

export function MobileNav() {
  const pathname = usePathname()
  return (
    <nav className="flex items-center gap-1 border-b border-border bg-sidebar px-2 py-2 md:hidden">
      {nav.map((item) => {
        const active =
          item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)
        const Icon = item.icon
        return (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              "flex flex-1 flex-col items-center gap-1 rounded-md px-2 py-1.5 text-xs font-medium",
              active ? "text-primary" : "text-muted-foreground",
            )}
          >
            <Icon className="size-4" />
            {item.label}
          </Link>
        )
      })}
    </nav>
  )
}
