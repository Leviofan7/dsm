import { NextResponse } from "next/server"

const BACKEND_URL = "http://localhost:8000"

export async function GET() {
  try {
    const res = await fetch(`${BACKEND_URL}/conversations`, { cache: "no-store" })
    if (!res.ok) throw new Error(`Backend: ${res.status}`)
    return NextResponse.json(await res.json())
  } catch (error) {
    console.error("Failed to fetch conversations:", error)
    return NextResponse.json({ error: "Failed to fetch conversations" }, { status: 500 })
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.json()
    const res = await fetch(`${BACKEND_URL}/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`Backend: ${res.status}`)
    return NextResponse.json(await res.json())
  } catch (error) {
    console.error("Failed to create conversation:", error)
    return NextResponse.json({ error: "Failed to create conversation" }, { status: 500 })
  }
}
