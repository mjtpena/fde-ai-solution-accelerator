/**
 * Server-side calls from the Next.js route handlers to the API.
 *
 * The browser never calls the API origin directly (see the CSP); these
 * handlers do, at `API_BASE_URL`, with explicit timeouts.
 */
export function apiBaseUrl(): string {
  return process.env.API_BASE_URL ?? "http://localhost:8000";
}

function milliseconds(name: string, fallback: number): number {
  const value = Number(process.env[name]);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

/** Time allowed for the API to start responding (headers). */
export function upstreamHeadersTimeoutMs(): number {
  return milliseconds("CHAT_UPSTREAM_TIMEOUT_MS", 15_000);
}

/** Longest silence allowed between streamed chunks once a response has started. */
export function upstreamIdleTimeoutMs(): number {
  return milliseconds("CHAT_UPSTREAM_IDLE_TIMEOUT_MS", 60_000);
}

export class UpstreamTimeoutError extends Error {
  constructor() {
    super("The API did not respond in time.");
    this.name = "UpstreamTimeoutError";
  }
}

/**
 * `fetch` that aborts if response headers do not arrive within `timeoutMs`,
 * or when `signal` (the caller's request) aborts.
 */
export async function fetchWithHeadersTimeout(
  url: URL,
  init: RequestInit,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<{ response: Response; controller: AbortController }> {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener("abort", forwardAbort, { once: true });
  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    return { response, controller };
  } catch (error) {
    if (timedOut) throw new UpstreamTimeoutError();
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/** Pass a body through, aborting the upstream when it goes silent too long. */
export function withIdleTimeout(
  body: ReadableStream<Uint8Array>,
  idleMs: number,
  controller: AbortController,
): ReadableStream<Uint8Array> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const reset = (target: TransformStreamDefaultController<Uint8Array>) => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      controller.abort();
      target.error(new UpstreamTimeoutError());
    }, idleMs);
  };
  return body.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      start(target) {
        reset(target);
      },
      transform(chunk, target) {
        reset(target);
        target.enqueue(chunk);
      },
      flush() {
        clearTimeout(timer);
      },
    }),
  );
}
