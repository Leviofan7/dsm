import { NextResponse } from "next/server"

/**
 * Единая точка подключения Next.js-прокси к FastAPI-бэкенду.
 *
 * Все админские эндпоинты бэкенда защищены `Depends(require_admin)`, который читает
 * сессию из cookie `contextus_session` (или заголовка Authorization). Cookie ставится
 * на origin Next.js, поэтому прокси обязан пробрасывать её вручную — иначе бэкенд
 * видит анонимный запрос и отвечает 401.
 */
const BACKEND_URL =
  process.env.BACKEND_URL || process.env.NEXT_PUBLIC_BACKEND_URL || "http://127.0.0.1:8000"

/** Базовый URL бэкенда (единый для всех прокси-роутов). */
export function backendUrl(): string {
  return BACKEND_URL
}

/**
 * Заголовки с сессией браузера (cookie + Authorization).
 * Работает и без запроса — тогда достаточно Content-Type.
 */
export function sessionHeaders(
  req?: { headers: { get(name: string): string | null } } | null,
  options: { json?: boolean } = {},
): Record<string, string> {
  const headers: Record<string, string> = options.json ? { "Content-Type": "application/json" } : {}

  const cookie = req?.headers.get("cookie") || ""
  const authorization = req?.headers.get("authorization") || ""
  if (cookie) headers["cookie"] = cookie
  if (authorization) headers["authorization"] = authorization

  return headers
}

/**
 * Пробрасывает ответ бэкенда как есть: сохраняет статус (401/403/422 вместо 500)
 * и текст ошибки FastAPI (`detail`), чтобы UI мог показать причину пользователю.
 */
export async function relayBackendResponse(res: Response): Promise<NextResponse> {
  const text = await res.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = null
    }
  }

  if (!res.ok) {
    const rawDetail =
      (payload as any)?.detail ?? (payload as any)?.error ?? (text || `Backend error ${res.status}`)
    const message = typeof rawDetail === "string" ? rawDetail : JSON.stringify(rawDetail)
    return NextResponse.json({ error: message, detail: rawDetail }, { status: res.status })
  }

  return NextResponse.json((payload as any) ?? {})
}
