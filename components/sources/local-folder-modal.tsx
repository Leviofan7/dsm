"use client"

import { useState } from "react"
import { FolderOpen, Loader2, ShieldCheck } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"

export function LocalFolderModal({
  open,
  onOpenChange,
  onConnect,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConnect: (path: string) => void
}) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [granting, setGranting] = useState(false)

  async function grantAccess() {
    setGranting(true)
    // File System Access API where available; fall back to a simulated path.
    try {
      const picker = (window as unknown as {
        showDirectoryPicker?: () => Promise<{ name: string }>
      }).showDirectoryPicker
      if (picker) {
        const handle = await picker()
        setSelectedPath(`/${handle.name}`)
      } else {
        setSelectedPath("/Users/lena/projects/my-app")
      }
    } catch {
      // user dismissed the picker
    } finally {
      setGranting(false)
    }
  }

  function handleConnect() {
    if (!selectedPath) return
    onConnect(selectedPath)
    setSelectedPath(null)
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <div className="mb-1 flex size-10 items-center justify-center rounded-lg border border-border bg-muted">
            <FolderOpen className="size-5" />
          </div>
          <DialogTitle>Connect a local directory</DialogTitle>
          <DialogDescription>
            Contextus requests <strong className="text-foreground">read-only</strong>{" "}
            access to the folder via your browser&apos;s File System Access API.
            Files are indexed locally and never leave your machine unencrypted.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-1">
          <div className="flex items-start gap-3 rounded-lg border border-border bg-muted/40 p-3">
            <ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" />
            <p className="text-sm leading-relaxed text-muted-foreground">
              You can revoke access at any time. We only read text-based files
              and respect your <code className="font-mono text-xs">.gitignore</code>.
            </p>
          </div>

          <Button
            variant="outline"
            className="w-full justify-center"
            onClick={grantAccess}
            disabled={granting}
          >
            {granting && <Loader2 className="size-4 animate-spin" />}
            {granting ? "Requesting access…" : "Grant Read Access"}
          </Button>

          {selectedPath && (
            <div className="rounded-md border border-primary/30 bg-primary/10 px-3 py-2 font-mono text-sm text-primary">
              Selected: {selectedPath}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleConnect} disabled={!selectedPath}>
            Connect Directory
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
