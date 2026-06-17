import { Bot, User } from "lucide-react"
import type { ChatMessage } from "@/lib/data"
import { cn } from "@/lib/utils"
import { Citations } from "@/components/chat/citations"

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user"

  return (
    <div className={cn("flex gap-3", isUser && "flex-row-reverse")}>
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
        <div
          className={cn(
            "rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
            isUser
              ? "rounded-tr-sm bg-primary text-primary-foreground"
              : "rounded-tl-sm border border-border bg-card text-card-foreground",
          )}
        >
          {message.content.split("\n\n").map((para, i) => (
            <p key={i} className={cn(i > 0 && "mt-3")}>
              {para}
            </p>
          ))}
        </div>

        {!isUser && message.sources && message.sources.length > 0 && (
          <Citations sources={message.sources} />
        )}
      </div>
    </div>
  )
}
