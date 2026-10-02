import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

export async function GET(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  try {
    // Next.js 15: params is a Promise
    const { id } = await context.params

    const res = await fetch(`${backendUrl()}/roles/${id}`, {
      method: "GET",
      headers: sessionHeaders(req),
      cache: "no-store",
    })

    return await relayBackendResponse(res)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}

export async function PUT(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await context.params
    const body = await req.json()

    const res = await fetch(`${backendUrl()}/roles/${id}`, {
      method: "POST", // The backend uses POST for create_or_update_role
      headers: sessionHeaders(req, { json: true }),
      body: JSON.stringify(body),
    })

    return await relayBackendResponse(res)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}

export async function DELETE(req: NextRequest, context: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await context.params

    const res = await fetch(`${backendUrl()}/roles/${id}`, {
      method: "DELETE",
      headers: sessionHeaders(req),
    })

    return await relayBackendResponse(res)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}
