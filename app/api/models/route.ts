import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

// Список моделей меняется при каждом `ollama pull`/`ollama rm` — кэш недопустим.
export const dynamic = "force-dynamic"

export async function GET(req: NextRequest) {
  try {
    const res = await fetch(`${backendUrl()}/models`, {
      headers: sessionHeaders(req),
      cache: "no-store",
    })
    return await relayBackendResponse(res)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}
