import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = req.nextUrl
    const params = new URLSearchParams()
    if (searchParams.get("from_date")) params.set("from_date", searchParams.get("from_date")!)
    if (searchParams.get("to_date")) params.set("to_date", searchParams.get("to_date")!)

    const url = `${backendUrl()}/analytics/metrics${params.toString() ? "?" + params.toString() : ""}`
    const res = await fetch(url, { headers: sessionHeaders(req), cache: "no-store" })
    return await relayBackendResponse(res)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}
