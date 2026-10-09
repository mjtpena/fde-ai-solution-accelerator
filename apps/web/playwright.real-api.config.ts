import { defineConfig, devices } from "@playwright/test";

/**
 * The web app against a real API process (no mock). Driven by
 * tests/e2e_local/test_web_against_api.py, which starts PostgreSQL-backed
 * `uvicorn` with a local signing authority and passes its URL and signed
 * tokens in E2E_* variables. Not part of `npm run test:e2e`.
 */
const apiBaseUrl = process.env.E2E_API_BASE_URL;
const tenantId = process.env.E2E_TENANT_ID;
const port = process.env.E2E_WEB_PORT ?? "3200";
if (!apiBaseUrl || !tenantId) {
  throw new Error(
    "E2E_API_BASE_URL and E2E_TENANT_ID are required; run `make e2e-local`.",
  );
}

export default defineConfig({
  testDir: "./tests/e2e-real-api",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : {},
  },
  webServer: [
    {
      command: `npm run dev -- --hostname 127.0.0.1 --port ${port}`,
      url: `http://127.0.0.1:${port}`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        API_BASE_URL: apiBaseUrl,
        NEXT_PUBLIC_ENTRA_CLIENT_ID: "playwright-client-id",
        NEXT_PUBLIC_ENTRA_TENANT_ID: tenantId,
        NEXT_PUBLIC_ENTRA_API_SCOPE: "api://playwright-client-id/access_as_user",
      },
    },
  ],
});
