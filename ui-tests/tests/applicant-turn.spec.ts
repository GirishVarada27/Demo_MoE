import { test, expect } from "./fixtures";

test.describe("loading an applicant into chat", { tag: ["@smoke", "@regression"] }, () => {
  test("runs the agent and renders a sectioned answer, trace and case state", async ({ app, page }) => {
    const reply = await app.loadApplicant("APP-001");

    await expect(app.userMessages.last()).toHaveText(/My applicant ID is APP-001\./);
    await expect(app.section(reply, "Verified")).toContainText("Verified");
    await expect(app.section(reply, "Assumed")).toContainText("American (US High School Diploma)");
    await expect(app.section(reply, "Simulated")).toContainText("Daniel Thompson");
    await expect(reply.getByText("Next step", { exact: true })).toBeVisible();

    // The agent chose these tools itself; the panel lists them newest first.
    expect(await app.traceTitles()).toEqual([
      "Step 4: evaluate_eligibility",
      "Step 3: search_requirements",
      "Step 2: check_documents",
      "Step 1: get_applicant_record",
    ]);
    await expect(app.caseStateValue("Applicant")).toHaveText("Applicant: APP-001");
    await expect(app.caseStateValue("Curriculum")).toHaveText("Curriculum: American (US High School Diploma)");
    await expect(page.getByRole("listitem").filter({ hasText: /^Eligibility undeterminable for rule\(s\): curr-us-001/ })).toBeVisible();
  });

  test("trace steps expand to show the tool's input and output", async ({ app }) => {
    await app.loadApplicant("APP-002");
    const lookup = app.traceStep("Step 1: get_applicant_record");
    await lookup.getByText("Step 1: get_applicant_record").click();
    await expect(lookup.getByTestId("stJson")).toContainText("APP-002");
    await expect(lookup.getByText(/found APP-002/)).toBeVisible();
  });

  test("the raw response holds the full answer text", async ({ app }) => {
    const reply = await app.loadApplicant("APP-007");
    const raw = reply.getByTestId("stExpander").filter({ hasText: "Raw response" });
    await raw.getByText("Raw response").click();
    await expect(raw.getByTestId("stText")).toContainText("Next step:");
    await expect(raw.getByTestId("stText")).toContainText("Brandon Michaels");
  });
});

test.describe("missing documents", { tag: "@regression" }, () => {
  test("asks for the missing Emirates ID instead of escalating", async ({ app }) => {
    const reply = await app.loadApplicant("APP-003");
    await expect(reply).toContainText("The missing Emirates ID copy blocks your application");
    await expect(reply).toContainText("please upload a copy of your Emirates ID");
    expect(await app.traceTitles()).not.toContainEqual(expect.stringContaining("escalate_to_human"));
  });
});
