import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

/**
 * Прокси сценариев: чтение карточки, заполнение аргументов, активация.
 *
 * До него редактора аргументов не могло существовать: бэкенд-пути `PUT /scenarios/{id}/steps`
 * и `POST /scenarios/{id}/activate` были недостижимы из браузера, то есть сценарий
 * активировался только правкой БД руками.
 *
 * Контракт бэкенда сохраняем как есть: 422 с `report` (какие поля не заполнены) и 403 на
 * привилегированные инструменты должны доезжать до UI, а не превращаться в 500.
 * `relayBackendResponse` кладёт разобранный `detail` в поле `detail`.
 */
export async function GET(req: NextRequest, { params }: { params: Promise<{ slug?: string[] }> }) {
  return handle(req, (await params).slug, "GET")
}

export async function PUT(req: NextRequest, { params }: { params: Promise<{ slug?: string[] }> }) {
  return handle(req, (await params).slug, "PUT")
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ slug?: string[] }> }) {
  return handle(req, (await params).slug, "POST")
}

async function handle(req: NextRequest, slug: string[] = [], method: string) {
  try {
    const path = slug.map(encodeURIComponent).join("/")
    const url = `${backendUrl()}/scenarios${path ? `/${path}` : ""}`

    const headers: Record<string, string> = sessionHeaders(req)
    let body: string | undefined
    if (method !== "GET" && method !== "HEAD") {
      body = await req.text()
      if (body) headers["Content-Type"] = "application/json"
    }

    const res = await fetch(url, { method, headers, body, cache: "no-store" })
    return await relayBackendResponse(res)
  } catch (error: any) {
    console.error("Failed to proxy scenarios:", error)
    return NextResponse.json({ error: error.message || "Backend unavailable" }, { status: 500 })
  }
}
