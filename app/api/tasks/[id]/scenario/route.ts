import { NextRequest, NextResponse } from "next/server"
import { backendUrl, relayBackendResponse, sessionHeaders } from "@/lib/backend-proxy"

/**
 * Предложение сценария по удачному прогону (черновик).
 *
 * Бэкенд намеренно отвечает 400 с причиной, когда повторять нечего (прогон упал или
 * не вызывал инструментов) — прокси обязан донести этот текст, а не превратить в 500.
 */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  try {
    const res = await fetch(`${backendUrl()}/tasks/${id}/scenario`, {
      method: "POST",
      headers: sessionHeaders(req, { json: true }),
    })
    return await relayBackendResponse(res)
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 })
  }
}
