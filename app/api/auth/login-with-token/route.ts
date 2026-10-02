import { NextRequest, NextResponse } from "next/server"

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000"

export async function POST(req: NextRequest) {
  try {
    const body = await req.json()
    const res = await fetch(`${BACKEND_URL}/api/auth/login-with-token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })

    const data = await res.json()
    if (!res.ok) {
      return NextResponse.json(data, { status: res.status })
    }

    const response = NextResponse.json(data)
    // Forward Set-Cookie header if provided by backend
    const setCookie = res.headers.get("set-cookie")
    if (setCookie) {
      response.headers.set("set-cookie", setCookie)
    } else if (data.token) {
      response.cookies.set("contextus_session", data.token, {
        httpOnly: true,
        sameSite: "lax",
        path: "/",
        maxAge: 7 * 86400,
      })
    }

    return response
  } catch (error: any) {
    console.error("Failed to login with token:", error)
    return NextResponse.json({ error: "Failed to login with token" }, { status: 500 })
  }
}
