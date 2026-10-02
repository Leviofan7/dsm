import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; msgId: string }> }
) {
  try {
    const { id, msgId } = await params
    const body = await req.json()
    const res = await fetch(`${backendUrl()}/conversations/${id}/messages/${msgId}`, {
      method: "PUT",
      headers: sessionHeaders(req, { json: true }),
      body: JSON.stringify(body),
    })
    return await relayBackendResponse(res)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; msgId: string }> }
) {
  try {
    const { id, msgId } = await params
    const res = await fetch(`${backendUrl()}/conversations/${id}/messages/${msgId}`, {
      method: "DELETE",
      headers: sessionHeaders(req),
    })
    return await relayBackendResponse(res)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}
