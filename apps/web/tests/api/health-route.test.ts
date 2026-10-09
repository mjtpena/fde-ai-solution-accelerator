import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "../../app/api/health/route";

function healthRequest(): Request {
  return new Request("http://web.test/api/health");
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("GET /api/health", () => {
  it("relays the API's /healthz status and body without caching", async () => {
    vi.stubEnv("API_BASE_URL", "http://api.internal:8000");
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response('{"status":"degraded"}', { status: 503 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(healthRequest());

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://api.internal:8000/healthz",
    );
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ status: "degraded" });
    expect(response.headers.get("content-type")).toBe("application/json");
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("returns 502 when the API does not respond in time", async () => {
    vi.stubEnv("CHAT_UPSTREAM_TIMEOUT_MS", "20");
    let upstreamSignal: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_input: unknown, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            upstreamSignal = init?.signal ?? undefined;
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
          }),
      ),
    );

    const response = await GET(healthRequest());

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({
      detail: "The API is unreachable.",
    });
    expect(upstreamSignal?.aborted).toBe(true);
  });

  it("returns 502 when the API is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );

    const response = await GET(healthRequest());

    expect(response.status).toBe(502);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({
      detail: "The API is unreachable.",
    });
  });
});
