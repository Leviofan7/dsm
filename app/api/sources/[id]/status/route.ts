import { NextResponse } from "next/server"

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000"

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params
    const res = await fetch(`${BACKEND_URL}/sources/${id}/status`, {
      cache: "no-store",
    })

    if (!res.ok) {
      return NextResponse.json({ error: "Not found" }, { status: res.status })
    }
    return NextResponse.json(await res.json())
  } catch (error) {
    console.error("Failed to get source status:", error)
    return NextResponse.json({ error: "Failed to get status" }, { status: 500 })
  }
}
