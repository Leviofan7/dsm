import { NextRequest, NextResponse } from "next/server"

const WORKER_SECRET = "default_secret"

export async function POST(req: NextRequest) {
  try {
    const { messages, agentAllowed, accounts, debugMode, sourceIds, targetAgent, mode, chatId } = await req.json()
    const lastMessage = messages[messages.length - 1]
    
    // We pass all previous messages as history so the agent remembers context
    const history = messages.slice(0, -1).map((m: any) => ({
      role: m.role,
      content: m.content
    }))

    // All requests now go through the FastAPI backend orchestrator!
    // The orchestrator will decide whether to use the browser or answer directly
    // based on its task classification LLM.
    const res = await fetch("http://127.0.0.1:8000/agent/run", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${WORKER_SECRET}`,
      },
      body: JSON.stringify({
        query: lastMessage.content,
        history: history,
        accounts: accounts || [],
        allow_browser: agentAllowed, // The backend can use this to strictly disable browser if the user toggled it off
        debug_mode: debugMode || false,
        source_ids: sourceIds || [],
        target_agent: targetAgent || "auto",
        mode: mode || "auto",
        chat_id: chatId || "",
      }),
    })

    if (!res.ok) {
      return NextResponse.json(
        { error: `Backend returned ${res.status}` },
        { status: res.status },
      )
    }

    const data = await res.json()
    const taskId = data.task_id

    // Now connect to the SSE stream for this task
    const streamRes = await fetch(`http://127.0.0.1:8000/agent/task/${taskId}/stream`, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${WORKER_SECRET}`,
      }
    })

    if (!streamRes.ok) {
      return NextResponse.json(
        { error: `Stream returned ${streamRes.status}` },
        { status: streamRes.status },
      )
    }

    // Stream the response back to the client via SSE
    return new Response(streamRes.body, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    })
  } catch (error: any) {
    console.error("API Chat error:", error)
    return NextResponse.json(
      {
        content: `Ошибка связи с бекендом: ${error.message}. Убедитесь, что FastAPI сервер запущен.`,
      },
      { status: 200 },
    )
  }
}
