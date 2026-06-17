import Link from "next/link"
import { ArrowUpRight } from "lucide-react"
import { AppShell, MobileNav } from "@/components/app-shell"
import { SourcesDashboard } from "@/components/sources/sources-dashboard"
import { Button } from "@/components/ui/button"

export default function Page() {
  return (
    <AppShell>
      <MobileNav />
      <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-border px-5 md:px-8">
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold">Data Sources</h1>
          <p className="hidden text-sm text-muted-foreground sm:block">
            Connect and index the context your assistant can reason over.
          </p>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link href="/chat">
            Open Chat
            <ArrowUpRight className="size-4" />
          </Link>
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto px-5 py-6 md:px-8">
        <div className="mx-auto max-w-6xl">
          <SourcesDashboard />
        </div>
      </div>
    </AppShell>
  )
}
