import { NextResponse } from "next/server"

const BACKEND_URL = "http://localhost:8000"

export async function DELETE(
  req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params
    const res = await fetch(`${BACKEND_URL}/sources/${id}`, {
      method: "DELETE",
    })
    
    if (!res.ok) {
      throw new Error(`Backend responded with status: ${res.status}`)
    }
    
    const data = await res.json()
    return NextResponse.json(data)
  } catch (error) {
    console.error("Failed to delete source:", error)
    return NextResponse.json({ error: "Failed to delete source" }, { status: 500 })
  }
}
