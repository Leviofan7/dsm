import { NextResponse } from "next/server"

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000"

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params
    const res = await fetch(`${BACKEND_URL}/sources/${id}/reindex`, {
      method: "POST",
    })

    const data = await res.json()
    if (!res.ok) {
      return NextResponse.json(data, { status: res.status })
    }
    return NextResponse.json(data)
  } catch (error) {
    console.error("Failed to reindex source:", error)
    return NextResponse.json({ error: "Failed to reindex source" }, { status: 500 })
  }
}
