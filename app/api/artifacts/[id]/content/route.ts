import { NextRequest, NextResponse } from "next/server"
import { backendUrl, sessionHeaders } from "@/lib/backend-proxy"

/**
 * Содержимое артефакта — БАЙТ-В-БАЙТ, без разбора JSON.
 *
 * `relayBackendResponse` здесь не годится: он читает тело как текст и оборачивает в JSON,
 * из-за чего ломались бы и картинки, и скачивание файлов. Заголовки пробрасываем руками,
 * а редирект на внешний источник возвращаем как редирект (иначе fetch сходит за него сам
 * и браузер получит чужой HTML вместо адреса).
 */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  try {
    const res = await fetch(`${backendUrl()}/artifacts/${id}/content`, {
      method: "GET",
      headers: sessionHeaders(req),
      redirect: "manual",
      cache: "no-store",
    })

    if (res.status >= 300 && res.status < 400) {
      const location = res.headers.get("location")
      if (location) return NextResponse.redirect(new URL(location, req.url), 302)
      return NextResponse.json({ error: "Бэкенд вернул редирект без адреса" }, { status: 502 })
    }

    if (!res.ok) {
      // Ошибку (401/400/404) отдаём как JSON — панель должна показать причину
      const text = await res.text()
      let detail: unknown = text
      try {
        detail = JSON.parse(text)
      } catch {
        /* не JSON — оставляем текстом */
      }
      return NextResponse.json({ error: detail }, { status: res.status })
    }

    const headers = new Headers()
    for (const name of ["content-type", "content-disposition", "content-length", "etag"]) {
      const value = res.headers.get(name)
      if (value) headers.set(name, value)
    }
    return new NextResponse(res.body, { status: 200, headers })
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}
