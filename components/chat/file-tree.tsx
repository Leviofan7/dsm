"use client"

import { useState } from "react"
import { ChevronRight, File, Folder, FolderOpen } from "lucide-react"
import type { TreeNode } from "@/lib/data"
import { cn } from "@/lib/utils"
import { Checkbox } from "@/components/ui/checkbox"

function collectFiles(node: TreeNode): string[] {
  if (node.type === "file") return [node.path]
  return (node.children ?? []).flatMap(collectFiles)
}

function TreeItem({
  node,
  depth,
  selected,
  onToggle,
}: {
  node: TreeNode
  depth: number
  selected: Set<string>
  onToggle: (paths: string[], checked: boolean) => void
}) {
  const [open, setOpen] = useState(depth < 2)

  if (node.type === "file") {
    const checked = selected.has(node.path)
    return (
      <label
        className="flex cursor-pointer items-center gap-2 rounded-md py-1 pr-2 text-sm hover:bg-accent/60"
        style={{ paddingLeft: `${depth * 14 + 8}px` }}
      >
        <Checkbox
          checked={checked}
          onCheckedChange={(v) => onToggle([node.path], Boolean(v))}
          className="size-3.5"
        />
        <File className="size-3.5 text-muted-foreground" />
        <span className="truncate font-mono text-xs">{node.name}</span>
      </label>
    )
  }

  const files = collectFiles(node)
  const allChecked = files.every((f) => selected.has(f))
  const someChecked = !allChecked && files.some((f) => selected.has(f))

  return (
    <div>
      <div
        className="flex items-center gap-2 rounded-md py-1 pr-2 text-sm hover:bg-accent/60"
        style={{ paddingLeft: `${depth * 14 + 8}px` }}
      >
        <Checkbox
          checked={someChecked ? "indeterminate" : allChecked}
          onCheckedChange={(v) => onToggle(files, Boolean(v))}
          className="size-3.5"
        />
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-1.5"
          onClick={() => setOpen((o) => !o)}
        >
          <ChevronRight
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-90",
            )}
          />
          {open ? (
            <FolderOpen className="size-3.5 text-chart-3" />
          ) : (
            <Folder className="size-3.5 text-chart-3" />
          )}
          <span className="truncate text-xs font-medium">{node.name}</span>
        </button>
      </div>
      {open &&
        node.children?.map((child) => (
          <TreeItem
            key={child.path}
            node={child}
            depth={depth + 1}
            selected={selected}
            onToggle={onToggle}
          />
        ))}
    </div>
  )
}

export function FileTree({
  tree,
  selected,
  onChange,
}: {
  tree: TreeNode[]
  selected: Set<string>
  onChange: (next: Set<string>) => void
}) {
  function toggle(paths: string[], checked: boolean) {
    const next = new Set(selected)
    paths.forEach((p) => (checked ? next.add(p) : next.delete(p)))
    onChange(next)
  }

  return (
    <div className="space-y-0.5">
      {tree.map((node) => (
        <TreeItem
          key={node.path}
          node={node}
          depth={0}
          selected={selected}
          onToggle={toggle}
        />
      ))}
    </div>
  )
}
