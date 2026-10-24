# Writing regression tests that stay green until something breaks

## Shape of a good spec

- One user journey per `test()`, named for what the user can do ("asks for the missing Emirates ID instead of escalating"), and tagged with `@smoke` (fast, critical path), `@regression` (full journeys) or `@visual`.
- Assert **behaviour the user sees**: text, state, counts, order, enabled/disabled, navigation, and the side effects that matter (a record written to the *test* data store). Avoid asserting incidental markup.
- Build page objects in `tests/fixtures.ts` (a `test.extend` fixture), so a UI change means fixing one locator, not twenty specs.
- Each test gets a fresh browser context, so never depend on another test's state. Create what you need inside the test.
- Data side effects: read the isolated data dir through `UI_DATA_DIR` (set by run_ui.py). Tests may run in parallel, so assert "a ticket with this reason exists", not "exactly N tickets".

## Locators, in order of preference

1. `getByRole(role, { name })`: what assistive tech and users see. Survives restyling.
2. `getByLabel`, `getByPlaceholder`, `getByText` (scoped to a region, `exact` where possible).
3. `getByTestId(...)`: the app's `data-testid`s (Streamlit ships `stSidebar`, `stChatMessage`, `stAlertContentSuccess`, `stExpander` and more). If the app has none where you need one, propose adding one.
4. Never generated CSS classes (`st-emotion-cache-...`, CSS-module hashes), nth-child chains, or XPath into layout.

Playwright is strict: a locator matching two elements fails. That's a feature. Scope it (`region.getBy...`) or `.filter({ has / hasText })` instead of reaching for `.first()`.

## Waiting

Never `waitForTimeout`. Use web-first assertions (`await expect(locator).toHaveText(...)`), which retry until the timeout. For apps that re-render asynchronously, add an `idle()` helper to the page object:

| Framework | "Settled" signal |
|---|---|
| Streamlit | `stStatusWidget` and `stSpinner` test ids gone; for a chat turn, the assistant message count went up |
| React / Next.js | the element the action produces is visible; `page.waitForResponse(url)` for the API call it triggers |
| Angular | the element is visible; avoid `networkidle` with long-polling apps |
| Any SPA | `await expect(page).toHaveURL(...)` after navigation |

Framework quirks seen in practice:
- Streamlit expander and toggle labels start with an icon-font ligature (e.g. `keyboard_arrow_right`), so `hasText: /^Step/` won't match. Match the label element inside instead.
- Streamlit hides the real switch input under its label: click the label, then assert `toBeChecked()`.
- The same text often appears twice (e.g. in a JSON viewer). Scope by role (`getByRole("listitem")`) rather than using `.first()`.

## Deterministic answers from LLM-backed apps

Point the app at `scripts/mock_llm_server.py` (via the config's `llm.mock.env`) with a script in the app repo. The default `tool_steps` / `final_answer` covers the main journey, and `scenarios` (matched by regex on the user message) give specific journeys their own answers. Assert on the exact mock text plus the app's own rendering of it. Real-model behaviour belongs in a separate, explicitly approved check, not the regression suite.

## Proving a test works

A test that never fails protects nothing. For every new spec:
1. `run_ui.py --grep "<title>" --repeat-each 3 --retries 0`: must pass 3/3.
2. Temporarily break the behaviour it covers in the app (after confirming the file is clean in git), run it, and confirm it fails with a clear message. Then `git checkout -- <file>`.
