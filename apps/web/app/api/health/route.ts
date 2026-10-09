import {
  apiBaseUrl,
  fetchWithHeadersTimeout,
  upstreamHeadersTimeoutMs,
} from "@/lib/api/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Same-origin API liveness check for the browser, which never calls the API directly. */
export async function GET(request: Request): Promise<Response> {
  try {
    const { response } = await fetchWithHeadersTimeout(
      new URL("/healthz", apiBaseUrl()),
      { headers: { Accept: "application/json" }, cache: "no-store" },
      upstreamHeadersTimeoutMs(),
      request.signal,
    );
    return new Response(await response.text(), {
      status: response.status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return Response.json(
      { detail: "The API is unreachable." },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}
