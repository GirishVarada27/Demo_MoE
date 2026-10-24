# NFR file: `perf/nfr.json`

The NFRs decide everything the run does: arrival rates, VU pools, phase
order, thresholds and the pass/fail verdict. Ask for them every run (see
SKILL.md rule 1).

```json
{
  "expected_tps": 5,
  "projected_tps": 12,
  "projected_by": "2027-Q4",
  "concurrent_users": 50,
  "latency_ms": {"p95": 2000, "p99": 4000},
  "max_error_rate": 0.01,
  "endpoints": {"POST /api/v1/chat": {"p95": 2500}},
  "think_time_s": 3,
  "durations": {"smoke": "20s", "load": "3m", "projected": "3m", "concurrency": "2m", "stress": "5m", "soak": "30m"},
  "stress_factor": 1.5,
  "regression_tolerance_pct": 15,
  "captured_at": "2026-10-24",
  "captured_from": "user"
}
```

| Field | Required | Meaning / how it's used |
|---|---|---|
| `expected_tps` | yes | Transactions/s today. `load` (and `soak`) phase arrival rate. Must hold every NFR. |
| `projected_tps` | yes | Future transactions/s. `projected` phase arrival rate, which proves headroom for enhancements. Must be at least `expected_tps`. |
| `concurrent_users` | yes | `concurrency` phase: this many VUs looping with `think_time_s` between transactions. |
| `latency_ms` | yes | Any of `p50`/`p90`/`p95`/`p99`/`max` (ms) per HTTP request, checked in every gated phase. |
| `max_error_rate` | yes | Fraction of failed requests allowed (0.01 = 1%). Also sets the functional-check pass rate (1 − value). |
| `endpoints` | no | Per-request-name latency limits; keys are step `name`s from the app config. |
| `think_time_s` | no (1) | Pause between a concurrent user's transactions. |
| `durations` | no | Per-phase durations (k6 format: `30s`, `5m`). `null` skips a phase. `soak` runs only in `full` when set. |
| `stress_factor` | no (1.5) | `full` profile: ramp to `projected_tps × stress_factor` to find the breaking point. Not gated. |
| `regression_tolerance_pct` | no (15) | A p95/p99 rise or TPS drop beyond this vs a comparable baseline fails the run. |

**Transaction** = one iteration of a flow in the app config (it may be several
HTTP requests, e.g. login + search + checkout). TPS targets are flows/s, and
the report shows requests/s separately.

**Throughput check:** an arrival-rate phase passes only if at least 98% of the
planned transactions started (k6 `dropped_iterations`). Dropped iterations mean
the app couldn't keep up.

## Asking the user

Ask in one message, with today's saved values (if any) shown as defaults:

1. Expected peak TPS today?
2. Projected TPS after future growth/enhancements, and by when?
3. Latency limits: p95 (and p99) in ms? Any endpoint with its own limit?
4. Peak concurrent users?
5. Max acceptable error rate?
6. Profile: `pre-merge` (default), `full` (adds stress, and soak if set) or `smoke`?

If an answer is vague ("fast", "a lot"), ask for a number. Don't guess.
