import { defineConfig, devices } from "@playwright/test";

// Run through the e2e-test-suite-ui-playwright skill (scripts/run_ui.py): it
// starts the app against an isolated test data dir with the mock
// model, and passes BASE_URL / UI_RESULTS_DIR / UI_DATA_DIR in the env.
const resultsDir = process.env.UI_RESULTS_DIR ?? "test-results";

export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  expect: {
    timeout: 20_000,
    // threshold = per-pixel colour tolerance. Playwright's default (0.2) let a
    // pale-yellow -> pale-blue box (MOE app) pass as unchanged; 0.05 catches it
    // and stayed stable across repeated runs on this machine.
    toHaveScreenshot: { threshold: 0.05, maxDiffPixelRatio: 0.005, animations: "disabled", caret: "hide" },
  },
  fullyParallel: true,
  forbidOnly: true, // a committed test.only would silently skip the rest of the suite
  retries: 1, // a test that only passes on retry is reported as flaky, not passed
  reporter: [
    ["list"],
    ["html", { outputFolder: `${resultsDir}/html`, open: "never" }],
    ["json", { outputFile: `${resultsDir}/results.json` }],
    ["junit", { outputFile: `${resultsDir}/junit.xml` }],
  ],
  outputDir: `${resultsDir}/artifacts`,
  // Approved visual baselines live next to the specs and are committed; the
  // platform suffix keeps Windows/Linux renderings apart.
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFileName}/{arg}-{projectName}-{platform}{ext}",
  use: {
    baseURL: process.env.BASE_URL,
    viewport: { width: 1400, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1400, height: 1000 } } },
    { name: "firefox", use: { ...devices["Desktop Firefox"], viewport: { width: 1400, height: 1000 } } },
    { name: "webkit", use: { ...devices["Desktop Safari"], viewport: { width: 1400, height: 1000 } } },
  ],
});
