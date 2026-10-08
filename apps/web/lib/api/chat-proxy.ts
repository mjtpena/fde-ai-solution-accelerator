export async function forwardChatRequest(request: Request): Promise<Response> {
  const apiBaseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";
  const authorization = request.headers.get("authorization");
  if (!authorization || !/^Bearer\s+\S+$/i.test(authorization)) {
    return Response.json(
      { detail: "A bearer token is required." },
      { status: 401 },
    );
  }

  const headers = new Headers({
    Accept: request.headers.get("accept") ?? "text/event-stream",
    "Content-Type": request.headers.get("content-type") ?? "application/json",
    Authorization: authorization,
  });

  const correlationId = request.headers.get("x-correlation-id");
  if (correlationId) headers.set("X-Correlation-ID", correlationId);

  const upstream = await fetch(new URL("/chat/stream", apiBaseUrl), {
    method: "POST",
    headers,
    body: await request.arrayBuffer(),
    cache: "no-store",
    signal: request.signal,
  });

  const responseHeaders = new Headers({
    "Content-Type":
      upstream.headers.get("content-type") ?? "application/octet-stream",
    "Cache-Control": upstream.headers.get("cache-control") ?? "no-cache",
  });
  const upstreamCorrelationId = upstream.headers.get("x-correlation-id");
  if (upstreamCorrelationId) {
    responseHeaders.set("X-Correlation-ID", upstreamCorrelationId);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}
