import { afterEach, describe, expect, it, vi } from "vitest";

import { forwardChatRequest } from "../../lib/api/chat-proxy";

function chatRequest(): Request {
  return new Request("http://web.test/api/chat/stream", {
    method: "POST",
    headers: {
      Authorization: "Bearer token",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ message: "Hi" }),
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

describe("forwardChatRequest", () => {
  it("rejects requests without a bearer token", async () => {
    const response = await forwardChatRequest(
      new Request("http://web.test/api/chat/stream", { method: "POST" }),
    );

    expect(response.status).toBe(401);
  });

  it("forwards to API_BASE_URL and relays correlation and retry headers", async () => {
    vi.stubEnv("API_BASE_URL", "http://api.internal:8000");
    const fetchMock = vi.fn<typeof fetch>(
      async () =>
        new Response("event: done\ndata: {}\n\n", {
          status: 429,
          headers: {
            "Content-Type": "text/event-stream",
            "X-Correlation-ID": "corr-1",
            "Retry-After": "30",
          },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await forwardChatRequest(chatRequest());

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://api.internal:8000/chat/stream",
    );
    expect(response.status).toBe(429);
    expect(response.headers.get("x-correlation-id")).toBe("corr-1");
    expect(response.headers.get("retry-after")).toBe("30");
  });

  it("returns 502 when the API is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );

    const response = await forwardChatRequest(chatRequest());

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({
      detail: "The chat service is unavailable.",
    });
  });

  it("returns 504 when the API does not start responding in time", async () => {
    vi.stubEnv("CHAT_UPSTREAM_TIMEOUT_MS", "20");
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_input: unknown, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
          }),
      ),
    );

    const response = await forwardChatRequest(chatRequest());

    expect(response.status).toBe(504);
  });

  it("aborts a stream that goes silent for longer than the idle timeout", async () => {
    vi.stubEnv("CHAT_UPSTREAM_IDLE_TIMEOUT_MS", "20");
    let upstreamSignal: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: unknown, init?: RequestInit) => {
        upstreamSignal = init?.signal ?? undefined;
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode("event: token\n"));
            // ...and then nothing more.
          },
        });
        return new Response(body, {
          headers: { "Content-Type": "text/event-stream" },
        });
      }),
    );

    const response = await forwardChatRequest(chatRequest());
    const reader = response.body!.getReader();
    await reader.read();

    await expect(reader.read()).rejects.toThrow("did not respond in time");
    expect(upstreamSignal?.aborted).toBe(true);
  });
});
