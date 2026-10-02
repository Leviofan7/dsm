import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

export const dynamic = "force-dynamic"

export async function POST(req: NextRequest) {
  try {
    // ?deep=true → бэкенд переспросит /api/show у каждой локальной модели
    const res = await fetch(`${backendUrl()}/models/refresh${req.nextUrl.search}`, {
      method: "POST",
      headers: sessionHeaders(req),
      cache: "no-store",
    })

    return await relayBackendResponse(res)
  } catch (error) {
    console.error("Failed to refresh models:", error)
    return NextResponse.json({ error: "Failed to refresh models" }, { status: 500 })
  }
}
