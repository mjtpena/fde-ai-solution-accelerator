import { describe, expect, it } from "vitest";
import { getHealth } from "../../lib/api/client";

describe("getHealth", () => {
  it("calls /healthz and returns the typed health status", async () => {
    let requestUrl: URL | undefined;
    let requestInit: RequestInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input instanceof URL ? input : new URL(input.toString());
      requestInit = init;
      return new Response(JSON.stringify({ status: "ok" }), {
        headers: { "Content-Type": "application/json" },
      });
    };

    await expect(getHealth("test-access-token", fetcher)).resolves.toEqual({
      status: "ok",
    });
    expect(requestUrl?.pathname).toBe("/healthz");
    expect(requestInit?.cache).toBe("no-store");
    expect(new Headers(requestInit?.headers).get("Authorization")).toBe(
      "Bearer test-access-token",
    );
  });

  it("reports non-success API responses", async () => {
    const fetcher: typeof fetch = async () => new Response(null, { status: 503 });

    await expect(getHealth("test-access-token", fetcher)).rejects.toThrow(
      "API health check failed with status 503.",
    );
  });

  it("rejects responses outside the OpenAPI health schema", async () => {
    const fetcher: typeof fetch = async () =>
      new Response(JSON.stringify({ status: "unknown" }), {
        headers: { "Content-Type": "application/json" },
      });

    await expect(getHealth("test-access-token", fetcher)).rejects.toThrow(
      "API health check returned an invalid response.",
    );
  });
});
