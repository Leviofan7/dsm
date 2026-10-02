import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

/** История прогонов беседы для вкладки «Артефакты». */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  try {
    const limit = req.nextUrl.searchParams.get("limit") ?? "50"
    const res = await fetch(`${backendUrl()}/conversations/${id}/runs?limit=${limit}`, {
      method: "GET",
      headers: sessionHeaders(req),
      cache: "no-store",
    })
    return await relayBackendResponse(res)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}
