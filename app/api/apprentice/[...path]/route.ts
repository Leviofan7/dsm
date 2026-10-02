import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

export async function GET(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  try {
    const { path } = await params
    const endpoint = path.join("/")
    const res = await fetch(`${backendUrl()}/apprentice/${endpoint}`, {
      headers: sessionHeaders(req),
      cache: "no-store",
    })
    return await relayBackendResponse(res)
  } catch (error) {
    console.error("Failed GET apprentice:", error)
    return NextResponse.json({ error: "Failed" }, { status: 500 })
  }
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  try {
    const { path } = await params
    const endpoint = path.join("/")

    let body = {}
    if (req.headers.get("content-type")?.includes("application/json")) {
        body = await req.json()
    }

    // POST-эндпоинты apprentice защищены require_admin — нужна сессионная cookie
    const res = await fetch(`${backendUrl()}/apprentice/${endpoint}`, {
      method: "POST",
      headers: sessionHeaders(req, { json: true }),
      body: JSON.stringify(body),
    })
    return await relayBackendResponse(res)
  } catch (error) {
    console.error("Failed POST apprentice:", error)
    return NextResponse.json({ error: "Failed" }, { status: 500 })
  }
}
