import { test as base, expect, Locator, Page } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

/**
 * Page object for the MOE Streamlit UI. Selectors use accessible roles/names
 * first and Streamlit's data-testids second -- never generated CSS classes,
 * which change between Streamlit releases.
 */
export class MoeApp {
  readonly sidebar: Locator;
  readonly applicantPicker: Locator;
  readonly chatInput: Locator;
  readonly userMessages: Locator;
  readonly assistantMessages: Locator;
  readonly traceSteps: Locator;
  readonly failureToggle: Locator;
  private readonly stepLabel = /^Step \d+: \w+$/;

  constructor(readonly page: Page) {
    this.sidebar = page.getByTestId("stSidebar");
    this.applicantPicker = page.getByRole("combobox", { name: "Pick an applicant" });
    this.chatInput = page.getByRole("textbox", { name: "Type a message..." });
    this.userMessages = page.getByRole("group", { name: "Chat message from user" });
    this.assistantMessages = page.getByRole("group", { name: "Chat message from assistant" });
    this.failureToggle = page.getByRole("switch", { name: "Inject tool failures" });
    // An expander's text starts with its icon's ligature name, so match the
    // step label element inside it rather than the expander's whole text.
    this.traceSteps = this.traceColumn().getByTestId("stExpander").filter({ has: page.getByText(this.stepLabel) });
  }

  private traceColumn(): Locator {
    return this.page.getByTestId("stColumn").nth(1);
  }

  traceStep(title: string): Locator {
    return this.traceSteps.filter({ has: this.page.getByText(title, { exact: true }) });
  }

  /** Streamlit hides the real switch input under its label; click the label. */
  async setFailureInjection(on: boolean): Promise<void> {
    if ((await this.failureToggle.isChecked()) !== on) {
      await this.sidebar.getByTestId("stCheckbox").getByText("Inject tool failures").click();
    }
    await (on ? expect(this.failureToggle).toBeChecked() : expect(this.failureToggle).not.toBeChecked());
    await this.idle();
  }

  async open(): Promise<void> {
    await this.page.goto("/");
    await expect(this.page.getByRole("heading", { name: "Synthetic Applicant" })).toBeVisible();
    await this.idle();
  }

  /** Streamlit shows a running indicator while a script rerun is in progress. */
  async idle(): Promise<void> {
    await expect(this.page.getByTestId("stStatusWidget")).toHaveCount(0, { timeout: 30_000 });
    await expect(this.page.getByTestId("stSpinner")).toHaveCount(0, { timeout: 30_000 });
  }

  async pickApplicant(applicantId: string): Promise<void> {
    await this.applicantPicker.click();
    await this.page.getByRole("option", { name: new RegExp(`^${applicantId} `) }).click();
    await expect(this.applicantPicker).toHaveValue(new RegExp(`^${applicantId} `));
    await this.idle();
  }

  /** Sidebar "Load applicant into chat"; waits for the assistant's reply. */
  async loadApplicant(applicantId: string): Promise<Locator> {
    await this.pickApplicant(applicantId);
    return this.expectReply(() => this.page.getByRole("button", { name: "Load applicant into chat" }).click());
  }

  async send(message: string): Promise<Locator> {
    return this.expectReply(async () => {
      await this.chatInput.fill(message);
      await this.chatInput.press("Enter");
    });
  }

  private async expectReply(action: () => Promise<void>): Promise<Locator> {
    const before = await this.assistantMessages.count();
    await action();
    await expect(this.assistantMessages).toHaveCount(before + 1);
    await this.idle();
    return this.assistantMessages.last();
  }

  section(reply: Locator, name: "Verified" | "Assumed" | "Simulated"): Locator {
    const kind = { Verified: "Success", Assumed: "Warning", Simulated: "Info" }[name];
    return reply.getByTestId(`stAlertContent${kind}`);
  }

  /** Trace step titles, newest first (the panel lists them in reverse). */
  async traceTitles(): Promise<string[]> {
    return (await this.traceColumn().getByText(this.stepLabel).allInnerTexts()).map((t) => t.trim());
  }

  caseStateValue(label: "Applicant" | "Curriculum"): Locator {
    return this.page.getByTestId("stColumn").nth(1).getByText(new RegExp(`^${label}:`)).locator("xpath=..");
  }
}

/** The isolated test data dir the app was started with (set by run_ui.py). */
export function testDataFile(name: string): string {
  const dir = process.env.UI_DATA_DIR;
  if (!dir) throw new Error("UI_DATA_DIR isn't set: run the suite through run_ui.py");
  return path.join(dir, name);
}

export function readJsonList(file: string): any[] {
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf-8")) : [];
}

export const test = base.extend<{ app: MoeApp }>({
  app: async ({ page }, use) => {
    const app = new MoeApp(page);
    await app.open();
    await use(app);
  },
});

export { expect };
