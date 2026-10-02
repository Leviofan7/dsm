import { AppShell, MobileNav } from "@/components/app-shell"
import { AgentForm } from "../_components/agent-form"

export default function NewAgentPage() {
  return (
    <AppShell>
      <MobileNav />
      <div className="flex-1 overflow-y-auto px-5 py-6 md:px-8">
        <AgentForm isNew={true} />
      </div>
    </AppShell>
  )
}
