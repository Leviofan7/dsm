import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params
    const res = await fetch(`${backendUrl()}/sources/${id}/reindex`, {
      method: "POST",
      headers: sessionHeaders(req),
    })
    return await relayBackendResponse(res)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}
