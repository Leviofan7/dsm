import { NextResponse } from "next/server"

const BACKEND_URL = "http://localhost:8000"

export async function POST(req: Request) {
  try {
    const body = await req.json()
    
    const res = await fetch(`${BACKEND_URL}/sources/local`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}))
      throw new Error(errorData.detail || `Backend responded with status: ${res.status}`)
    }
    
    const data = await res.json()
    return NextResponse.json(data)
  } catch (error) {
    console.error("Failed to add local source:", error)
    return NextResponse.json({ error: error instanceof Error ? error.message : "Failed to add source" }, { status: 500 })
  }
}
