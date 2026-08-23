import { NextRequest, NextResponse } from "next/server"

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000"
const WORKER_SECRET = "default_secret" // In reality, this should be an env var

export async function GET(req: NextRequest, { params }: { params: { slug?: string[] } }) {
  return handleRequest(req, params.slug, "GET")
}

export async function POST(req: NextRequest, { params }: { params: { slug?: string[] } }) {
  return handleRequest(req, params.slug, "POST")
}

async function handleRequest(req: NextRequest, slug: string[] = [], method: string) {
  try {
    const { searchParams } = req.nextUrl
    const path = slug.join("/")
    
    let url = `${BACKEND_URL}/privileged-actions`
    if (path) {
      url += `/${path}`
    }
    if (searchParams.toString()) {
      url += `?${searchParams.toString()}`
    }

    const headers: Record<string, string> = {
      "Authorization": `Bearer ${WORKER_SECRET}`
    }
    
    let body = undefined
    if (method !== "GET" && method !== "HEAD") {
      try {
        body = await req.text()
        if (body) headers["Content-Type"] = "application/json"
      } catch (e) {
        // empty body
      }
    }

    const res = await fetch(url, { 
      method,
      headers,
      body: body || undefined,
      cache: "no-store" 
    })

    if (!res.ok) {
      let errText = await res.text()
      try {
        errText = JSON.parse(errText).detail || errText
      } catch {}
      return NextResponse.json({ error: errText || `Backend responded with status: ${res.status}` }, { status: res.status })
    }

    const data = await res.json()
    return NextResponse.json(data)
  } catch (error) {
    console.error("Failed to proxy privileged actions:", error)
    return NextResponse.json({ error: "Failed to communicate with backend" }, { status: 500 })
  }
}
