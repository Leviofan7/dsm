"use client"

import { useState } from "react"
import { MessageSquarePlus, Trash2, Pencil, Check, X } from "lucide-react"
import type { Conversation } from "@/lib/data"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

export function ConversationList({
  conversations,
  activeId,
  onSelect,
  onCreate,
  onDelete,
  onRename,
}: {
  conversations: Conversation[]
  activeId: string | null
  onSelect: (id: string) => void
  onCreate: () => void
  onDelete: (id: string) => void
  onRename: (id: string, title: string) => void
}) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState("")

  function startRename(conv: Conversation) {
    setEditingId(conv.id)
    setEditTitle(conv.title)
  }

  function commitRename() {
    if (editingId && editTitle.trim()) {
      onRename(editingId, editTitle.trim())
    }
    setEditingId(null)
  }

  return (
    <div className="flex flex-col gap-1">
      <Button
        variant="outline"
        size="sm"
        className="mb-2 w-full justify-start gap-2"
        onClick={onCreate}
      >
        <MessageSquarePlus className="size-4" />
        Новый чат
      </Button>

      {conversations.map((conv) => (
        <div
          key={conv.id}
          className={cn(
            "group flex items-center gap-1 rounded-md px-2 py-1.5 text-sm cursor-pointer transition-colors",
            conv.id === activeId
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground"
          )}
          onClick={() => {
            if (editingId !== conv.id) onSelect(conv.id)
          }}
        >
          {editingId === conv.id ? (
            <div className="flex flex-1 items-center gap-1">
              <input
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commitRename()
                  if (e.key === "Escape") setEditingId(null)
                }}
                className="flex-1 rounded border border-border bg-background px-1.5 py-0.5 text-xs"
                autoFocus
                onClick={(e) => e.stopPropagation()}
              />
              <button onClick={(e) => { e.stopPropagation(); commitRename() }} className="text-primary hover:text-primary/80">
                <Check className="size-3.5" />
              </button>
              <button onClick={(e) => { e.stopPropagation(); setEditingId(null) }} className="text-muted-foreground hover:text-foreground">
                <X className="size-3.5" />
              </button>
            </div>
          ) : (
            <>
              <span className="flex-1 truncate text-xs font-medium">{conv.title}</span>
              <div className="flex items-center gap-0.5 opacity-50 transition-opacity group-hover:opacity-100 focus-within:opacity-100 md:opacity-0">
                <button
                  onClick={(e) => { e.stopPropagation(); startRename(conv) }}
                  className="rounded p-1 hover:bg-accent hover:text-foreground"
                >
                  <Pencil className="size-3.5" />
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); onDelete(conv.id) }}
                  className="rounded p-1 text-destructive hover:bg-destructive/10"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            </>
          )}
        </div>
      ))}
    </div>
  )
}
