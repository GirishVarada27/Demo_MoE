import { test, expect } from "./fixtures";

test.describe("app shell", { tag: "@smoke" }, () => {
  test("loads with sidebar, chat and an empty trace", async ({ app, page }) => {
    await expect(page.getByRole("heading", { name: "Chat" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Agent Trace" })).toBeVisible();
    await expect(page.getByText("No tool calls yet this session.")).toBeVisible();
    await expect(app.chatInput).toBeVisible();
    await expect(app.caseStateValue("Applicant")).toHaveText("Applicant: none yet");
    await expect(app.failureToggle).not.toBeChecked();
  });

  test("lists all 10 synthetic applicants and shows the selected one's details", async ({ app, page }) => {
    await app.applicantPicker.click();
    const options = page.getByRole("option");
    await expect(options).toHaveCount(10);
    await expect(options.first()).toHaveText(/^APP-001 — Daniel Thompson/);
    await page.keyboard.press("Escape");

    await app.pickApplicant("APP-010");
    await expect(app.sidebar.getByText("Curriculum on file: Indian (CBSE)")).toBeVisible();
  });
});
