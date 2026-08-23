import { NextRequest, NextResponse } from "next/server"

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000"

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = req.nextUrl
    const params = new URLSearchParams()
    if (searchParams.get("from_date")) params.set("from_date", searchParams.get("from_date")!)
    if (searchParams.get("to_date")) params.set("to_date", searchParams.get("to_date")!)

    const url = `${BACKEND_URL}/analytics/metrics${params.toString() ? "?" + params.toString() : ""}`
    const res = await fetch(url, { cache: "no-store" })

    if (!res.ok) throw new Error(`Backend responded with status: ${res.status}`)

    const data = await res.json()
    return NextResponse.json(data)
  } catch (error) {
    console.error("Failed to fetch analytics metrics:", error)
    return NextResponse.json({ error: "Failed to fetch analytics" }, { status: 500 })
  }
}
