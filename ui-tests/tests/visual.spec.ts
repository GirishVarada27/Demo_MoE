import { test, expect } from "./fixtures";

// Visual regression is opt-in: only the screens below are compared against
// approved baselines in tests/__screenshots__/. Re-record them only after a
// reviewed, intended UI change (run_ui.py --update-snapshots --grep @visual).
test.describe("visual baselines", { tag: "@visual" }, () => {
  test("sidebar", async ({ app, page }) => {
    await app.pickApplicant("APP-001");
    // The picker keeps its focus ring until focus moves elsewhere; screenshot the resting state.
    await page.getByRole("heading", { name: "Synthetic Applicant" }).click();
    await expect(app.sidebar).toHaveScreenshot("sidebar.png");
  });

  test("answer card for a complete file", async ({ app }) => {
    const reply = await app.loadApplicant("APP-001");
    await expect(reply).toHaveScreenshot("answer-app-001.png");
  });
});
