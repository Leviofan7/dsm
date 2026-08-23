import { NextResponse } from "next/server"

const BACKEND_URL = "http://localhost:8000"

export async function GET() {
  try {
    const res = await fetch(`${BACKEND_URL}/agents/list`, {
      cache: "no-store",
    })
    if (!res.ok) throw new Error(`Backend: ${res.status}`)
    return NextResponse.json(await res.json())
  } catch (error) {
    console.error("Failed to fetch agents:", error)
    return NextResponse.json({ error: "Failed to fetch agents" }, { status: 500 })
  }
}
