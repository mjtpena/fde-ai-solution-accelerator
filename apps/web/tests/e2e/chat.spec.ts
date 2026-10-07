import { expect, test } from "@playwright/test";

test("requires sign-in before enabling chat", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Message").fill("What does the guide say?");

  await expect(
    page.getByText("Sign in to send a chat message.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toBeDisabled();
});
