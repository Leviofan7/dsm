import { AppShell, MobileNav } from "@/components/app-shell"
import { ChatInterface } from "@/components/chat/chat-interface"

export default function ChatPage() {
  return (
    <AppShell>
      <MobileNav />
      <ChatInterface />
    </AppShell>
  )
}
