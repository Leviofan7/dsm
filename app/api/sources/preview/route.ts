import { NextResponse } from "next/server"

const BACKEND_URL = "http://localhost:8000"

export async function GET(req: Request) {
  try {
    const { searchParams } = new URL(req.url)
    const path = searchParams.get("path")
    
    if (!path) {
      return NextResponse.json({ error: "Path parameter is required" }, { status: 400 })
    }

    const res = await fetch(`${BACKEND_URL}/sources/preview?path=${encodeURIComponent(path)}`, {
      cache: "no-store", 
    })
    
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}))
      throw new Error(errorData.detail || `Backend responded with status: ${res.status}`)
    }
    
    const data = await res.json()
    return NextResponse.json(data)
  } catch (error) {
    console.error("Failed to fetch folder preview:", error)
    return NextResponse.json({ error: error instanceof Error ? error.message : "Failed to fetch folder preview" }, { status: 500 })
  }
}
