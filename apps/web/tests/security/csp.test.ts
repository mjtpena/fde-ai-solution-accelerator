import { describe, expect, it } from "vitest";

import {
  buildContentSecurityPolicy,
  createNonce,
} from "../../lib/security/csp";

describe("buildContentSecurityPolicy", () => {
  const policy = buildContentSecurityPolicy({
    nonce: "abc123",
    authorityOrigin: "https://login.microsoftonline.com",
    development: false,
  });

  it("allows scripts only with the request nonce", () => {
    expect(policy).toContain("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
    expect(policy).not.toContain("unsafe-inline");
    expect(policy).not.toContain("unsafe-eval");
  });

  it("limits connections to this origin and the Entra authority", () => {
    expect(policy).toContain(
      "connect-src 'self' https://login.microsoftonline.com",
    );
    expect(policy).toContain("frame-src https://login.microsoftonline.com");
    expect(policy).toContain("frame-ancestors 'none'");
    expect(policy).toContain("object-src 'none'");
  });

  it("permits eval only in development", () => {
    const development = buildContentSecurityPolicy({
      nonce: "n",
      authorityOrigin: "https://login.microsoftonline.us",
      development: true,
    });
    expect(development).toContain("'unsafe-eval'");
    expect(development).not.toContain("upgrade-insecure-requests");
  });
});

describe("createNonce", () => {
  it("returns a fresh base64 value each time", () => {
    const first = createNonce();
    expect(first).toMatch(/^[A-Za-z0-9+/]+=*$/);
    expect(createNonce()).not.toBe(first);
  });
});
