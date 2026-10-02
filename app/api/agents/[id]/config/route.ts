import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params

  try {
    const body = await req.json()
    const res = await fetch(`${backendUrl()}/agents/${id}/config`, {
      method: "POST",
      headers: sessionHeaders(req, { json: true }),
      body: JSON.stringify(body),
    })

    // Пробрасываем статус и detail бэкенда (401/403/422), а не маскируем их под 500
    return await relayBackendResponse(res)
  } catch (error) {
    console.error(`Failed to update config for ${id}:`, error)
    return NextResponse.json({ error: "Failed to update config" }, { status: 500 })
  }
}
