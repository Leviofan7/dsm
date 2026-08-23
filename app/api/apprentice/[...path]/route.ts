import { NextRequest, NextResponse } from "next/server"

const BACKEND_URL = "http://localhost:8000"

export async function GET(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  try {
    const { path } = await params
    const endpoint = path.join("/")
    const res = await fetch(`${BACKEND_URL}/apprentice/${endpoint}`, {
      cache: "no-store",
    })
    if (!res.ok) throw new Error(`Backend: ${res.status}`)
    return NextResponse.json(await res.json())
  } catch (error) {
    console.error("Failed GET apprentice:", error)
    return NextResponse.json({ error: "Failed" }, { status: 500 })
  }
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  try {
    const { path } = await params
    const endpoint = path.join("/")
    
    let body = {}
    if (req.headers.get("content-type")?.includes("application/json")) {
        body = await req.json()
    }

    const res = await fetch(`${BACKEND_URL}/apprentice/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`Backend: ${res.status}`)
    return NextResponse.json(await res.json())
  } catch (error) {
    console.error("Failed POST apprentice:", error)
    return NextResponse.json({ error: "Failed" }, { status: 500 })
  }
}
