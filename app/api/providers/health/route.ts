import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

/**
 * Прокси здоровья провайдеров моделей.
 *
 * `?probe=true` пробрасываем как есть: он включает ЖИВУЮ проверку (Ollama / баланс
 * DeepSeek) — это единственный способ увидеть «кончились деньги», а не только «ключ задан».
 * Эндпоинт защищён require_admin, поэтому cookie сессии обязана уехать на бэкенд.
 */
export async function GET(req: NextRequest) {
  try {
    const qs = req.nextUrl.searchParams.toString()
    const res = await fetch(`${backendUrl()}/providers/health${qs ? `?${qs}` : ""}`, {
      headers: sessionHeaders(req),
      cache: "no-store",
    })
    return await relayBackendResponse(res)
  } catch (error: any) {
    console.error("Failed to proxy providers health:", error)
    return NextResponse.json({ error: error.message || "Backend unavailable" }, { status: 500 })
  }
}
