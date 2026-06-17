"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  ArrowUp,
  FolderOpen,
  GitBranch,
  PanelRightClose,
  PanelRightOpen,
  X,
} from "lucide-react"
import {
  fileTree,
  initialMessages,
  type ChatMessage,
} from "@/lib/data"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { FileTree } from "@/components/chat/file-tree"
import { MessageBubble } from "@/components/chat/message-bubble"

interface ContextPill {
  id: string
  type: "github" | "local"
  label: string
}

const defaultPills: ContextPill[] = [
  { id: "p1", type: "github", label: "vercel/next.js (canary)" },
  { id: "p2", type: "local", label: "/ds-docs" },
]

export function ChatInterface() {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages)
  const [pills, setPills] = useState<ContextPill[]>(defaultPills)
  const [input, setInput] = useState("")
  const [panelOpen, setPanelOpen] = useState(true)
  const [selected, setSelected] = useState<Set<string>>(
    new Set(["src/auth/session.ts", "src/proxy.ts", "docs/auth/overview.md"]),
  )
  const scrollRef = useRef<HTMLDivElement>(null)

  const selectedCount = selected.size
  const activeTree = useMemo(() => fileTree, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    })
  }, [messages])

  function removePill(id: string) {
    setPills((prev) => prev.filter((p) => p.id !== id))
  }

  function send() {
    const text = input.trim()
    if (!text) return
    const userMsg: ChatMessage = {
      id: `u_${Date.now()}`,
      role: "user",
      content: text,
    }
    const reply: ChatMessage = {
      id: `a_${Date.now()}`,
      role: "assistant",
      content:
        "Based on the selected context, here's what I found. The implementation follows the pattern established in the indexed sources and is consistent across the connected repositories.",
      sources: [
        {
          path: "src/auth/service.ts",
          line: 92,
          score: 0.91,
          quote: "export async function getSession(req: Request) {\n  // ...\n}",
        },
        {
          path: "docs/auth/overview.md",
          line: 40,
          score: 0.84,
          quote: "Sessions are validated on every protected route via the proxy.",
        },
      ],
    }
    setMessages((prev) => [...prev, userMsg, reply])
    setInput("")
  }

  return (
    <div className="flex min-h-0 flex-1">
      {/* Chat area */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-border px-5 md:px-8">
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold">Assistant</h1>
            <p className="hidden text-sm text-muted-foreground sm:block">
              Grounded on {pills.length} active{" "}
              {pills.length === 1 ? "source" : "sources"}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPanelOpen((o) => !o)}
          >
            {panelOpen ? (
              <PanelRightClose className="size-4" />
            ) : (
              <PanelRightOpen className="size-4" />
            )}
            <span className="hidden sm:inline">Context</span>
          </Button>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-6 md:px-8">
          <div className="mx-auto flex max-w-3xl flex-col gap-6">
            {messages.map((m) => (
              <MessageBubble key={m.id} message={m} />
            ))}
          </div>
        </div>

        {/* Composer */}
        <div className="shrink-0 border-t border-border px-5 py-4 md:px-8">
          <div className="mx-auto max-w-3xl">
            {pills.length > 0 && (
              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                <span className="text-xs text-muted-foreground">Talking to:</span>
                {pills.map((pill) => (
                  <span
                    key={pill.id}
                    className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/60 py-1 pl-2 pr-1 text-xs"
                  >
                    {pill.type === "github" ? (
                      <GitBranch className="size-3" />
                    ) : (
                      <FolderOpen className="size-3 text-chart-3" />
                    )}
                    <span className="font-mono">{pill.label}</span>
                    <button
                      type="button"
                      onClick={() => removePill(pill.id)}
                      className="flex size-4 items-center justify-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground"
                    >
                      <X className="size-3" />
                      <span className="sr-only">Remove {pill.label}</span>
                    </button>
                  </span>
                ))}
              </div>
            )}

            <div className="flex items-end gap-2 rounded-2xl border border-border bg-card p-2 focus-within:border-primary/50">
              <Textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault()
                    send()
                  }
                }}
                placeholder="Ask about your connected sources…"
                rows={1}
                className="max-h-40 min-h-9 resize-none border-0 bg-transparent px-2 py-1.5 shadow-none focus-visible:ring-0"
              />
              <Button
                size="icon"
                className="size-9 shrink-0 rounded-xl"
                onClick={send}
                disabled={!input.trim()}
              >
                <ArrowUp className="size-4" />
                <span className="sr-only">Send</span>
              </Button>
            </div>
            <p className="mt-2 px-1 text-xs text-muted-foreground">
              {selectedCount} files included in context · Enter to send,
              Shift+Enter for newline
            </p>
          </div>
        </div>
      </div>

      {/* Context panel */}
      <aside
        className={cn(
          "hidden shrink-0 flex-col border-l border-border bg-sidebar transition-all lg:flex",
          panelOpen ? "w-80" : "w-0 overflow-hidden border-l-0",
        )}
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-sidebar-border px-4">
          <div className="flex flex-col leading-tight">
            <span className="text-sm font-semibold">Context</span>
            <span className="text-xs text-muted-foreground">
              vercel/next.js · canary
            </span>
          </div>
          <span className="rounded-full bg-primary/15 px-2 py-0.5 text-xs font-medium text-primary">
            {selectedCount} selected
          </span>
        </div>
        <div className="flex items-center justify-between px-4 py-2">
          <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Files
          </span>
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setSelected(new Set())}
          >
            Clear
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
          <FileTree tree={activeTree} selected={selected} onChange={setSelected} />
        </div>
      </aside>
    </div>
  )
}
