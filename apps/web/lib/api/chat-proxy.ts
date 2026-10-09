import {
  apiBaseUrl,
  fetchWithHeadersTimeout,
  upstreamHeadersTimeoutMs,
  upstreamIdleTimeoutMs,
  UpstreamTimeoutError,
  withIdleTimeout,
} from "./upstream";

function problem(status: number, detail: string): Response {
  return Response.json(
    { detail },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

export async function forwardChatRequest(request: Request): Promise<Response> {
  const authorization = request.headers.get("authorization");
  if (!authorization || !/^Bearer\s+\S+$/i.test(authorization)) {
    return problem(401, "A bearer token is required.");
  }

  const headers = new Headers({
    Accept: request.headers.get("accept") ?? "text/event-stream",
    "Content-Type": request.headers.get("content-type") ?? "application/json",
    Authorization: authorization,
  });

  const correlationId = request.headers.get("x-correlation-id");
  if (correlationId) headers.set("X-Correlation-ID", correlationId);

  let upstream: Response;
  let controller: AbortController;
  try {
    ({ response: upstream, controller } = await fetchWithHeadersTimeout(
      new URL("/chat/stream", apiBaseUrl()),
      {
        method: "POST",
        headers,
        body: await request.arrayBuffer(),
        cache: "no-store",
      },
      upstreamHeadersTimeoutMs(),
      request.signal,
    ));
  } catch (error) {
    if (error instanceof UpstreamTimeoutError) {
      return problem(504, "The chat service did not respond in time.");
    }
    if (request.signal.aborted) {
      return problem(499, "The client closed the request.");
    }
    return problem(502, "The chat service is unavailable.");
  }

  const responseHeaders = new Headers({
    "Content-Type":
      upstream.headers.get("content-type") ?? "application/octet-stream",
    "Cache-Control": upstream.headers.get("cache-control") ?? "no-cache",
  });
  for (const name of ["x-correlation-id", "retry-after"]) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }

  const body = upstream.body
    ? withIdleTimeout(upstream.body, upstreamIdleTimeoutMs(), controller)
    : null;
  return new Response(body, { status: upstream.status, headers: responseHeaders });
}
