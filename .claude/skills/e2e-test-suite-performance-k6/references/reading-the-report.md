# Reading the report

Each run folder (`<results_dir>/<timestamp>-<profile>/`) holds:

| File | Use it for |
|---|---|
| `report.html` | The report: verdict, NFR table, regression, phases, timeline charts with phase bands, diagnostics |
| `report.md` | The same plus per-endpoint latency, status codes, error samples, slowest requests, environment |
| `results.json` | Machine-readable verdict and stats (also the baseline format) |
| `plan.json` | The exact k6 scenarios and thresholds generated from the NFRs |
| `metrics.csv` | Every k6 sample (`--out csv`), for ad-hoc analysis |
| `k6.log` | k6 output, including `PERF_ERROR` samples (status, error code, response body) |
| `app.log` / `mock_llm.log` / `seed.log` | Stdout/stderr of the app, the mock and the seed step |
| `resources.csv` | App CPU %, RSS, threads per second (needs `psutil` in the runner's Python) |

## Phases

- **smoke**: 1 VU sanity check. It aborts the whole run if more than 50% of requests fail, so a broken build fails fast.
- **load**: constant arrival rate at `expected_tps`. Today's load must meet every NFR.
- **projected**: constant arrival rate at `projected_tps`. The future load must meet every NFR (this is the headroom check).
- **concurrency**: `concurrent_users` VUs looping with think time. The achieved TPS here approximates users ÷ (latency + think time).
- **stress** (full): ramps to `projected × stress_factor`. Not gated; finds the breaking point.
- **soak** (full, if set): expected TPS for a long time. Shows leaks and slow degradation.

## Symptom → likely cause

| Symptom in report | Likely cause | Look at |
|---|---|---|
| Dropped transactions, achieved TPS < target | App saturated: every VU stuck waiting | latency trend, CPU/threads, "requests queue as load rises" |
| p50 grows with load while downstream latency is fixed | Bounded worker/thread/connection pool or lock | the in-flight estimate in the diagnostic, app pool settings |
| Latency climbs within a constant-load phase | Queue build-up, memory growth, GC, connection leak | RSS chart, `app.log` |
| 5xx errors | Exceptions under load | `app.log` tracebacks, error samples |
| Status 0 / k6 error 1050 | Request timeout (`request_timeout`) | server hung or overloaded |
| k6 error 1211/1212 | Connection refused/reset | app crashed or ran out of sockets; `app.log` |
| 429 | Rate limiting (app or upstream provider) | upstream quotas; with `--llm real`, provider limits |
| 200s but checks fail | Wrong responses under load (race, stale cache, partial data) | error samples' response bodies |
| TTFB ≈ total latency | Time spent server-side, not network | profile the handler and its DB/LLM calls |
| High connect/blocked time | Accept backlog or client-side (k6 host) saturation | server backlog settings; k6 host CPU |
| CPU ≥ 85% × cores | CPU-bound | profile hot paths; scale out |
| RSS keeps growing | Leak or unbounded cache/session store | heap snapshot; session/cache eviction |

## Regression check

`--baseline` (default `<results_dir>/baseline.json`) is compared per gated
phase on p95, p99, achieved TPS and error rate. It gates only when the
baseline used the same profile, LLM mode and TPS/user targets; otherwise the
comparison is shown as informational. Update the baseline deliberately (after
a passing run you accept), never just to make a regression go away.
