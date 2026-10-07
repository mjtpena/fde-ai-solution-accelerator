import { expect, test } from "@playwright/test";

test("streams an answer with clickable citations and an approval card", async ({
  page,
}) => {
  await page.route("**/api/chat/stream", async (route) => {
    await route.continue({
      headers: {
        ...route.request().headers(),
        authorization: "Bearer e2e-test-token",
      },
    });
  });

  await page.goto("/");
  await page.getByLabel("Message").fill("What does the guide say?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByTestId("answer-text")).toHaveText("Streaming", {
    timeout: 1_000,
  });
  await expect(page.getByTestId("answer-text")).toHaveText("Streaming answer.");
  const citation = page.getByRole("link", { name: "Guide (chunk-1)" });
  await expect(citation).toHaveAttribute("href", "https://docs.example/guide");
  await expect(
    page.getByRole("region", { name: "Approval required" }),
  ).toContainText("write_record");
});

test("rejects a chat request without a bearer token", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Message").fill("What does the guide say?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(
    page.getByText("Chat request failed with status 401.", { exact: true }),
  ).toHaveText("Chat request failed with status 401.");
});
