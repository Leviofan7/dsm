import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

export async function POST(req: NextRequest) {
  try {
    const body = await req.json()

    const res = await fetch(`${backendUrl()}/sources/github`, {
      method: "POST",
      headers: sessionHeaders(req, { json: true }),
      body: JSON.stringify(body),
    })
    return await relayBackendResponse(res)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}
