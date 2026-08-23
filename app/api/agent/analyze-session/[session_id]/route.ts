import { NextRequest, NextResponse } from "next/server"

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000"
const WORKER_SECRET = "default_secret"

export async function POST(req: NextRequest, { params }: { params: Promise<{ session_id: string }> }) {
  try {
    const { session_id } = await params;
    const res = await fetch(`${BACKEND_URL}/agent/analyze-session/${session_id}`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${WORKER_SECRET}`
      }
    })

    if (!res.ok) {
      const err = await res.text()
      return NextResponse.json({ error: err }, { status: res.status })
    }

    return NextResponse.json(await res.json())
  } catch (error) {
    console.error("Failed to proxy analyze session:", error)
    return NextResponse.json({ error: "Failed to communicate with backend" }, { status: 500 })
  }
}
