import { NextRequest, NextResponse } from "next/server"

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000"

export async function GET(req: NextRequest) {
  try {
    const cookie = req.headers.get("cookie") || ""
    const auth = req.headers.get("authorization") || ""
    
    const headers: Record<string, string> = { "Cache-Control": "no-store" }
    if (cookie) headers["cookie"] = cookie
    if (auth) headers["authorization"] = auth

    const res = await fetch(`${BACKEND_URL}/api/users`, {
      method: "GET",
      headers,
      cache: "no-store",
    })

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `Backend: ${res.status}` }))
      return NextResponse.json(err, { status: res.status })
    }

    return NextResponse.json(await res.json())
  } catch (error: any) {
    console.error("Failed to fetch users:", error)
    return NextResponse.json({ error: "Failed to fetch users" }, { status: 500 })
  }
}
