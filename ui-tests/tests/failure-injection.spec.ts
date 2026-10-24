import { test, expect, readJsonList, testDataFile } from "./fixtures";

test.describe("tool failure injection", { tag: "@regression" }, () => {
  test("retries a failing tool once, then escalates, and writes the ticket to the test data dir", async ({ app, page }) => {
    const queue = testDataFile("escalation_queue.json");

    await app.setFailureInjection(true);
    await app.loadApplicant("APP-005");

    const titles = await app.traceTitles();
    // Oldest steps are last: the lookup, its retry, then the forced escalation.
    expect(titles.slice(-3)).toEqual([
      "Step 3: escalate_to_human",
      "Step 2: get_applicant_record",
      "Step 1: get_applicant_record",
    ]);
    const retry = app.traceStep("Step 2: get_applicant_record");
    await retry.getByText("Step 2: get_applicant_record").click();
    await expect(retry.getByText(/Simulated .* failure.*\(retry\)/)).toBeVisible();

    // The ticket landed in the isolated test data dir (run_ui.py separately
    // checks that the real data/ folder was not touched).
    const tickets = readJsonList(queue);
    expect(tickets.some((t) => /Tool 'get_applicant_record' failed twice/.test(t.reason))).toBe(true);
  });
});
