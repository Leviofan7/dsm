import { NextResponse } from "next/server"

const BACKEND_URL = "http://localhost:8000"

export async function GET() {
  try {
    const res = await fetch(`${BACKEND_URL}/sources`, {
      // Don't cache so we always get the latest indexing status
      cache: "no-store", 
    })
    
    if (!res.ok) {
      throw new Error(`Backend responded with status: ${res.status}`)
    }
    
    const data = await res.json()
    return NextResponse.json(data)
  } catch (error) {
    console.error("Failed to fetch sources from backend:", error)
    return NextResponse.json({ error: "Failed to fetch sources" }, { status: 500 })
  }
}
