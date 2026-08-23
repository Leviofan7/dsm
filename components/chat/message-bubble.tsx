"use client"

import { useState, useRef, useEffect } from "react"
import { Bot, User, Copy, Pencil, Trash2, Check, X } from "lucide-react"
import type { ChatMessage } from "@/lib/data"
import { cn } from "@/lib/utils"
import { Citations } from "@/components/chat/citations"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

export function MessageBubble({
  message,
  onDelete,
  onEdit,
}: {
  message: ChatMessage
  onDelete?: (id: string) => void
  onEdit?: (id: string, content: string) => void
}) {
  const isUser = message.role === "user"
  const [copied, setCopied] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editContent, setEditContent] = useState(message.content)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (editing && textareaRef.current) {
      textareaRef.current.focus()
      textareaRef.current.style.height = "auto"
      textareaRef.current.style.height = textareaRef.current.scrollHeight + "px"
    }
  }, [editing])

  function handleCopy() {
    navigator.clipboard.writeText(message.content)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  function handleSaveEdit() {
    if (editContent.trim() && onEdit) {
      onEdit(message.id, editContent.trim())
    }
    setEditing(false)
  }

  function handleCancelEdit() {
    setEditContent(message.content)
    setEditing(false)
  }

  return (
    <div className={cn("group relative flex gap-3", isUser && "flex-row-reverse")}>
      <div
        className={cn(
          "flex size-8 shrink-0 items-center justify-center rounded-full border",
          isUser
            ? "border-border bg-muted text-foreground"
            : "border-primary/30 bg-primary/15 text-primary",
        )}
      >
        {isUser ? <User className="size-4" /> : <Bot className="size-4" />}
      </div>

      <div className={cn("min-w-0 max-w-[85%]", isUser && "flex flex-col items-end")}>
        {editing ? (
          <div className="w-full rounded-2xl border border-primary/40 bg-card p-3">
            <textarea
              ref={textareaRef}
              value={editContent}
              onChange={(e) => {
                setEditContent(e.target.value)
                e.target.style.height = "auto"
                e.target.style.height = e.target.scrollHeight + "px"
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSaveEdit() }
                if (e.key === "Escape") handleCancelEdit()
              }}
              className="w-full resize-none bg-transparent text-sm leading-relaxed outline-none"
              rows={1}
            />
            <div className="mt-2 flex items-center gap-2 justify-end">
              <button
                onClick={handleSaveEdit}
                className="flex items-center gap-1 rounded-lg bg-primary px-3 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                <Check className="size-3" /> {isUser ? "Сохранить и отправить" : "Сохранить"}
              </button>
              <button
                onClick={handleCancelEdit}
                className="flex items-center gap-1 rounded-lg bg-muted px-3 py-1 text-xs font-medium text-muted-foreground hover:bg-accent transition-colors"
              >
                <X className="size-3" /> Отмена
              </button>
            </div>
          </div>
        ) : (
          <div
            className={cn(
              "rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
              isUser
                ? "rounded-tr-sm bg-primary text-primary-foreground"
                : "rounded-tl-sm border border-border bg-card text-card-foreground",
            )}
          >
            {isUser ? (
              <div className="whitespace-pre-wrap break-words">
                {message.content}
              </div>
            ) : (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  p: ({ node, ...props }) => <p className="mb-3 last:mb-0 break-words" {...props} />,
                  ul: ({ node, ...props }) => <ul className="mb-3 list-disc pl-5 last:mb-0" {...props} />,
                  ol: ({ node, ...props }) => <ol className="mb-3 list-decimal pl-5 last:mb-0" {...props} />,
                  li: ({ node, ...props }) => <li className="mb-1" {...props} />,
                  a: ({ node, ...props }) => (
                    <a className="font-medium underline underline-offset-4 hover:text-primary/80 break-words" target="_blank" rel="noopener noreferrer" {...props} />
                  ),
                  strong: ({ node, ...props }) => <strong className="font-semibold" {...props} />,
                  code: ({ node, className, children, ...props }) => {
                    const match = /language-(\w+)/.exec(className || "")
                    const isInline = !match && !String(children).includes("\n")
                    return isInline ? (
                      <code className="rounded bg-black/10 px-1 py-0.5 font-mono text-xs dark:bg-white/10 break-words" {...props}>
                        {children}
                      </code>
                    ) : (
                      <div className="my-3 overflow-x-auto rounded-md bg-black/50 border border-white/10 p-3 max-w-full">
                        <code className="block font-mono text-xs text-gray-300" {...props}>
                          {children}
                        </code>
                      </div>
                    )
                  },
                  table: ({ node, ...props }) => (
                    <div className="my-3 w-full overflow-y-auto">
                      <table className="w-full text-left text-sm" {...props} />
                    </div>
                  ),
                  th: ({ node, ...props }) => (
                    <th className="border-b border-border bg-muted/50 px-3 py-2 font-semibold" {...props} />
                  ),
                  td: ({ node, ...props }) => (
                    <td className="border-b border-border px-3 py-2" {...props} />
                  ),
                  blockquote: ({ node, ...props }) => (
                    <blockquote className="mt-3 border-l-2 border-border pl-4 italic text-muted-foreground break-words" {...props} />
                  ),
                }}
              >
                {message.content}
              </ReactMarkdown>
            )}
          </div>
        )}

        {/* Action buttons — visible on hover */}
        {!editing && (
          <div className={cn(
            "mt-1 flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100",
            isUser && "flex-row-reverse"
          )}>
            <button
              onClick={handleCopy}
              className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
              title="Копировать"
            >
              {copied ? <Check className="size-3.5 text-green-400" /> : <Copy className="size-3.5" />}
            </button>
            {onEdit && (
              <button
                onClick={() => { setEditContent(message.content); setEditing(true) }}
                className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                title="Редактировать"
              >
                <Pencil className="size-3.5" />
              </button>
            )}
            {onDelete && (
              <button
                onClick={() => onDelete(message.id)}
                className="rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
                title="Удалить"
              >
                <Trash2 className="size-3.5" />
              </button>
            )}
          </div>
        )}

        {!isUser && message.sources && message.sources.length > 0 && (
          <Citations sources={message.sources} />
        )}
      </div>
    </div>
  )
}
