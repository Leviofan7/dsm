import { cn } from "@/lib/utils"
import type { SyncStatus } from "@/lib/data"

const config: Record<
  SyncStatus,
  { label: string; dot: string; text: string; ring: string }
> = {
  indexed: {
    label: "Indexed",
    dot: "bg-primary",
    text: "text-primary",
    ring: "border-primary/30 bg-primary/10",
  },
  indexing: {
    label: "Indexing",
    dot: "bg-chart-3 animate-pulse",
    text: "text-chart-3",
    ring: "border-chart-3/30 bg-chart-3/10",
  },
  queued: {
    label: "Queued",
    dot: "bg-muted-foreground",
    text: "text-muted-foreground",
    ring: "border-border bg-muted/40",
  },
  error: {
    label: "Error",
    dot: "bg-destructive",
    text: "text-destructive",
    ring: "border-destructive/30 bg-destructive/10",
  },
}

export function StatusBadge({ status }: { status: SyncStatus }) {
  const c = config[status]
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        c.ring,
        c.text,
      )}
    >
      <span className={cn("size-1.5 rounded-full", c.dot)} />
      {c.label}
    </span>
  )
}
