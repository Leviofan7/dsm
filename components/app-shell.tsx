"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import type { ReactNode } from "react"
import { Boxes, Database, MessagesSquare, Settings, Sparkles } from "lucide-react"
import { cn } from "@/lib/utils"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"

const nav = [
  { href: "/", label: "Data Sources", icon: Database },
  { href: "/chat", label: "Chat", icon: MessagesSquare },
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

  return (
    <div className="flex h-dvh w-full overflow-hidden bg-background text-foreground">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-sidebar md:flex">
        <div className="flex h-16 items-center gap-2.5 border-b border-sidebar-border px-5">
          <div className="flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Boxes className="size-5" />
          </div>
          <div className="flex flex-col leading-tight">
            <span className="text-sm font-semibold">Contextus</span>
            <span className="text-xs text-muted-foreground">RAG Console</span>
          </div>
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
