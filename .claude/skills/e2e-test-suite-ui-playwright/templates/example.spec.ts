import { test, expect } from "./fixtures";

// One user journey per test, named for what the user can do. Tag @smoke for
// the critical path, @regression for full journeys, @visual for screenshots.
test.describe("app shell", { tag: "@smoke" }, () => {
  test("loads the main page", async ({ app, page }) => {
    await expect(app.mainHeading).toBeVisible();
    await expect(page).toHaveTitle(/CHANGE ME/);
  });
});
