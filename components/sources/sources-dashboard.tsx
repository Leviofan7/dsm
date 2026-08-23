"use client"

import { useRef, useState, useEffect } from "react"
import {
  FileText,
  FolderOpen,
  GitBranch,
  Plus,
  RefreshCw,
  Trash2,
  UploadCloud,
} from "lucide-react"
import {
  type ConnectedSource,
  type SourceType,
} from "@/lib/data"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { StatusBadge } from "@/components/sources/status-badge"
import { GithubModal } from "@/components/sources/github-modal"
import { LocalFolderModal } from "@/components/sources/local-folder-modal"

const typeMeta: Record<
  SourceType,
  { label: string; icon: typeof GitBranch }
> = {
  github: { label: "GitHub", icon: GitBranch },
  local: { label: "Local", icon: FolderOpen },
  file: { label: "File", icon: FileText },
}

export function SourcesDashboard() {
  const [sources, setSources] = useState<ConnectedSource[]>([])
  const [githubOpen, setGithubOpen] = useState(false)
  const [folderOpen, setFolderOpen] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [loading, setLoading] = useState(true)
  const [reindexingIds, setReindexingIds] = useState<Set<string>>(new Set())
  const fileInputRef = useRef<HTMLInputElement>(null)

  async function fetchSources() {
    try {
      const res = await fetch("/api/sources")
      if (res.ok) {
        const data = await res.json()
        setSources(data)
      }
    } catch (err) {
      console.error("Failed to fetch sources", err)
    } finally {
      setLoading(false)
    }
  }

  // Initial load and polling
  useEffect(() => {
    fetchSources()
    const interval = setInterval(fetchSources, 4000)
    return () => clearInterval(interval)
  }, [])

  async function connectGithub(repo: string, branch: string, token: string) {
    try {
      await fetch("/api/sources/github", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo, branch, token }),
      })
      fetchSources()
    } catch (err) {
      console.error("Failed to connect github", err)
    }
  }

  async function connectFolder(path: string) {
    try {
      await fetch("/api/sources/local", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      })
      fetchSources()
    } catch (err) {
      console.error("Failed to connect folder", err)
    }
  }

  function addFiles(files: FileList | null) {
    if (!files) return
    // File upload logic to be implemented
  }

  async function disconnect(id: string) {
    // Optimistic UI update
    setSources((prev) => prev.filter((s) => s.id !== id))
    try {
      await fetch(`/api/sources/${id}`, {
        method: "DELETE",
      })
      fetchSources()
    } catch (err) {
      console.error("Failed to delete source", err)
      fetchSources() // Revert on failure
    }
  }

  async function reindex(id: string) {
    setReindexingIds((prev) => new Set(prev).add(id))
    try {
      const res = await fetch(`/api/sources/${id}/reindex`, { method: "POST" })
      if (!res.ok) {
        const err = await res.json()
        alert(err.detail || "Reindex failed")
        return
      }
      // Optimistically mark as indexing in UI
      setSources((prev) =>
        prev.map((s) => (s.id === id ? { ...s, status: "indexing" } : s))
      )
    } catch (err) {
      console.error("Failed to reindex source", err)
    } finally {
      setReindexingIds((prev) => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    }
  }

  return (
    <>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="border-border">
          <CardHeader>
            <div className="flex size-10 items-center justify-center rounded-lg border border-border bg-muted">
              <FolderOpen className="size-5" />
            </div>
            <CardTitle className="text-base">Local Folder</CardTitle>
            <CardDescription>
              Index a directory from your machine via the File System Access API.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              variant="secondary"
              className="w-full"
              onClick={() => setFolderOpen(true)}
            >
              Connect Local Directory
            </Button>
          </CardContent>
        </Card>

        <Card className="border-border">
          <CardHeader>
            <div className="flex size-10 items-center justify-center rounded-lg border border-border bg-muted">
              <GitBranch className="size-5" />
            </div>
            <CardTitle className="text-base">GitHub Repository</CardTitle>
            <CardDescription>
              Clone and index any public or private repository and branch.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button className="w-full" onClick={() => setGithubOpen(true)}>
              Connect Repository
            </Button>
          </CardContent>
        </Card>

        <Card
          className={cn(
            "border-2 border-dashed border-border transition-colors",
            dragging && "border-primary bg-primary/5",
          )}
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            addFiles(e.dataTransfer.files)
          }}
        >
          <CardHeader>
            <div className="flex size-10 items-center justify-center rounded-lg border border-border bg-muted">
              <UploadCloud className="size-5" />
            </div>
            <CardTitle className="text-base">File Upload</CardTitle>
            <CardDescription>
              Drag &amp; drop PDF, CSV, or DOCX — or browse to upload.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".pdf,.csv,.docx,.txt,.md"
              className="hidden"
              onChange={(e) => addFiles(e.target.files)}
            />
            <Button
              variant="outline"
              className="w-full"
              onClick={() => fileInputRef.current?.click()}
            >
              <Plus className="size-4" />
              Browse files
            </Button>
          </CardContent>
        </Card>
      </div>

      <section className="mt-8">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">
            Connected Sources
            <span className="ml-2 text-muted-foreground">{sources.length}</span>
          </h2>
        </div>

        <div className="overflow-hidden rounded-xl border border-border bg-card">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Source</TableHead>
                <TableHead className="hidden md:table-cell">Type</TableHead>
                <TableHead className="hidden lg:table-cell">Files</TableHead>
                <TableHead className="hidden sm:table-cell">Size</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="hidden xl:table-cell">Last Synced</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="h-24 text-center text-sm text-muted-foreground"
                  >
                    Loading sources...
                  </TableCell>
                </TableRow>
              ) : sources.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="h-24 text-center text-sm text-muted-foreground"
                  >
                    No sources connected yet. Connect one above to get started.
                  </TableCell>
                </TableRow>
              ) : null}
              {sources.map((source) => {
                const meta = typeMeta[source.type]
                const Icon = meta.icon
                return (
                  <TableRow key={source.id}>
                    <TableCell>
                      <div className="flex items-center gap-3">
                        <div className="flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-muted">
                          <Icon className="size-4" />
                        </div>
                        <div className="flex min-w-0 flex-col">
                          <span className="truncate font-medium">
                            {source.name}
                          </span>
                          <span className="truncate font-mono text-xs text-muted-foreground">
                            {source.detail}
                          </span>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="hidden md:table-cell">
                      <span className="rounded-md border border-border bg-muted/50 px-2 py-0.5 text-xs text-muted-foreground">
                        {meta.label}
                      </span>
                    </TableCell>
                    <TableCell className="hidden lg:table-cell tabular-nums text-muted-foreground">
                      {source.files.toLocaleString()}
                    </TableCell>
                    <TableCell className="hidden sm:table-cell tabular-nums text-muted-foreground">
                      {source.size}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={source.status} errorMessage={source.error_message} />
                    </TableCell>
                    <TableCell className="hidden xl:table-cell text-xs text-muted-foreground tabular-nums">
                      {source.updatedAt
                        ? new Date(source.updatedAt).toLocaleString("ru", {
                            day: "2-digit",
                            month: "2-digit",
                            hour: "2-digit",
                            minute: "2-digit",
                          })
                        : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        {source.type !== "local" && (
                          <Tooltip>
                            <TooltipTrigger
                              render={
                                <button
                                  type="button"
                                  disabled={source.status === "indexing" || source.status === "queued" || reindexingIds.has(source.id)}
                                  className="size-8 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-primary hover:bg-muted transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                                  onClick={() => reindex(source.id)}
                                />
                              }
                            >
                              <RefreshCw className={`size-4 ${
                                source.status === "indexing" || source.status === "queued"
                                  ? "animate-spin"
                                  : ""
                              }`} />
                              <span className="sr-only">Sync {source.name}</span>
                            </TooltipTrigger>
                            <TooltipContent>
                              Re-index
                            </TooltipContent>
                          </Tooltip>
                        )}
                        <Tooltip>
                          <TooltipTrigger
                            render={
                              <button
                                type="button"
                                className="size-8 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-destructive hover:bg-muted transition-colors"
                                onClick={() => disconnect(source.id)}
                              />
                            }
                          >
                            <Trash2 className="size-4" />
                            <span className="sr-only">Disconnect {source.name}</span>
                          </TooltipTrigger>
                          <TooltipContent>Disconnect</TooltipContent>
                        </Tooltip>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      </section>

      <GithubModal
        open={githubOpen}
        onOpenChange={setGithubOpen}
        onConnect={connectGithub}
      />
      <LocalFolderModal
        open={folderOpen}
        onOpenChange={setFolderOpen}
        onConnect={connectFolder}
      />
    </>
  )
}