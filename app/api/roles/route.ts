import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

export async function GET(req: NextRequest) {
  try {
    const res = await fetch(`${backendUrl()}/roles`, {
      method: "GET",
      headers: sessionHeaders(req),
      cache: "no-store",
    })

    return await relayBackendResponse(res)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json()
    const { id, ...roleData } = body

    if (!id) {
      return NextResponse.json({ error: "Role ID is required" }, { status: 400 })
    }

    const res = await fetch(`${backendUrl()}/roles/${id}`, {
      method: "POST",
      headers: sessionHeaders(req, { json: true }),
      body: JSON.stringify(roleData),
    })

    return await relayBackendResponse(res)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}
