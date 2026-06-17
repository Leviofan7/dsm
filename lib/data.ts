export type SourceType = "github" | "local" | "file"
export type SyncStatus = "indexed" | "indexing" | "error" | "queued"

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
    id: "src_2",
    name: "acme/payments-service",
    type: "github",
    detail: "branch: main · private",
    files: 1203,
    size: "94 MB",
    status: "indexing",
    updatedAt: "now",
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
  },
  {
    id: "src_4",
    name: "Q3-financial-report.pdf",
    type: "file",
    detail: "PDF · 42 pages",
    files: 1,
    size: "3.2 MB",
    status: "indexed",
    updatedAt: "yesterday",
  },
  {
    id: "src_5",
    name: "customer-feedback.csv",
    type: "file",
    detail: "CSV · 14,902 rows",
    files: 1,
    size: "5.1 MB",
    status: "error",
    updatedAt: "3 hours ago",
  },
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
  /** Display name of the upstream provider, e.g. "Duck.ai (GPT-4o mini / Claude)". */
  source: string
  /** The returned answer (markdown / code). */
  content: string
  /** Whether the content should render as a fenced code block. */
  isCode?: boolean
  /** Seconds elapsed end-to-end. */
  timeSpent: number
  /** Which path the agent took. */
  mode: AgentMode
  /** How many screenshots the Vision fallback analyzed. */
  screenshots: number
}

export interface ChatMessage {
  id: string
  role: "user" | "assistant" | "external"
  content: string
  sources?: ContextChunk[]
  external?: ExternalResponse
}

export const mockExternalResponse: ExternalResponse = {
  source: "Duck.ai (GPT-4o mini / Claude)",
  content:
    "Для проактивной ротации токенов используйте фоновый таймер, который срабатывает за 60 секунд до истечения срока действия. Это исключает гонки состояний при параллельных запросах:\n\nconst REFRESH_THRESHOLD_MS = 60_000\n\nasync function ensureFreshToken(session) {\n  if (session.expiresAt - Date.now() < REFRESH_THRESHOLD_MS) {\n    return rotateTokens(session.refreshToken)\n  }\n  return session\n}",
  isCode: true,
  timeSpent: 4.2,
  mode: "fast",
  screenshots: 0,
}

export const initialMessages: ChatMessage[] = [
  {
    id: "m1",
    role: "user",
    content: "How does the auth middleware decide when to refresh a session token?",
  },
  {
    id: "m2",
    role: "assistant",
    content:
      "The middleware checks the token's expiry against a refresh threshold. When the access token is within 60 seconds of expiring, it silently exchanges the refresh token for a new pair before continuing the request. If refresh fails, it clears the session cookie and redirects to /login.\n\nThe relevant logic lives in the session service and is invoked from the proxy entrypoint on every protected route.",
    sources: [
      {
        path: "src/auth/session.ts",
        line: 45,
        score: 0.94,
        quote:
          "if (expiresAt - Date.now() < REFRESH_THRESHOLD_MS) {\n  return await rotateTokens(refreshToken)\n}",
      },
      {
        path: "proxy.ts",
        line: 18,
        score: 0.88,
        quote:
          "const session = await getSession(req)\nif (!session) return redirect('/login')",
      },
      {
        path: "docs/auth/overview.md",
        line: 112,
        score: 0.81,
        quote:
          "Tokens are rotated proactively to avoid mid-request expiry. The refresh window defaults to 60s.",
      },
    ],
  },
]

export interface TreeNode {
  name: string
  type: "folder" | "file"
  path: string
  children?: TreeNode[]
}

export const fileTree: TreeNode[] = [
  {
    name: "src",
    type: "folder",
    path: "src",
    children: [
      {
        name: "auth",
        type: "folder",
        path: "src/auth",
        children: [
          { name: "session.ts", type: "file", path: "src/auth/session.ts" },
          { name: "service.ts", type: "file", path: "src/auth/service.ts" },
          { name: "tokens.ts", type: "file", path: "src/auth/tokens.ts" },
        ],
      },
      {
        name: "app",
        type: "folder",
        path: "src/app",
        children: [
          { name: "page.tsx", type: "file", path: "src/app/page.tsx" },
          { name: "layout.tsx", type: "file", path: "src/app/layout.tsx" },
        ],
      },
      { name: "proxy.ts", type: "file", path: "src/proxy.ts" },
    ],
  },
  {
    name: "docs",
    type: "folder",
    path: "docs",
    children: [
      { name: "overview.md", type: "file", path: "docs/auth/overview.md" },
      { name: "deploy.md", type: "file", path: "docs/deploy.md" },
    ],
  },
  { name: "README.md", type: "file", path: "README.md" },
]
