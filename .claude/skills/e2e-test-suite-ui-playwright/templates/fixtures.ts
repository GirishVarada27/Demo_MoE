import { test as base, expect, Locator, Page } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

/**
 * Page object for the app under test. Put every locator and multi-step
 * action here so specs read like user journeys and a UI change is fixed in
 * one place. Prefer getByRole/getByLabel/getByTestId; never generated CSS
 * classes.
 */
export class AppPage {
  readonly mainHeading: Locator;

  constructor(readonly page: Page) {
    this.mainHeading = page.getByRole("heading", { level: 1 }); // CHANGE ME
  }

  async open(): Promise<void> {
    await this.page.goto("/");
    await expect(this.mainHeading).toBeVisible();
    await this.idle();
  }

  /** Wait until the app has settled after an action (see references/writing-tests.md). */
  async idle(): Promise<void> {
    // e.g. Streamlit: await expect(this.page.getByTestId("stStatusWidget")).toHaveCount(0);
    // e.g. a SPA spinner: await expect(this.page.getByRole("progressbar")).toHaveCount(0);
  }
}

/** The isolated test data dir the app was started with (set by run_ui.py for file:// stores). */
export function testDataFile(name: string): string {
  const dir = process.env.UI_DATA_DIR;
  if (!dir) throw new Error("UI_DATA_DIR isn't set: run the suite through run_ui.py");
  return path.join(dir, name);
}

export function readJsonList(file: string): any[] {
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf-8")) : [];
}

export const test = base.extend<{ app: AppPage }>({
  app: async ({ page }, use) => {
    const app = new AppPage(page);
    await app.open();
    await use(app);
  },
});

export { expect };
