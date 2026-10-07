import { BrowserCacheLocation } from "@azure/msal-browser";
import { describe, expect, it } from "vitest";

import { createMsalConfiguration } from "../../lib/auth/msal";

describe("createMsalConfiguration", () => {
  it("uses the configured Entra tenant and session-scoped token cache", () => {
    const configuration = createMsalConfiguration("web-client", "tenant-id");

    expect(configuration.auth?.clientId).toBe("web-client");
    expect(configuration.auth?.authority).toBe(
      "https://login.microsoftonline.com/tenant-id",
    );
    expect(configuration.cache?.cacheLocation).toBe(
      BrowserCacheLocation.SessionStorage,
    );
  });
});
