# Triaging a failed UI run

Start with `summary.md`: **newly failing** tests are the likeliest regressions
from the current change. For each failure, open its trace:

```
npx playwright show-trace "<run>/artifacts/<test>/trace.zip"
```

The trace shows every action and assertion with DOM snapshots before and
after, network calls, and console logs. Also check `app.log` (server
errors) and the failure screenshot and video.

| Symptom | Usually | Check |
|---|---|---|
| Assertion on text or state fails, and the screenshot shows the wrong content | **App regression** | the diff since the last passing run; `app.log` |
| `strict mode violation: resolved to 2 elements` | Test locator too broad (or the app now renders a duplicate) | scope the locator; if the duplicate is new, it may be an app bug |
| Timeout waiting for a locator that is visibly on the page | Locator wrong: text changed, icon-ligature prefix, hidden input | the trace's DOM snapshot at that step |
| `element ... intercepts pointer events` | An overlay or label covers the target | click the visible label; check for a stuck modal (may be an app bug) |
| Passes on retry (flaky) | Race: an asserted state before the UI settled, or shared data between parallel tests | add a proper wait, isolate data; `--repeat-each 5 --retries 0` until stable |
| Visual diff | Intended change → get approval, then update the baseline; unintended → app bug | the `*-diff.png` |
| Every test fails at the start | App didn't start or health check passed too early | `app.log`, `mock_llm.log`, the run's Setup error |
| "PROTECTED DATA MODIFIED" | **The app wrote to its real data** despite the test data URL: an isolation bug in the app | which files changed (summary.md), and how the app resolves its data path |

Report each failure as **app bug** (with what the user would see) or **test
bug** (with what was wrong in the test). Never as "flaky, ignore", and never
fix it by loosening the assertion.
