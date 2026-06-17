"use client"

import { useRef, useState } from "react"
import {
  FileText,
  FolderOpen,
  GitBranch,
  Plus,
  Trash2,
  UploadCloud,
} from "lucide-react"
import {
  connectedSources as initialSources,
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
  const [sources, setSources] = useState<ConnectedSource[]>(initialSources)
  const [githubOpen, setGithubOpen] = useState(false)
  const [folderOpen, setFolderOpen] = useState(false)
  const [dragging, setDragging] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  function addSource(source: ConnectedSource) {
    setSources((prev) => [source, ...prev])
  }

  function connectGithub(repo: string, branch: string) {
    addSource({
      id: `src_${Date.now()}`,
      name: repo,
      type: "github",
      detail: `branch: ${branch}`,
      files: Math.floor(Math.random() * 4000) + 200,
      size: `${Math.floor(Math.random() * 400) + 20} MB`,
      status: "indexing",
      updatedAt: "now",
    })
  }

  function connectFolder(path: string) {
    addSource({
      id: `src_${Date.now()}`,
      name: path.split("/").filter(Boolean).pop() ?? "Local folder",
      type: "local",
      detail: path,
      files: Math.floor(Math.random() * 500) + 10,
      size: `${Math.floor(Math.random() * 40) + 2} MB`,
      status: "indexing",
      updatedAt: "now",
    })
  }

  function addFiles(files: FileList | null) {
    if (!files) return
    Array.from(files).forEach((file) => {
      addSource({
        id: `src_${Date.now()}_${file.name}`,
        name: file.name,
        type: "file",
        detail: `${file.name.split(".").pop()?.toUpperCase() ?? "FILE"} · upload`,
        files: 1,
        size: `${(file.size / 1024 / 1024).toFixed(1)} MB`,
        status: "queued",
        updatedAt: "now",
      })
    })
  }

  function disconnect(id: string) {
    setSources((prev) => prev.filter((s) => s.id !== id))
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
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sources.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="h-24 text-center text-sm text-muted-foreground"
                  >
                    No sources connected yet. Connect one above to get started.
                  </TableCell>
                </TableRow>
              )}
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
                      <StatusBadge status={source.status} />
                    </TableCell>
                    <TableCell className="text-right">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="size-8 text-muted-foreground hover:text-destructive"
                            onClick={() => disconnect(source.id)}
                          >
                            <Trash2 className="size-4" />
                            <span className="sr-only">
                              Disconnect {source.name}
                            </span>
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>Disconnect</TooltipContent>
                      </Tooltip>
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
