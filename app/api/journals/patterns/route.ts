import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

/**
 * Сводка по последним журналам прогонов — минимальный самоанализ.
 * Только агрегаты по уже записанным журналам: доля успеха, длительность, частые ошибки.
 */
export async function GET(req: NextRequest) {
  try {
    const limit = req.nextUrl.searchParams.get("limit") ?? "100"
    const res = await fetch(`${backendUrl()}/journals/patterns?limit=${limit}`, {
      method: "GET",
      headers: sessionHeaders(req),
      cache: "no-store",
    })
    return await relayBackendResponse(res)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}
