import { expect, test } from "@playwright/test";

test("streams an authenticated answer with citations and an approval card", async ({
  page,
}) => {
  const clientId = "playwright-client-id";
  const tenantId = "00000000-0000-0000-0000-000000000001";
  const homeAccountId = `playwright-user.${tenantId}`;
  const environment = "login.microsoftonline.com";
  const apiScope = "api://playwright-client-id/access_as_user";
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
    ({
      accountKey,
      accessTokenKey,
      clientId,
      environment,
      homeAccountId,
      tenantId,
      apiScope,
    }) => {
      const cachedAt = Math.floor(Date.now() / 1000);
      const account = {
        homeAccountId,
        environment,
        localAccountId: "playwright-user",
        username: "playwright@example.com",
        name: "Playwright User",
        authorityType: "MSSTS",
        realm: tenantId,
        lastUpdatedAt: String(Date.now()),
      };
      const accessToken = {
        homeAccountId,
        environment,
        credentialType: "AccessToken",
        clientId,
        secret: "playwright-test-access-token",
        realm: tenantId,
        target: apiScope,
        cachedAt: String(cachedAt),
        expiresOn: String(cachedAt + 3600),
        extendedExpiresOn: String(cachedAt + 3600),
        tokenType: "Bearer",
        lastUpdatedAt: String(Date.now()),
      };

      sessionStorage.setItem(accountKey, JSON.stringify(account));
      sessionStorage.setItem(
        "msal.3.account.keys",
        JSON.stringify([accountKey]),
      );
      sessionStorage.setItem(accessTokenKey, JSON.stringify(accessToken));
      sessionStorage.setItem(
        `msal.3.token.keys.${clientId}`,
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
      clientId,
      environment,
      homeAccountId,
      tenantId,
      apiScope,
    },
  );

  await page.goto("/");
  await expect(page.getByText("Signed in as Playwright User")).toBeVisible();
  await expect(page.getByText("API status: ok")).toBeVisible();
  await page.getByLabel("Message").fill("What does the guide say?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByTestId("answer-text")).toHaveText("Streaming answer.");
  await expect(
    page.getByRole("link", { name: "Guide (chunk-1)" }),
  ).toHaveAttribute("href", "https://docs.example/guide");
  await expect(
    page.getByRole("region", { name: "Approval required" }),
  ).toContainText("write_record");
});

test("requires sign-in before enabling chat", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Message").fill("What does the guide say?");

  await expect(
    page.getByText("Sign in to send a chat message.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toBeDisabled();
});
