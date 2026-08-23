import { NextResponse } from "next/server"

const BACKEND_URL = "http://localhost:8000"

export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params
    const res = await fetch(`${BACKEND_URL}/sources/${id}/tree`, {
      cache: "no-store",
    })
    if (!res.ok) throw new Error(`Backend: ${res.status}`)
    return NextResponse.json(await res.json())
  } catch (error) {
    console.error("Failed to fetch file tree:", error)
    return NextResponse.json({ error: "Failed to fetch file tree" }, { status: 500 })
  }
}
