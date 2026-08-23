import { NextResponse } from "next/server"

const BACKEND_URL = "http://localhost:8000"

export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params
    const res = await fetch(`${BACKEND_URL}/conversations/${id}/messages`, {
      cache: "no-store",
    })
    if (!res.ok) throw new Error(`Backend: ${res.status}`)
    return NextResponse.json(await res.json())
  } catch (error) {
    console.error("Failed to fetch messages:", error)
    return NextResponse.json({ error: "Failed to fetch messages" }, { status: 500 })
  }
}

export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params
    const body = await req.json()
    const res = await fetch(`${BACKEND_URL}/conversations/${id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`Backend: ${res.status}`)
    return NextResponse.json(await res.json())
  } catch (error) {
    console.error("Failed to add message:", error)
    return NextResponse.json({ error: "Failed to add message" }, { status: 500 })
  }
}
