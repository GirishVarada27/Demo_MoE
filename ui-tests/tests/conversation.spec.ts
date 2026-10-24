import { test, expect } from "./fixtures";

test.describe("typed conversation", { tag: "@regression" }, () => {
  test("a typed message runs the same flow as the sidebar button", async ({ app }) => {
    const reply = await app.send("My applicant ID is APP-010. Can you check my certificate equivalency?");
    await expect(app.section(reply, "Assumed")).toContainText("Indian (CBSE)");
    await expect(app.section(reply, "Simulated")).toContainText("Ananya Sharma");
    await expect(app.caseStateValue("Applicant")).toHaveText("Applicant: APP-010");
  });

  test("a follow-up turn keeps the earlier context instead of starting over", async ({ app }) => {
    await app.loadApplicant("APP-008");
    const stepsAfterFirst = (await app.traceTitles()).length;

    const second = await app.send("hello again");
    await expect(second).toContainText("Tell me your applicant ID");
    await expect(app.userMessages).toHaveCount(2);
    await expect(app.assistantMessages).toHaveCount(2);
    // The greeting needs no tools: the trace and case state from turn 1 stay put.
    expect((await app.traceTitles()).length).toBe(stepsAfterFirst);
    await expect(app.caseStateValue("Applicant")).toHaveText("Applicant: APP-008");
  });

  test("Reset conversation clears chat, trace and case state", async ({ app, page }) => {
    await app.loadApplicant("APP-009");
    await page.getByRole("button", { name: "Reset conversation" }).click();
    await app.idle();
    await expect(app.userMessages).toHaveCount(0);
    await expect(app.assistantMessages).toHaveCount(0);
    await expect(page.getByText("No tool calls yet this session.")).toBeVisible();
    await expect(app.caseStateValue("Applicant")).toHaveText("Applicant: none yet");
  });
});
