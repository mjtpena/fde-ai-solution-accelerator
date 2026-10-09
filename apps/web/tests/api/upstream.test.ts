import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchWithHeadersTimeout,
  withIdleTimeout,
} from "../../lib/api/upstream";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function silentAfterOneChunk(): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode("event: token\n"));
    },
  });
}

describe("fetchWithHeadersTimeout", () => {
  it("never starts a live upstream call for an already-aborted request", async () => {
    let upstreamSignal: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: unknown, init?: RequestInit) => {
        upstreamSignal = init?.signal ?? undefined;
        if (upstreamSignal?.aborted) {
          throw new DOMException("Aborted", "AbortError");
        }
        return new Response("{}");
      }),
    );
    const request = new AbortController();
    request.abort();

    await expect(
      fetchWithHeadersTimeout(
        new URL("http://api.test/healthz"),
        {},
        1_000,
        request.signal,
      ),
    ).rejects.toThrow("Aborted");
    expect(upstreamSignal?.aborted).toBe(true);
  });
});

describe("withIdleTimeout", () => {
  it("clears the idle timer and aborts the upstream when the reader cancels", async () => {
    vi.useFakeTimers();
    const upstream = new AbortController();
    const reader = withIdleTimeout(
      silentAfterOneChunk(),
      60_000,
      upstream,
    ).getReader();
    await reader.read();

    await reader.cancel();

    expect(upstream.signal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("clears the idle timer and aborts the upstream when the source errors", async () => {
    vi.useFakeTimers();
    const upstream = new AbortController();
    const reader = withIdleTimeout(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.error(new Error("connection reset"));
        },
      }),
      60_000,
      upstream,
    ).getReader();

    await expect(reader.read()).rejects.toThrow("connection reset");
    expect(upstream.signal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("clears the idle timer when the source closes normally", async () => {
    vi.useFakeTimers();
    const upstream = new AbortController();
    const reader = withIdleTimeout(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.close();
        },
      }),
      60_000,
      upstream,
    ).getReader();

    expect((await reader.read()).done).toBe(true);
    expect(upstream.signal.aborted).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });
});
