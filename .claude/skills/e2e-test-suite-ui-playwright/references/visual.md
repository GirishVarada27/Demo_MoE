# Visual regression (opt-in)

Use `toHaveScreenshot` only for screens where *appearance* is the thing to
protect (an answer card's colour-coded sections, a dashboard layout), and
tag those tests `@visual`.

- **Screenshot a component, not the page**: `await expect(locator).toHaveScreenshot("name.png")`. Smaller areas mean fewer unrelated diffs.
- **Mask what legitimately changes** (timestamps, ids, avatars, ads): `toHaveScreenshot("x.png", { mask: [page.getByTestId("timestamp")] })`.
- **Settle first**: wait for content, close menus, and disable animations (the config sets `animations: "disabled"`, `caret: "hide"`).
- **Tolerances** (in `playwright.config.ts`): `threshold` is the per-pixel colour distance (0-1), and `maxDiffPixelRatio` is the share of pixels allowed to differ. Playwright's default `threshold` of 0.2 is too loose for pale UI colours: on the MOE app it let a pale-yellow section box turn pale blue unnoticed. Start at `threshold: 0.05`, `maxDiffPixelRatio: 0.005`. Then **verify both directions**: a deliberate style change must fail, and `--repeat-each 5 --retries 0` on unchanged code must pass.
- **Baselines are per browser and per OS** (`{arg}-{projectName}-{platform}.png`). Fonts render differently on Windows, Linux and macOS, so record baselines on the OS where the gate runs, and commit them.

## Updating baselines

Only after an intended UI change, and with the user's approval:
1. Run the visual tests normally. On failure, Playwright saves `*-expected.png`, `*-actual.png` and `*-diff.png` under the run's `artifacts/`; show them to the user.
2. Once approved: `run_ui.py --db-url <URL> --grep @visual --update-snapshots`.
3. Commit the changed PNGs in the same change as the UI code, so reviewers see both.
