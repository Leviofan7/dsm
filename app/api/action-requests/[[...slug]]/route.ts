import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

/**
 * Прокси единой очереди подтверждений (ActionRequest).
 *
 * Раньше его не было: карточка в чате звала /api/action-requests/{id}/approve, а такого
 * роута в Next не существовало → кнопки Approve/Reject молча получали 404, то есть
 * ручное подтверждение из веб-UI не работало вообще (работал только Telegram).
 */
export async function GET(req: NextRequest, { params }: { params: { slug?: string[] } }) {
  return handleRequest(req, params.slug, "GET")
}

export async function POST(req: NextRequest, { params }: { params: { slug?: string[] } }) {
  return handleRequest(req, params.slug, "POST")
}

async function handleRequest(req: NextRequest, slug: string[] = [], method: string) {
  try {
    const path = slug.join("/")
    let url = `${backendUrl()}/api/action-requests`
    if (path) url += `/${path}`
    const qs = req.nextUrl.searchParams.toString()
    if (qs) url += `?${qs}`

    // Эндпоинты очереди защищены require_admin — нужна сессионная cookie пользователя
    const headers: Record<string, string> = sessionHeaders(req)

    let body = undefined
    if (method !== "GET" && method !== "HEAD") {
      body = await req.text()
      if (body) headers["Content-Type"] = "application/json"
    }

    const res = await fetch(url, { method, headers, body: body || undefined, cache: "no-store" })
    return await relayBackendResponse(res)
  } catch (error) {
    console.error("Failed to proxy action-requests:", error)
    return NextResponse.json({ error: "Failed to communicate with backend" }, { status: 500 })
  }
}
