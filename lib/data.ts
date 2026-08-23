export type SourceType = "github" | "local" | "file"
export type SyncStatus = "indexed" | "indexing" | "error" | "queued" | "ready"

export interface ConnectedSource {
  id: string
  name: string
  type: SourceType
  detail: string
  files: number
  size: string
  status: SyncStatus
  updatedAt: string
}

export interface Conversation {
  id: string
  title: string
  createdAt: string
  sourceIds: string[]
}

export const connectedSources: ConnectedSource[] = [
  {
    id: "src_1",
    name: "vercel/next.js",
    type: "github",
    detail: "branch: canary",
    files: 8421,
    size: "642 MB",
    status: "indexed",
    updatedAt: "2 min ago",
  },
  {
    id: "src_3",
    name: "Design System Docs",
    type: "local",
    detail: "/Users/lena/projects/ds-docs",
    files: 312,
    size: "18 MB",
    status: "indexed",
    updatedAt: "1 hour ago",
  }
]

export interface ContextChunk {
  path: string
  line: number
  score: number
  quote: string
}

export type AgentMode = "fast" | "vision"

/** Ordered keys of the external-agent automation pipeline. */
export type AgentStepKey =
  | "init"
  | "browser"
  | "fasttrack"
  | "vision"
  | "success"

export interface ExternalResponse {
  source: string
  content: string
  isCode?: boolean
  timeSpent: number
  mode: AgentMode
  screenshots: number
}

export interface ChatMessage {
  id: string
  role: "user" | "assistant" | "external"
  content: string
  sources?: ContextChunk[]
  external?: ExternalResponse
  timestamp?: number
  steps?: any[]
}

export const mockExternalResponse: ExternalResponse = {
  source: "Duck.ai (GPT-4o mini / Claude)",
  content: "Пример ответа",
  isCode: false,
  timeSpent: 4.2,
  mode: "fast",
  screenshots: 0,
}

export const initialMessages: ChatMessage[] = [
  {
    id: "m1",
    role: "assistant",
    content: "Привет! Я готов к работе. Нажмите шестеренку, чтобы настроить агента.",
    timestamp: Date.now()
  }
]

export interface TreeNode {
  name: string
  type: "folder" | "directory" | "file"
  path: string
  children?: TreeNode[]
}

export const fileTree: TreeNode[] = [
  {
    name: "src",
    type: "folder",
    path: "src",
    children: [
      { name: "proxy.ts", type: "file", path: "src/proxy.ts" },
    ],
  }
]