import { NextResponse } from "next/server"

const BACKEND_URL = "http://localhost:8000"

export async function PUT(
  req: Request,
  { params }: { params: Promise<{ id: string; msgId: string }> }
) {
  try {
    const { id, msgId } = await params
    const body = await req.json()
    const res = await fetch(`${BACKEND_URL}/conversations/${id}/messages/${msgId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`Backend: ${res.status}`)
    return NextResponse.json(await res.json())
  } catch (error) {
    console.error("Failed to update message:", error)
    return NextResponse.json({ error: "Failed to update message" }, { status: 500 })
  }
}

export async function DELETE(
  req: Request,
  { params }: { params: Promise<{ id: string; msgId: string }> }
) {
  try {
    const { id, msgId } = await params
    const res = await fetch(`${BACKEND_URL}/conversations/${id}/messages/${msgId}`, {
      method: "DELETE",
    })
    if (!res.ok) throw new Error(`Backend: ${res.status}`)
    return NextResponse.json(await res.json())
  } catch (error) {
    console.error("Failed to delete message:", error)
    return NextResponse.json({ error: "Failed to delete message" }, { status: 500 })
  }
}
