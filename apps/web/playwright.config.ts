import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    // Lets environments with a preinstalled Chromium skip `playwright install`.
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : {},
  },
  webServer: [
    {
      command: "node tests/e2e/mock-api.mjs",
      url: "http://127.0.0.1:8100/healthz",
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      command: "npm run dev -- --hostname 127.0.0.1 --port 3100",
      url: "http://127.0.0.1:3100",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        API_BASE_URL: "http://127.0.0.1:8100",
        NEXT_PUBLIC_ENTRA_CLIENT_ID: "playwright-client-id",
        NEXT_PUBLIC_ENTRA_TENANT_ID: "00000000-0000-0000-0000-000000000001",
        NEXT_PUBLIC_ENTRA_API_SCOPE:
          "api://playwright-client-id/access_as_user",
      },
    },
  ],
});
