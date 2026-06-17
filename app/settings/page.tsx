import { AppShell, MobileNav } from "@/components/app-shell"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Separator } from "@/components/ui/separator"

const indexingOptions = [
  {
    id: "ignore",
    label: "Respect .gitignore",
    description: "Skip files matched by .gitignore during indexing.",
    defaultOn: true,
  },
  {
    id: "autosync",
    label: "Auto-sync repositories",
    description: "Re-index connected repos when new commits are pushed.",
    defaultOn: true,
  },
  {
    id: "citations",
    label: "Always show citations",
    description: "Expand the used context sources on every assistant reply.",
    defaultOn: false,
  },
]

export default function SettingsPage() {
  return (
    <AppShell>
      <MobileNav />
      <header className="flex h-16 shrink-0 items-center border-b border-border px-5 md:px-8">
        <div>
          <h1 className="text-lg font-semibold">Settings</h1>
          <p className="hidden text-sm text-muted-foreground sm:block">
            Configure how sources are indexed and retrieved.
          </p>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-5 py-6 md:px-8">
        <div className="mx-auto max-w-2xl space-y-6">
          <Card className="border-border">
            <CardHeader>
              <CardTitle className="text-base">Indexing &amp; retrieval</CardTitle>
              <CardDescription>
                Control how Contextus processes your connected sources.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-1">
              {indexingOptions.map((opt, i) => (
                <div key={opt.id}>
                  {i > 0 && <Separator className="my-1" />}
                  <div className="flex items-center justify-between gap-4 py-2">
                    <div className="space-y-0.5">
                      <Label htmlFor={opt.id} className="text-sm font-medium">
                        {opt.label}
                      </Label>
                      <p className="text-sm text-muted-foreground">
                        {opt.description}
                      </p>
                    </div>
                    <Switch id={opt.id} defaultChecked={opt.defaultOn} />
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card className="border-border">
            <CardHeader>
              <CardTitle className="text-base">Embedding model</CardTitle>
              <CardDescription>
                Vectors are generated with the model below.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between rounded-lg border border-border bg-muted/40 px-3 py-2.5">
                <span className="font-mono text-sm">text-embedding-3-large</span>
                <span className="rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                  Active
                </span>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </AppShell>
  )
}
