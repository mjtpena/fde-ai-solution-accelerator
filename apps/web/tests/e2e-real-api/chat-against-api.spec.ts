import { expect, test, type Page } from "@playwright/test";

/**
 * The real web app, through its own route handlers, against a real API process.
 * Sign-in is the only shortcut: the MSAL cache is seeded with an access token,
 * but that token is a real RS256 JWT the API verifies against the signing
 * authority's JWKS. Without Azure AI Search and Foundry the API must refuse to
 * answer, and the UI must show that rather than any text.
 */
const clientId = "playwright-client-id";
const apiScope = "api://playwright-client-id/access_as_user";
const environment = "login.microsoftonline.com";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required; run \`make e2e-local\`.`);
  return value;
}

async function signInWithCachedToken(page: Page, accessToken: string) {
  const tenantId = required("E2E_TENANT_ID");
  const payload = JSON.parse(
    Buffer.from(accessToken.split(".")[1], "base64url").toString("utf8"),
  ) as { exp: number };
  const homeAccountId = `e2e-user.${tenantId}`;
  const accountKey =
    `msal.3|${homeAccountId}|${environment}|${tenantId}`.toLowerCase();
  const accessTokenKey = [
    "msal.3",
    homeAccountId,
    environment,
    "accesstoken",
    clientId,
    tenantId,
    apiScope,
    "",
    "",
  ]
    .join("|")
    .toLowerCase();

  await page.addInitScript(
    ({ accountKey, accessTokenKey, homeAccountId, tenantId, token, expiresOn }) => {
      const now = Math.floor(Date.now() / 1000);
      sessionStorage.setItem(
        accountKey,
        JSON.stringify({
          homeAccountId,
          environment: "login.microsoftonline.com",
          localAccountId: "e2e-user",
          username: "e2e@example.com",
          name: "End To End User",
          authorityType: "MSSTS",
          realm: tenantId,
          lastUpdatedAt: String(Date.now()),
        }),
      );
      sessionStorage.setItem("msal.3.account.keys", JSON.stringify([accountKey]));
      sessionStorage.setItem(
        accessTokenKey,
        JSON.stringify({
          homeAccountId,
          environment: "login.microsoftonline.com",
          credentialType: "AccessToken",
          clientId: "playwright-client-id",
          secret: token,
          realm: tenantId,
          target: "api://playwright-client-id/access_as_user",
          cachedAt: String(now),
          expiresOn: String(expiresOn),
          extendedExpiresOn: String(expiresOn),
          tokenType: "Bearer",
          lastUpdatedAt: String(Date.now()),
        }),
      );
      sessionStorage.setItem(
        "msal.3.token.keys.playwright-client-id",
        JSON.stringify({
          idToken: [],
          accessToken: [accessTokenKey],
          refreshToken: [],
        }),
      );
    },
    {
      accountKey,
      accessTokenKey,
      homeAccountId,
      tenantId,
      token: accessToken,
      expiresOn: payload.exp,
    },
  );
}

// Next.js renders its own (empty) route-announcer alert; match the chat's alert.
function chatAlert(page: Page) {
  return page.getByRole("alert").filter({ hasText: "Chat request failed" });
}

async function send(page: Page, message: string) {
  await page.getByLabel("Message").fill(message);
  await page.getByRole("button", { name: "Send" }).click();
}

test("the real API is live, refuses to answer without Azure, then rate limits", async ({
  page,
}) => {
  await signInWithCachedToken(page, required("E2E_ACCESS_TOKEN"));
  await page.goto("/");
  await expect(page.getByText("Signed in as End To End User")).toBeVisible();
  // /api/health is the web app's route handler calling the API's /healthz.
  await expect(page.getByText("API status: ok")).toBeVisible();

  await send(page, "What is the retention period for decision records?");
  await expect(chatAlert(page)).toHaveText(
    "Chat request failed with status 503.",
  );
  await expect(page.getByTestId("answer-text")).toHaveCount(0);

  // The API under test allows one model-backed request per window.
  await send(page, "And for working notes?");
  await expect(chatAlert(page)).toHaveText(
    "Chat request failed with status 429.",
  );
  await expect(page.getByTestId("answer-text")).toHaveCount(0);
});

test("a token the API cannot verify is refused", async ({ page }) => {
  await signInWithCachedToken(page, required("E2E_FORGED_TOKEN"));
  await page.goto("/");
  await expect(page.getByText("Signed in as End To End User")).toBeVisible();

  await send(page, "Hello");
  await expect(chatAlert(page)).toHaveText(
    "Chat request failed with status 401.",
  );
});
