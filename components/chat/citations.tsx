"use client"

import { FileCode2, FileText } from "lucide-react"
import type { ContextChunk } from "@/lib/data"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

function FileIcon({ path }: { path: string }) {
  const isDoc = /\.(md|txt|pdf|docx)$/.test(path)
  const Icon = isDoc ? FileText : FileCode2
  return <Icon className="size-3.5 shrink-0 text-muted-foreground" />
}

export function Citations({ sources }: { sources: ContextChunk[] }) {
  return (
    <Accordion type="single" collapsible className="mt-3">
      <AccordionItem
        value="sources"
        className="overflow-hidden rounded-lg border border-border bg-background/40"
      >
        <AccordionTrigger className="px-3 py-2 text-xs font-medium hover:no-underline">
          Used Context Sources
          <span className="ml-1.5 rounded-full bg-primary/15 px-1.5 text-primary">
            {sources.length}
          </span>
        </AccordionTrigger>
        <AccordionContent className="px-3 pb-3">
          <div className="grid gap-2">
            {sources.map((chunk, i) => (
              <Tooltip key={i}>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    className="group rounded-md border border-border bg-card p-2.5 text-left transition-colors hover:border-primary/40 hover:bg-accent/40"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-1.5">
                        <FileIcon path={chunk.path} />
                        <span className="truncate font-mono text-xs">
                          {chunk.path}
                        </span>
                        <span className="shrink-0 text-xs text-muted-foreground">
                          :{chunk.line}
                        </span>
                      </div>
                      <span className="shrink-0 rounded-full border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-primary">
                        {Math.round(chunk.score * 100)}% match
                      </span>
                    </div>
                    <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap rounded bg-muted/60 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
                      {chunk.quote}
                    </pre>
                  </button>
                </TooltipTrigger>
                <TooltipContent side="left" className="max-w-xs">
                  Retrieved from {chunk.path} (line {chunk.line})
                </TooltipContent>
              </Tooltip>
            ))}
          </div>
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  )
}
