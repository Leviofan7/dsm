"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  ArrowUp,
  Bug,
  FolderOpen,
  GitBranch,
  MessagesSquare,
  PanelRightClose,
  PanelRightOpen,
  X,
  ShieldAlert,
} from "lucide-react"
import {
  type ChatMessage,
  type ConnectedSource,
  type Conversation,
  type TreeNode,
} from "@/lib/data"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { FileTree } from "@/components/chat/file-tree"
import { MessageBubble } from "@/components/chat/message-bubble"
import { AgentToggle, type AgentAccount } from "@/components/chat/agent-toggle"
import { AgentSelector } from "@/components/chat/agent-selector"
import { ComplexityToggle } from "@/components/chat/complexity-toggle"
import { ApprenticeCard, type ApprenticeStep } from "@/components/chat/apprentice-card"
import { AgentThinkingBubble, type AgentThinkingStep } from "@/components/chat/agent-thinking-bubble"
import { ConversationList } from "@/components/chat/conversation-list"
import { SourceSelector } from "@/components/chat/source-selector"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"

/** Ключ localStorage: активный чат должен переживать перезагрузку страницы и HMR. */
const ACTIVE_CONV_STORAGE_KEY = "contextus_active_conversation"

export function ChatInterface() {
  // --- Conversations ---
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConvId, setActiveConvId] = useState<string | null>(null)
  const [convDialogOpen, setConvDialogOpen] = useState(false)

  // --- Sources ---
  const [allSources, setAllSources] = useState<ConnectedSource[]>([])

  // --- Messages ---
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState("")
  const [panelOpen, setPanelOpen] = useState(true)

  // --- File tree ---
  const [fileTreeData, setFileTreeData] = useState<TreeNode[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())

  // --- Agent ---
  const [agentAllowed, setAgentAllowed] = useState(false)
  const [isAgentWorking, setIsAgentWorking] = useState(false)
  const [streamingContent, setStreamingContent] = useState("")
  const [agentSteps, _setAgentSteps] = useState<AgentThinkingStep[]>([])
  const agentStepsRef = useRef<AgentThinkingStep[]>([])
  const setAgentSteps = useCallback((val: AgentThinkingStep[] | ((prev: AgentThinkingStep[]) => AgentThinkingStep[])) => {
    if (typeof val === "function") {
      _setAgentSteps(prev => {
        const next = val(prev)
        agentStepsRef.current = next
        return next
      })
    } else {
      _setAgentSteps(val)
      agentStepsRef.current = val
    }
  }, [])
  const [accounts, setAccounts] = useState<AgentAccount[]>([])
  const [debugMode, setDebugMode] = useState(false)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [targetAgent, setTargetAgent] = useState("auto")
  const [mode, setMode] = useState("auto")
  const [complexity, setComplexity] = useState<"light" | "heavy" | "auto">("auto")
  const [pendingApprenticeStep, setPendingApprenticeStep] = useState<ApprenticeStep | null>(null)

  useEffect(() => {
    const saved = localStorage.getItem("chatComplexity")
    if (saved === "light" || saved === "heavy" || saved === "auto") {
      setComplexity(saved)
    }
  }, [])

  const handleComplexityChange = (val: "light" | "heavy" | "auto") => {
    setComplexity(val)
    localStorage.setItem("chatComplexity", val)
  }

  // Активный чат переживает перезагрузку страницы и HMR: без этого любой ремоунт
  // компонента оставлял пустой чат, и казалось, что отправленный вопрос «пропал».
  useEffect(() => {
    const saved = localStorage.getItem(ACTIVE_CONV_STORAGE_KEY)
    if (saved) setActiveConvId(saved)
  }, [])

  useEffect(() => {
    if (activeConvId) localStorage.setItem(ACTIVE_CONV_STORAGE_KEY, activeConvId)
    else localStorage.removeItem(ACTIVE_CONV_STORAGE_KEY)
  }, [activeConvId])

  // Примечание: карточки ActionRequest (Gate 2.0) в веб-UI намеренно НЕ реализованы.
  // Подтверждения этой очереди идут через Telegram (кнопки в сообщении), см. решение
  // в памяти проекта. Раньше здесь была карточка, но события `action_request` бэкенд
  // не эмитил, а сам компонент упал бы на рассинхроне action_type (RUN_COMMAND vs
  // request_command_execution), — удалён вместе с legacy /improvements.

  const abortControllerRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const streamingTimestampRef = useRef<number>(Date.now())
  // Прилипание автоскролла: пока пользователь внизу — ведём вьюпорт, если он
  // ушёл читать историю — не выдёргиваем его на каждый шаг агента.
  const atBottomRef = useRef(true)
  // Сохранилось ли сообщение пользователя в БД: если POST не прошёл, ресинк с сервером
  // затёр бы сообщение из UI, которого там ещё нет
  const userPersistedRef = useRef(true)

  const activeConv = conversations.find((c) => c.id === activeConvId) ?? null
  const selectedCount = selected.size

  // ========== Data fetching ==========

  async function fetchConversations() {
    try {
      const res = await fetch("/api/conversations")
      if (res.ok) setConversations(await res.json())
    } catch (e) {
      console.error("fetchConversations error:", e)
    }
  }

  async function fetchSources() {
    try {
      const res = await fetch("/api/sources")
      if (res.ok) setAllSources(await res.json())
    } catch (e) {
      console.error("fetchSources error:", e)
    }
  }

  async function fetchMessages(convId: string) {
    try {
      const res = await fetch(`/api/conversations/${convId}/messages`)
      if (res.status === 404) {
        // Чат удалён (например, id восстановили из localStorage) — сбрасываем выбор
        setActiveConvId((prev) => (prev === convId ? null : prev))
        setMessages([])
        return
      }
      if (res.ok) {
        const data = await res.json()
        setMessages(data.map((m: any) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          timestamp: m.timestamp,
          // steps агента тоже persisted в БД: раньше они отбрасывались при загрузке,
          // и «мысли» исчезали после перезагрузки или ресинка
          steps: Array.isArray(m.steps) && m.steps.length > 0 ? m.steps : undefined,
        })))
      }
    } catch (e) {
      console.error("fetchMessages error:", e)
    }
  }

  async function fetchFileTree(sourceIds: string[]) {
    if (sourceIds.length === 0) {
      setFileTreeData([])
      return
    }
    try {
      const trees = await Promise.all(
        sourceIds.map(async (id) => {
          const res = await fetch(`/api/sources/${id}/tree`)
          if (!res.ok) return []
          return res.json()
        })
      )
      // Merge trees: wrap each source's tree in a source-name folder
      const merged: TreeNode[] = []
      for (let i = 0; i < sourceIds.length; i++) {
        const source = allSources.find((s) => s.id === sourceIds[i])
        const treePart = trees[i] as TreeNode[]
        if (source && treePart.length > 0) {
          merged.push({
            name: source.name,
            type: "folder",
            path: `__source__/${source.id}`,
            children: treePart,
          })
        }
      }
      setFileTreeData(merged)
    } catch (e) {
      console.error("fetchFileTree error:", e)
    }
  }

  // ========== Init & effects ==========
  // Poll for Apprentice pending step when mode is apprentice and agent is working
  useEffect(() => {
    if (mode !== "apprentice" || !activeConvId) {
      setPendingApprenticeStep(null)
      return
    }
    
    // We only need to poll if the last step was an apprentice_pause, OR if the agent is working
    let interval: any;
    
    const checkPending = async () => {
      try {
        const res = await fetch(`/api/apprentice/${activeConvId}/pending`)
        if (res.ok) {
          const data = await res.json()
          if (data.status === "pending") {
            setPendingApprenticeStep(data.step)
            // Optional: If we found a step, we can stop polling until it's resolved? No, keep checking in case backend resets
          } else {
            setPendingApprenticeStep(null)
          }
        }
      } catch (e) {}
    }

    interval = setInterval(checkPending, 2000)
    return () => clearInterval(interval)
  }, [mode, activeConvId])

  const handleApprenticeAccept = async (id: string) => {
    const res = await fetch(`/api/apprentice/step/${id}/accept`, { method: "POST" })
    const data = await res.json()
    if (data.status !== "elevation_required") {
        setPendingApprenticeStep(null)
    }
    return data
  }

  const handleApprenticeReject = async (id: string) => {
    await fetch(`/api/apprentice/step/${id}/reject`, { method: "POST" })
    setPendingApprenticeStep(null)
  }

  const handleApprenticeCorrect = async (id: string, args: any, reasoning: string) => {
    await fetch(`/api/apprentice/step/${id}/correct`, { 
      method: "POST", 
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ corrected_args: args, corrected_reasoning: reasoning })
    })
    setPendingApprenticeStep(null)
  }



  useEffect(() => {
    fetchConversations()
    fetchSources()
    const saved = localStorage.getItem("contextus_agent_accounts")
    if (saved) {
      try { setAccounts(JSON.parse(saved)) } catch { }
    } else {
      setAccounts([{
        id: `acc_${Date.now()}`,
        label: "Duck.ai Chat",
        url: "https://duck.ai/chat",
        username: "",
        password: "",
      }])
    }
  }, [])

  // Refresh conversations periodically (picks up auto-rename)
  useEffect(() => {
    const iv = setInterval(fetchConversations, 5000)
    return () => clearInterval(iv)
  }, [])

  // Load messages when active conversation changes
  useEffect(() => {
    if (activeConvId) {
      atBottomRef.current = true
      fetchMessages(activeConvId)
    } else {
      setMessages([])
    }
    setSelected(new Set())
  }, [activeConvId])

  // Load file tree when active conversation's sources change
  useEffect(() => {
    if (activeConv) {
      fetchFileTree(activeConv.sourceIds)
    } else {
      setFileTreeData([])
    }
  }, [activeConv?.sourceIds?.join(","), allSources.length])

  // Auto-scroll (только пока пользователь у нижней границы списка)
  useEffect(() => {
    const el = scrollRef.current
    if (!el || !atBottomRef.current) return
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" })
  }, [messages, streamingContent, isAgentWorking])

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  const handleAccountsChange = (newAccounts: AgentAccount[]) => {
    setAccounts(newAccounts)
    localStorage.setItem("contextus_agent_accounts", JSON.stringify(newAccounts))
  }

  // ========== Conversation CRUD ==========

  async function createConversation() {
    try {
      const res = await fetch("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "Новая беседа", sourceIds: [] }),
      })
      if (res.ok) {
        const conv = await res.json()
        setConversations((prev) => [conv, ...prev])
        setActiveConvId(conv.id)
        setConvDialogOpen(false)
      }
    } catch (e) {
      console.error("createConversation error:", e)
    }
  }

  async function deleteConversation(id: string) {
    try {
      await fetch(`/api/conversations/${id}`, { method: "DELETE" })
      setConversations((prev) => prev.filter((c) => c.id !== id))
      if (activeConvId === id) {
        setActiveConvId(null)
        setMessages([])
      }
    } catch (e) {
      console.error("deleteConversation error:", e)
    }
  }

  async function renameConversation(id: string, title: string) {
    try {
      await fetch(`/api/conversations/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      })
      setConversations((prev) =>
        prev.map((c) => (c.id === id ? { ...c, title } : c))
      )
    } catch (e) {
      console.error("renameConversation error:", e)
    }
  }

  async function updateConversationSources(sourceIds: string[]) {
    if (!activeConvId) return
    try {
      await fetch(`/api/conversations/${activeConvId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sourceIds }),
      })
      setConversations((prev) =>
        prev.map((c) => (c.id === activeConvId ? { ...c, sourceIds } : c))
      )
    } catch (e) {
      console.error("updateConversationSources error:", e)
    }
  }

  function selectConversation(id: string) {
    setActiveConvId(id)
    setConvDialogOpen(false)
  }

  // ========== Message CRUD ==========

  async function deleteMessage(msgId: string) {
    if (!activeConvId) return
    try {
      await fetch(`/api/conversations/${activeConvId}/messages/${msgId}`, { method: "DELETE" })
      setMessages((prev) => prev.filter((m) => m.id !== msgId))
    } catch (e) {
      console.error("deleteMessage error:", e)
    }
  }

  async function editMessage(msgId: string, content: string) {
    if (!activeConvId) return
    try {
      await fetch(`/api/conversations/${activeConvId}/messages/${msgId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      })
      
      const msgIndex = messages.findIndex((m) => m.id === msgId)
      if (msgIndex === -1) return

      const editedMsg = messages[msgIndex]
      const isUser = editedMsg.role === "user"

      let updatedMessages = messages.map((m) => (m.id === msgId ? { ...m, content } : m))

      if (isUser) {
        // Delete all subsequent messages in DB
        const toDelete = updatedMessages.slice(msgIndex + 1)
        for (const m of toDelete) {
          await fetch(`/api/conversations/${activeConvId}/messages/${m.id}`, { method: "DELETE" })
        }
        // Truncate local state
        updatedMessages = updatedMessages.slice(0, msgIndex + 1)
        setMessages(updatedMessages)
        // Trigger AI response
        triggerAI(updatedMessages)
      } else {
        setMessages(updatedMessages)
      }
    } catch (e) {
      console.error("editMessage error:", e)
    }
  }

  // ========== Send message ==========

  async function send() {
    const text = input.trim()
    if (!text || !activeConvId) return

    const userMsg: ChatMessage = {
      id: `u_${Date.now()}`,
      role: "user",
      content: text,
      timestamp: Date.now(),
    }

    const updatedMessages = [...messages, userMsg]
    setMessages(updatedMessages)
    setInput("")
    atBottomRef.current = true  // своё сообщение всегда показываем внизу

    let dbMsgId = userMsg.id
    // Persist to DB
    userPersistedRef.current = false
    try {
      const dbRes = await fetch(`/api/conversations/${activeConvId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: "user", content: text }),
      })
      if (dbRes.ok) {
        const dbData = await dbRes.json()
        dbMsgId = dbData.id
        userPersistedRef.current = true
        setMessages((prev) => prev.map(m => m.id === userMsg.id ? { ...m, id: dbMsgId } : m))
        updatedMessages[updatedMessages.length - 1].id = dbMsgId
      }
    } catch { }

    triggerAI(updatedMessages)
  }

  async function triggerAI(chatHistory: ChatMessage[]) {
    if (!activeConvId) return

    // Abort any previous request
    if (abortControllerRef.current) abortControllerRef.current.abort()
    const controller = new AbortController()
    abortControllerRef.current = controller

    setIsAgentWorking(false)
    setStreamingContent("")
    setAgentSteps([])
    streamingTimestampRef.current = Date.now()

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          messages: chatHistory,
          agentAllowed,
          accounts,
          debugMode,
          sourceIds: activeConv?.sourceIds || [],
          targetAgent: targetAgent,
          mode: mode,
          complexity: complexity,
          chatId: activeConvId,
        }),
      })

      if (!res.ok) throw new Error(`Server returned ${res.status}`)

      const contentType = res.headers.get("content-type") || ""

      if (contentType.includes("application/json")) {
        const data = await res.json()
        const reply: ChatMessage = {
          id: `a_${Date.now()}`,
          role: "assistant",
          content: data.content,
          timestamp: Date.now(),
        }
        setMessages((prev) => [...prev, reply])
        // Persist assistant reply
        try {
          const dbRes = await fetch(`/api/conversations/${activeConvId}/messages`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ role: "assistant", content: data.content }),
          })
          if (dbRes.ok) {
            const dbData = await dbRes.json()
            setMessages((prev) => prev.map(m => m.id === reply.id ? { ...m, id: dbData.id } : m))
          }
        } catch { }
        return
      }

      if (contentType.includes("text/event-stream")) {
        setIsAgentWorking(true)
        const reader = res.body?.getReader()
        if (!reader) throw new Error("No reader available")
        const decoder = new TextDecoder()
        let buffer = ""
        let currentContent = ""
        let streamFailed = false

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split("\n")
          buffer = lines.pop() || ""
          let currentEvent = ""

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEvent = line.slice(7).trim()
            } else if (line.startsWith("data: ")) {
              const dataStr = line.slice(6).trim()
              if (!dataStr) continue
              try {
                const parsed = JSON.parse(dataStr)
                if (currentEvent === "error" || parsed.type === "error") {
                  const errMsg = parsed.message || parsed.reason || "Неизвестная ошибка"
                  setAgentSteps(prev => [...prev, {
                    id: `s_${Date.now()}_${Math.random().toString(36).slice(2, 5)}`,
                    type: "error",
                    message: `Ошибка: ${errMsg}`,
                    timestamp: Date.now(),
                    screenshot: parsed.screenshot || undefined,
                  }])
                  streamFailed = true
                  break
                } else if (currentEvent === "step" || parsed.step || parsed.type === "step") {
                  setAgentSteps(prev => [...prev, {
                    id: `s_${Date.now()}_${Math.random().toString(36).slice(2, 5)}`,
                    type: "step",
                    step_type: parsed.step || "step",
                    message: parsed.message || `Шаг: ${parsed.step}`,
                    timestamp: Date.now(),
                    screenshot: parsed.screenshot || undefined,
                  }])
                } else if (parsed.type === "thought_chunk") {
                  setAgentSteps(prev => {
                    const newSteps = [...prev];
                    const last = newSteps[newSteps.length - 1];
                    if (last && last.step_type === "thought") {
                       last.message += parsed.content;
                    } else {
                       const generatingIdx = newSteps.findIndex(s => s.step_type === "generating");
                       if (generatingIdx !== -1) {
                           newSteps.splice(generatingIdx, 1);
                       }
                       newSteps.push({
                         id: `s_${Date.now()}_${Math.random().toString(36).slice(2, 5)}`,
                         type: "step",
                         step_type: "thought",
                         message: `🤔 ${parsed.content}`,
                         timestamp: Date.now()
                       });
                    }
                    return newSteps;
                  });
                } else if (parsed.type === "apprentice_request") {
                  // Apprentice: agent is asking for permission — fetch the pending step and show approval card
                  try {
                    const pendingRes = await fetch(`/api/apprentice/${activeConvId}/pending`)
                    if (pendingRes.ok) {
                      const pendingData = await pendingRes.json()
                      if (pendingData.status === "pending") {
                        setPendingApprenticeStep(pendingData.step)
                      }
                    }
                  } catch { }
                } else if (currentEvent === "result" || parsed.type === "result" || (parsed.content && !parsed.type)) {
                  currentContent += parsed.content
                  setStreamingContent(currentContent)
                }
              } catch {
                currentContent += dataStr
                setStreamingContent(currentContent)
              }
            }
          }

          if (streamFailed) break
        }

        setIsAgentWorking(false)
        const finalSteps = [...agentStepsRef.current]
        setAgentSteps([])
        if (currentContent || finalSteps.length > 0) {
          const contentToSave = currentContent || "*(Выполнение завершено без текстового ответа)*"
          setMessages((prev) => [...prev, {
            id: `a_${Date.now()}`,
            role: "assistant",
            content: contentToSave,
            timestamp: Date.now(),
            steps: finalSteps,
          }])
          setStreamingContent("")
          // Persist
          try {
            const dbRes = await fetch(`/api/conversations/${activeConvId}/messages`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ role: "assistant", content: contentToSave, steps: finalSteps }),
            })
            if (dbRes.ok) {
              const dbData = await dbRes.json()
              setMessages((prev) => prev.map(m => (m.content === contentToSave && m.role === "assistant") ? { ...m, id: dbData.id, steps: finalSteps } : m))
              // Сервер теперь знает и вопрос, и ответ со шагами — приводим чат к его
              // состоянию, чтобы никакие гонки/ремоунты не оставили «половину» диалога
              if (userPersistedRef.current) await fetchMessages(activeConvId)
            }
          } catch { }
        }
      }
    } catch (error: any) {
      if (error.name === "AbortError") return
      console.error("Chat error:", error)
      setIsAgentWorking(false)
      const finalSteps = [...agentStepsRef.current]
      setAgentSteps([])
      setMessages((prev) => [...prev, {
        id: `err_${Date.now()}`,
        role: "assistant",
        content: `Ошибка связи: ${error.message}`,
        timestamp: Date.now(),
        steps: finalSteps,
      }])
    }
  }

  // ========== Meta-Analyst ==========
  
  async function triggerAnalysis() {
    if (!activeConvId) return
    setIsAnalyzing(true)
    try {
      const res = await fetch(`/api/agent/analyze-session/${activeConvId}`, {
        method: "POST"
      })
      if (!res.ok) {
        const error = await res.json()
        throw new Error(error.error || "Failed to trigger analysis")
      }
      const data = await res.json()
      
      const reportContent = `**Отчет Аналитика (Meta-Analyst):**\n\n${data.report}`
      const reportMsg: ChatMessage = {
        id: `a_${Date.now()}`,
        role: "assistant",
        content: reportContent,
        timestamp: Date.now(),
      }
      
      setMessages((prev) => [...prev, reportMsg])
      
      // Persist the report to the chat
      try {
        await fetch(`/api/conversations/${activeConvId}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: "assistant", content: reportContent }),
        })
      } catch (e) {
        console.error("Failed to save analyst report to db", e)
      }
      
    } catch (e: any) {
      alert("Error: " + e.message)
    } finally {
      setIsAnalyzing(false)
    }
  }

  // ========== Render ==========

  return (
    <div className="flex min-h-0 flex-1">
      {/* CENTER: Chat */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-border px-5 md:px-8">
          <div className="flex min-w-0 items-center gap-3">
            {/* Conversation picker button → opens modal */}
            <Dialog open={convDialogOpen} onOpenChange={setConvDialogOpen}>
              <DialogTrigger
                render={
                  <Button variant="outline" size="sm" className="gap-2" />
                }
              >
                <MessagesSquare className="size-4" />
                <span className="hidden sm:inline">
                  {activeConv?.title ?? "Выбрать чат"}
                </span>
              </DialogTrigger>
              <DialogContent className="sm:max-w-md max-h-[70vh] overflow-hidden flex flex-col">
                <DialogHeader>
                  <DialogTitle>Беседы</DialogTitle>
                </DialogHeader>
                <div className="flex-1 overflow-y-auto py-2 -mx-1 px-1">
                  <ConversationList
                    conversations={conversations}
                    activeId={activeConvId}
                    onSelect={selectConversation}
                    onCreate={createConversation}
                    onDelete={deleteConversation}
                    onRename={renameConversation}
                  />
                </div>
              </DialogContent>
            </Dialog>

            {activeConv && (
              <div className="min-w-0">
                <h1 className="truncate text-lg font-semibold">
                  {activeConv.title}
                </h1>
                <p className="text-sm text-muted-foreground">
                  {activeConv.sourceIds.length} источник{activeConv.sourceIds.length !== 1 ? "ов" : ""}
                </p>
              </div>
            )}
            {activeConv && (
              <SourceSelector
                allSources={allSources}
                activeSourceIds={activeConv.sourceIds}
                onChange={updateConversationSources}
              />
            )}
          </div>
          <div className="flex items-center gap-2">
            {activeConv && (
              <Button
                variant="outline"
                size="sm"
                onClick={triggerAnalysis}
                disabled={isAnalyzing}
                title="Запросить ревизию сессии (Meta-Analyst)"
              >
                <ShieldAlert className={`size-4 ${isAnalyzing ? "animate-pulse" : "text-purple-500"}`} />
                <span className="hidden sm:inline ml-1 text-xs">Анализ</span>
              </Button>
            )}
            <Button
              variant={debugMode ? "default" : "outline"}
              size="sm"
              onClick={() => setDebugMode((d) => !d)}
              title={debugMode ? "Режим отладки: ВКЛ" : "Режим отладки: ВЫКЛ"}
            >
              <Bug className="size-4" />
              {debugMode ? <span className="ml-1 text-xs">DEBUG</span> : null}
            </Button>
            <Button variant="outline" size="sm" onClick={() => setPanelOpen((o) => !o)}>
              {panelOpen ? <PanelRightClose key="close" className="size-4" /> : <PanelRightOpen key="open" className="size-4" />}
              <span className="hidden sm:inline">Context</span>
            </Button>
          </div>
        </header>

        {!activeConvId ? (          /* key forces React to remount instead of patch */
          <div key="empty" className="flex flex-1 items-center justify-center">
            <div className="text-center">
              <MessagesSquare className="mx-auto size-12 text-muted-foreground/40 mb-4" />
              <p className="text-lg font-medium text-muted-foreground">
                Выберите беседу или создайте новую
              </p>
              <Button className="mt-4" onClick={() => setConvDialogOpen(true)}>
                <MessagesSquare className="size-4 mr-2" />
                Открыть список бесед
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div ref={scrollRef} onScroll={handleScroll} className="flex-1 overflow-y-auto px-5 py-6 md:px-8">
              <div className="mx-auto flex max-w-3xl flex-col gap-6">
                {messages.map((m) => (
                  <div key={m.id} className="flex flex-col gap-2">
                    {m.steps && m.steps.length > 0 && (
                      <AgentThinkingBubble steps={m.steps} isWorking={false} />
                    )}
                    <MessageBubble
                      message={m}
                      onDelete={deleteMessage}
                      onEdit={editMessage}
                    />
                  </div>
                ))}
                {(isAgentWorking || agentSteps.length > 0) && (
                  <AgentThinkingBubble steps={agentSteps} isWorking={isAgentWorking} />
                )}
                
                {pendingApprenticeStep && (
                  <ApprenticeCard 
                    step={pendingApprenticeStep} 
                    onAccept={handleApprenticeAccept} 
                    onReject={handleApprenticeReject} 
                    onCorrect={handleApprenticeCorrect} 
                  />
                )}

                {streamingContent && (
                  <MessageBubble
                    message={{ id: "streaming", role: "assistant", content: streamingContent, timestamp: streamingTimestampRef.current }}
                  />
                )}
              </div>
            </div>

            <div className="shrink-0 border-t border-border px-5 py-4 md:px-8">
              <div className="mx-auto max-w-3xl">
                <div className="flex items-end gap-2 rounded-2xl border border-border bg-card p-2 focus-within:border-primary/50">
                  <Textarea
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault()
                        send()
                      }
                    }}
                    placeholder="Спросите что-нибудь о подключенных источниках…"
                    rows={1}
                    className="max-h-40 min-h-9 resize-none border-0 bg-transparent px-2 py-1.5 shadow-none focus-visible:ring-0"
                  />
                                    <AgentSelector 
                    value={targetAgent} 
                    onChange={setTargetAgent} 
                    mode={mode} 
                    onModeChange={setMode} 
                  />
                  <ComplexityToggle
                    value={complexity}
                    onChange={handleComplexityChange}
                  />
                  <AgentToggle
                    enabled={agentAllowed}
                    onToggle={setAgentAllowed}
                    accounts={accounts}
                    onAccountsChange={handleAccountsChange}
                  />
                  <Button size="icon" className="size-9 shrink-0 rounded-xl" onClick={send} disabled={!input.trim() || isAgentWorking}>
                    <ArrowUp className="size-4" />
                    <span className="sr-only">Send</span>
                  </Button>
                </div>
                <p className="mt-2 px-1 text-xs text-muted-foreground">
                  {agentAllowed
                    ? "⚡ Режим внешней автоматизации активен."
                    : `${selectedCount} файлов в контексте · Enter — отправить, Shift+Enter — новая строка`
                  }
                </p>
              </div>
            </div>
          </>
        )}
      </div>

      {/* RIGHT: File tree panel */}
      <aside
        className={cn(
          "hidden shrink-0 flex-col border-l border-border bg-sidebar transition-all lg:flex",
          panelOpen ? "w-80" : "w-0 overflow-hidden border-l-0",
        )}
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-sidebar-border px-4">
          <div className="flex flex-col leading-tight">
            <span className="text-sm font-semibold">Контекст</span>
            <span className="text-xs text-muted-foreground">
              {activeConv
                ? `${activeConv.sourceIds.length} источник${activeConv.sourceIds.length !== 1 ? "ов" : ""}`
                : "Нет активной беседы"
              }
            </span>
          </div>
          <span suppressHydrationWarning className="rounded-full bg-primary/15 px-2 py-0.5 text-xs font-medium text-primary">
            {selectedCount} selected
          </span>
        </div>
        <div className="flex items-center justify-between px-4 py-2">
          <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Files
          </span>
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setSelected(new Set())}
          >
            Clear
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
          {fileTreeData.length > 0 ? (
            <FileTree tree={fileTreeData} selected={selected} onChange={setSelected} />
          ) : (
            <p className="px-2 py-4 text-xs text-muted-foreground">
              {activeConv
                ? "Привяжите источники через кнопку в шапке чата"
                : "Выберите или создайте беседу"
              }
            </p>
          )}
        </div>
      </aside>
    </div>
  )
}