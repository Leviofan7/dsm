import { NextRequest, NextResponse } from "next/server"

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000"

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params
    const cookie = req.headers.get("cookie") || ""
    const auth = req.headers.get("authorization") || ""
    
    const headers: Record<string, string> = { "Content-Type": "application/json" }
    if (cookie) headers["cookie"] = cookie
    if (auth) headers["authorization"] = auth

    const res = await fetch(`${BACKEND_URL}/api/users/${id}/link-telegram`, {
      method: "POST",
      headers,
    })

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `Backend: ${res.status}` }))
      return NextResponse.json(err, { status: res.status })
    }

    return NextResponse.json(await res.json())
  } catch (error: any) {
    console.error("Failed to generate link token:", error)
    return NextResponse.json({ error: "Failed to generate link token" }, { status: 500 })
  }
}
