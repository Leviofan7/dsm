"use client"

import { useState } from "react"
import { GitBranch, Loader2, Lock } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export function GithubModal({
  open,
  onOpenChange,
  onConnect,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConnect: (repo: string, branch: string) => void
}) {
  const [repo, setRepo] = useState("")
  const [branch, setBranch] = useState("main")
  const [token, setToken] = useState("")
  const [loading, setLoading] = useState(false)

  function handleConnect() {
    if (!repo.trim()) return
    setLoading(true)
    setTimeout(() => {
      setLoading(false)
      onConnect(repo.trim(), branch.trim() || "main")
      setRepo("")
      setBranch("main")
      setToken("")
      onOpenChange(false)
    }, 1400)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <div className="mb-1 flex size-10 items-center justify-center rounded-lg border border-border bg-muted">
            <GitBranch className="size-5" />
          </div>
          <DialogTitle>Connect GitHub repository</DialogTitle>
          <DialogDescription>
            We&apos;ll clone and index the repository so the assistant can reason
            over its code.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-1">
          <div className="space-y-2">
            <Label htmlFor="repo">Repository URL</Label>
            <Input
              id="repo"
              placeholder="owner/repo"
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              className="font-mono"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="branch">Branch</Label>
            <Input
              id="branch"
              placeholder="main"
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
              className="font-mono"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="token" className="flex items-center gap-1.5">
              <Lock className="size-3.5 text-muted-foreground" />
              Personal Access Token
              <span className="font-normal text-muted-foreground">(private repos)</span>
            </Label>
            <Input
              id="token"
              type="password"
              placeholder="ghp_••••••••••••••••"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              className="font-mono"
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleConnect} disabled={loading || !repo.trim()}>
            {loading && <Loader2 className="size-4 animate-spin" />}
            {loading ? "Verifying…" : "Verify & Connect"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
