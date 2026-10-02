import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

export async function POST(req: NextRequest, { params }: { params: Promise<{ session_id: string }> }) {
  try {
    const { session_id } = await params;
    const res = await fetch(`${backendUrl()}/agent/analyze-session/${session_id}`, {
      method: "POST",
      headers: sessionHeaders(req),
    })

    return await relayBackendResponse(res)
  } catch (error) {
    console.error("Failed to proxy analyze session:", error)
    return NextResponse.json({ error: "Failed to communicate with backend" }, { status: 500 })
  }
}
