"""Build the load-test report for one run directory.

    python report.py <run_dir> [--baseline perf-results/baseline.json]

Reads plan.json, nfr.json, meta.json, metrics.csv (k6 --out csv),
k6_summary.json, k6.log (PERF_ERROR samples), app.log and resources.csv,
and writes:
  results.json  machine-readable verdict, per-phase stats, NFR checks,
                regression vs baseline, diagnostics
  report.md     the same, readable in a PR or terminal
  report.html   self-contained report with timeline charts
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BUCKET_S = 5
LATENCY_KEYS = ("p50", "p90", "p95", "p99", "max")


# --- stats ----------------------------------------------------------------------


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def latency_stats(values: list[float]) -> dict:
    if not values:
        return {}
    return {"count": len(values), "avg": sum(values) / len(values), "min": min(values), "p50": pct(values, 50),
            "p90": pct(values, 90), "p95": pct(values, 95), "p99": pct(values, 99), "max": max(values)}


def _actual(stats: dict, key: str) -> float | None:
    return stats.get(key)


# --- parsing --------------------------------------------------------------------


def parse_metrics(csv_path: Path) -> dict:
    data = {
        "t0": None, "t1": None,
        "dur": defaultdict(list), "dur_by_name": defaultdict(lambda: defaultdict(list)),
        "waiting": defaultdict(list), "connecting": defaultdict(list), "blocked": defaultdict(list),
        "reqs": Counter(), "failed": Counter(), "status": defaultdict(Counter), "error_codes": defaultdict(Counter),
        "iterations": Counter(), "dropped": Counter(), "tx": defaultdict(list), "tx_failed": Counter(),
        "checks": defaultdict(lambda: [0, 0]), "vus": [], "slowest": [],
        "buckets": defaultdict(lambda: {"reqs": 0, "failed": 0, "dur": [], "iters": 0}),
    }
    if not csv_path.exists():
        return data
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            name = row.get("metric_name")
            try:
                ts = float(row.get("timestamp") or 0)
                value = float(row.get("metric_value") or 0)
            except ValueError:
                continue
            data["t0"] = ts if data["t0"] is None else min(data["t0"], ts)
            data["t1"] = ts if data["t1"] is None else max(data["t1"], ts)
            scen = row.get("scenario") or "-"
            b = data["buckets"][int(ts // BUCKET_S) * BUCKET_S]
            if name == "http_req_duration":
                data["dur"][scen].append(value)
                data["dur_by_name"][scen][row.get("name") or row.get("url") or "?"].append(value)
                b["dur"].append(value)
                data["slowest"].append((value, ts, scen, row.get("name"), row.get("status")))
                if len(data["slowest"]) > 200:
                    data["slowest"] = sorted(data["slowest"], reverse=True)[:20]
            elif name == "http_reqs":
                data["reqs"][scen] += 1
                data["status"][scen][row.get("status") or "0"] += 1
                if row.get("error_code"):
                    data["error_codes"][scen][f"{row.get('error_code')} {row.get('error') or ''}".strip()] += 1
                b["reqs"] += 1
            elif name == "http_req_failed":
                if value:
                    data["failed"][scen] += 1
                    b["failed"] += 1
            elif name == "http_req_waiting":
                data["waiting"][scen].append(value)
            elif name == "http_req_connecting":
                data["connecting"][scen].append(value)
            elif name == "http_req_blocked":
                data["blocked"][scen].append(value)
            elif name == "iterations":
                data["iterations"][scen] += 1
                b["iters"] += 1
            elif name == "dropped_iterations":
                data["dropped"][scen] += int(value)
            elif name == "transaction_duration":
                data["tx"][scen].append(value)
            elif name == "transaction_failed":
                data["tx_failed"][scen] += int(value)
            elif name == "checks":
                c = data["checks"][(scen, row.get("check") or "?")]
                c[0 if value else 1] += 1
            elif name == "vus":
                data["vus"].append((ts, value))
    data["slowest"] = sorted(data["slowest"], reverse=True)[:20]
    return data


def parse_error_samples(log_path: Path, limit: int = 40) -> list[dict]:
    samples = []
    if not log_path.exists():
        return samples
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(r'PERF_ERROR (\{.*\})', line.replace('\\"', '"'))
        if m:
            try:
                samples.append(json.loads(m.group(1)))
            except json.JSONDecodeError:
                continue
        if len(samples) >= limit:
            break
    return samples


def parse_app_log(log_path: Path) -> dict:
    if not log_path.exists():
        return {"lines": 0, "errors": 0, "tracebacks": 0, "top": []}
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    errors = [l for l in lines if re.search(r"\b(ERROR|CRITICAL|Exception|Error:)\b", l)]
    exc_lines = [l.strip() for i, l in enumerate(lines) if i > 0 and re.match(r"^\w+(\.\w+)*(Error|Exception)\b", l.strip())]
    top = Counter(exc_lines or errors).most_common(8)
    return {"lines": len(lines), "errors": len(errors), "tracebacks": sum(1 for l in lines if l.startswith("Traceback")),
            "top": [{"message": m[:300], "count": c} for m, c in top]}


def parse_resources(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [{k: float(v) for k, v in row.items()} for row in csv.DictReader(f)]


# --- analysis -------------------------------------------------------------------


def analyse(run_dir: Path, baseline_path: Path | None) -> dict:
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    nfr = json.loads((run_dir / "nfr.json").read_text(encoding="utf-8"))
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8")) if (run_dir / "meta.json").exists() else {}
    k6_summary = json.loads((run_dir / "k6_summary.json").read_text(encoding="utf-8")) if (run_dir / "k6_summary.json").exists() else {}
    m = parse_metrics(run_dir / "metrics.csv")
    resources = parse_resources(run_dir / "resources.csv")
    error_samples = parse_error_samples(run_dir / "k6.log")
    app_log = parse_app_log(run_dir / "app.log")
    lat_nfr = {k: v for k, v in nfr["latency_ms"].items() if k in LATENCY_KEYS}
    max_err = float(nfr["max_error_rate"])
    t0 = m["t0"] or 0

    phases = []
    for ph in plan["phases"]:
        name = ph["name"]
        reqs = m["reqs"][name]
        dur = latency_stats(m["dur"][name])
        lo, hi = t0 + ph["start_s"], t0 + ph["start_s"] + ph["duration_s"]
        vus_in = [v for ts, v in m["vus"] if lo <= ts <= hi + 5]
        res_in = [r for r in resources if lo <= r["timestamp"] <= hi]
        iters = m["iterations"][name]
        achieved = iters / ph["duration_s"] if ph["duration_s"] else 0
        checks = {c: {"passed": v[0], "failed": v[1]} for (s, c), v in m["checks"].items() if s == name}
        third = max(1, len(m["dur"][name]) // 3)
        phases.append({
            "name": name, "gated": ph["gated"], "start_s": ph["start_s"], "duration_s": ph["duration_s"],
            "target_tps": ph["target_tps"], "achieved_tps": round(achieved, 3),
            "throughput_pct": round(100 * achieved / ph["target_tps"], 1) if ph["target_tps"] else None,
            "requests": reqs, "rps": round(reqs / ph["duration_s"], 3) if ph["duration_s"] else 0,
            "transactions": iters, "transaction_failed": m["tx_failed"][name],
            "error_rate": round(m["failed"][name] / reqs, 5) if reqs else None,
            "latency_ms": {k: round(v, 1) for k, v in dur.items() if k != "count"},
            "transaction_latency_ms": {k: round(v, 1) for k, v in latency_stats(m["tx"][name]).items() if k != "count"},
            "ttfb_p95_ms": round(pct(m["waiting"][name], 95) or 0, 1),
            "connect_p95_ms": round(pct(m["connecting"][name], 95) or 0, 1),
            "blocked_p95_ms": round(pct(m["blocked"][name], 95) or 0, 1),
            "latency_trend": {"first_third_p95": round(pct(m["dur"][name][:third], 95) or 0, 1),
                              "last_third_p95": round(pct(m["dur"][name][-third:], 95) or 0, 1)},
            "dropped_iterations": m["dropped"][name],
            "max_vus": int(max(vus_in)) if vus_in else None,
            "status_codes": dict(m["status"][name]), "error_codes": dict(m["error_codes"][name]),
            "checks": checks,
            "endpoints": {n: {k: round(v, 1) for k, v in latency_stats(vals).items()} for n, vals in m["dur_by_name"][name].items()},
            "resources": {"cpu_avg": round(sum(r["cpu_percent"] for r in res_in) / len(res_in), 1) if res_in else None,
                          "cpu_max": max((r["cpu_percent"] for r in res_in), default=None),
                          "rss_start_mb": res_in[0]["rss_mb"] if res_in else None,
                          "rss_end_mb": res_in[-1]["rss_mb"] if res_in else None,
                          "threads_max": max((r["threads"] for r in res_in), default=None)},
        })

    # NFR checks on gated phases.
    nfr_checks = []
    for ph in phases:
        if not ph["gated"]:
            continue
        if not ph["requests"]:
            nfr_checks.append({"phase": ph["name"], "criterion": "requests executed", "target": "> 0", "actual": 0, "pass": False})
            continue
        for key, limit in lat_nfr.items():
            actual = ph["latency_ms"].get(key)
            nfr_checks.append({"phase": ph["name"], "criterion": f"latency {key}", "target": f"< {limit} ms",
                               "actual": f"{actual:.0f} ms" if actual is not None else "n/a", "pass": actual is not None and actual < limit})
        nfr_checks.append({"phase": ph["name"], "criterion": "error rate", "target": f"< {max_err:.2%}",
                           "actual": f"{ph['error_rate']:.2%}", "pass": ph["error_rate"] < max_err})
        failed_checks = sum(c["failed"] for c in ph["checks"].values())
        total_checks = failed_checks + sum(c["passed"] for c in ph["checks"].values())
        if total_checks:
            rate = 1 - failed_checks / total_checks
            nfr_checks.append({"phase": ph["name"], "criterion": "functional checks", "target": f"> {1 - max_err:.2%}",
                               "actual": f"{rate:.2%}", "pass": rate > 1 - max_err})
        if ph["target_tps"]:
            nfr_checks.append({"phase": ph["name"], "criterion": "throughput (TPS)", "target": f">= 98% of {ph['target_tps']:g}",
                               "actual": f"{ph['achieved_tps']:g} ({ph['throughput_pct']}%)", "pass": ph["throughput_pct"] >= 98})
        for endpoint, limits in (nfr.get("endpoints") or {}).items():
            stats = ph["endpoints"].get(endpoint, {})
            for key, limit in limits.items():
                actual = stats.get(key)
                nfr_checks.append({"phase": ph["name"], "criterion": f"{endpoint} {key}", "target": f"< {limit} ms",
                                   "actual": f"{actual:.0f} ms" if actual is not None else "n/a",
                                   "pass": actual is not None and actual < limit})

    if not any(ph["gated"] for ph in phases):  # smoke-only profile: gate on errors and checks
        for ph in phases:
            if ph["requests"]:
                nfr_checks.append({"phase": ph["name"], "criterion": "error rate", "target": f"< {max_err:.2%}",
                                   "actual": f"{ph['error_rate']:.2%}", "pass": ph["error_rate"] < max_err})

    k6_thresholds = []
    for metric, body in (k6_summary.get("metrics") or {}).items():
        for expr, res in (body.get("thresholds") or {}).items():
            k6_thresholds.append({"metric": metric, "threshold": expr, "pass": bool(res.get("ok", res) if isinstance(res, dict) else res)})

    # Regression vs baseline.
    regression = None
    if baseline_path and baseline_path.exists():
        base = json.loads(baseline_path.read_text(encoding="utf-8-sig"))
        tol = float(nfr.get("regression_tolerance_pct", 15))
        rows = []
        base_phases = {p["name"]: p for p in base.get("phases", [])}
        for ph in phases:
            bp = base_phases.get(ph["name"])
            if not bp or not ph["gated"]:
                continue
            for label, cur, old, worse_if in (
                ("latency p95 (ms)", ph["latency_ms"].get("p95"), bp.get("latency_ms", {}).get("p95"), "higher"),
                ("latency p99 (ms)", ph["latency_ms"].get("p99"), bp.get("latency_ms", {}).get("p99"), "higher"),
                ("achieved TPS", ph["achieved_tps"], bp.get("achieved_tps"), "lower"),
            ):
                if cur is None or not old:
                    continue
                change = 100 * (cur - old) / old
                regressed = change > tol if worse_if == "higher" else change < -tol
                rows.append({"phase": ph["name"], "metric": label, "baseline": round(old, 2), "current": round(cur, 2),
                             "change_pct": round(change, 1), "regressed": regressed})
            if ph["error_rate"] is not None and bp.get("error_rate") is not None and ph["error_rate"] - bp["error_rate"] > max_err:
                rows.append({"phase": ph["name"], "metric": "error rate", "baseline": bp["error_rate"], "current": ph["error_rate"],
                             "change_pct": None, "regressed": True})
        # A baseline only gates when it was measured against the same load: comparing a
        # 2-TPS run with a 4-TPS run says nothing about the code change.
        keys = ("expected_tps", "projected_tps", "concurrent_users")
        base_nfr = base.get("nfr") or {}
        mismatch = [k for k in keys if base_nfr.get(k) != nfr.get(k)]
        if base.get("profile") != plan.get("profile"):
            mismatch.append("profile")
        if base.get("llm_mode") != plan.get("llm_mode"):
            mismatch.append("llm_mode")
        regression = {"baseline_file": str(baseline_path), "baseline_commit": (base.get("meta") or {}).get("git", {}).get("commit"),
                      "tolerance_pct": tol, "rows": rows, "comparable": not mismatch, "mismatch": mismatch,
                      "regressed": not mismatch and any(r["regressed"] for r in rows)}

    # Stress: where did it break?
    capacity = None
    stress = next((p for p in plan["phases"] if p["name"] == "stress"), None)
    if stress:
        lo = t0 + stress["start_s"]
        for ts in sorted(k for k in m["buckets"] if lo <= k <= lo + stress["duration_s"]):
            b = m["buckets"][ts]
            p95 = pct(b["dur"], 95) or 0
            err = b["failed"] / b["reqs"] if b["reqs"] else 0
            limit = lat_nfr.get("p95") or max(lat_nfr.values())
            if p95 > limit or err > max_err:
                capacity = {"broke_at_s": round(ts - lo), "tps_at_break": round(b["iters"] / BUCKET_S, 2),
                            "p95_ms": round(p95), "error_rate": round(err, 4),
                            "reason": "latency" if p95 > limit else "errors"}
                break
        if capacity is None:
            capacity = {"broke_at_s": None, "note": f"No breaking point up to the stress peak ({stress['target_tps']:g} TPS)."}

    diagnostics = diagnose(phases, nfr, error_samples, app_log, capacity, resources, meta)

    failed = [c for c in nfr_checks if not c["pass"]]
    verdict = "PASS" if not failed and not (regression and regression["regressed"]) and nfr_checks else "FAIL"
    if meta.get("k6_exit_code") not in (None, 0, 99) and not failed:
        verdict = "FAIL"
        diagnostics.insert(0, {"severity": "high", "title": f"k6 exited with code {meta.get('k6_exit_code')}",
                               "detail": "k6 itself failed or aborted (e.g. smoke abort, script error). See k6.log."})
    timeline = [{"t": ts - t0, "rps": b["reqs"] / BUCKET_S, "tps": b["iters"] / BUCKET_S,
                 "p95": pct(b["dur"], 95), "err": (b["failed"] / b["reqs"]) if b["reqs"] else 0}
                for ts, b in sorted(m["buckets"].items())]
    vus_series = [(ts - t0, v) for ts, v in m["vus"]]
    return {
        "verdict": verdict, "app_name": plan.get("app_name"), "profile": plan.get("profile"), "llm_mode": plan.get("llm_mode"),
        "meta": meta, "nfr": nfr, "notes": plan.get("notes", []), "phases": phases, "nfr_checks": nfr_checks,
        "failed_checks": failed, "k6_thresholds": k6_thresholds, "regression": regression, "capacity": capacity,
        "diagnostics": diagnostics, "error_samples": error_samples, "app_log": app_log,
        "slowest": [{"ms": round(v), "t_s": round(ts - t0, 1), "phase": s, "request": n, "status": st} for v, ts, s, n, st in m["slowest"][:10]],
        "_timeline": timeline, "_vus": vus_series,
        "_resources": [{"t": r["timestamp"] - t0, **r} for r in resources],
    }


def diagnose(phases, nfr, error_samples, app_log, capacity, resources, meta) -> list[dict]:
    """Turn patterns in the numbers into likely causes and where to look."""
    out = []
    lat_p95 = nfr["latency_ms"].get("p95")
    by_name = {p["name"]: p for p in phases}
    low, high = by_name.get("load"), by_name.get("projected")
    if low and high and low["latency_ms"].get("p50") and high["latency_ms"].get("p50"):
        if high["latency_ms"]["p50"] > 1.5 * low["latency_ms"]["p50"]:
            in_flight = high["achieved_tps"] * low["latency_ms"]["p50"] / 1000
            out.append({"severity": "high", "title": "requests queue as load rises",
                        "detail": f"Median latency went from {low['latency_ms']['p50']:.0f} ms at {low['achieved_tps']:g} TPS to "
                                  f"{high['latency_ms']['p50']:.0f} ms at {high['achieved_tps']:g} TPS. A fixed per-request cost "
                                  "doesn't grow with load like that, so requests are waiting for a bounded resource: a worker/thread "
                                  f"pool, DB connection pool, or a downstream rate limit. The limit is about {in_flight:.0f} "
                                  "requests in flight (achieved TPS x unloaded latency). Raise or remove it, or make each request cheaper."})
    for ph in phases:
        name = ph["name"]
        if not ph["requests"] or name == "smoke":
            continue
        if ph["dropped_iterations"]:
            out.append({"severity": "high", "title": f"[{name}] {ph['dropped_iterations']} transactions never started",
                        "detail": f"k6 couldn't sustain {ph['target_tps']:g} TPS (achieved {ph['achieved_tps']:g}): every VU was "
                                  f"busy waiting on slow responses (max VUs {ph['max_vus']}). The app is saturated below the "
                                  "target rate -- look at the latency trend and server resources for the bottleneck."})
        trend = ph["latency_trend"]
        if trend["first_third_p95"] and trend["last_third_p95"] > 1.5 * trend["first_third_p95"] and ph["duration_s"] >= 30:
            out.append({"severity": "medium", "title": f"[{name}] latency climbed during the phase",
                        "detail": f"p95 went from {trend['first_third_p95']:.0f} ms (first third) to {trend['last_third_p95']:.0f} ms "
                                  "(last third) at a constant load: requests are queueing. Typical causes: a bounded worker/thread "
                                  "pool or DB connection pool, lock contention, or memory growth. Compare with the CPU/threads chart."})
        statuses = {k: v for k, v in ph["status_codes"].items() if k not in ("200", "201", "204")}
        five = sum(v for k, v in statuses.items() if k.startswith("5"))
        if five:
            out.append({"severity": "high", "title": f"[{name}] {five} server errors (5xx)",
                        "detail": f"Status breakdown: {statuses}. Check app.log for the stack traces "
                                  f"({app_log['tracebacks']} tracebacks logged)."})
        if statuses.get("429"):
            out.append({"severity": "medium", "title": f"[{name}] rate limited (429 x{statuses['429']})",
                        "detail": "The app or an upstream it calls is rate limiting. If LLM mode is real, this is the provider's quota."})
        if statuses.get("0") or ph["error_codes"]:
            out.append({"severity": "high", "title": f"[{name}] transport errors / timeouts",
                        "detail": f"k6 error codes: {ph['error_codes'] or {'status 0': statuses.get('0')}}. 1050 = request timeout, "
                                  "1211/1212 = connection refused/reset. The server stopped answering or crashed -- see app.log."})
        failing_checks = {c: v["failed"] for c, v in ph["checks"].items() if v["failed"]}
        if failing_checks and not five:
            out.append({"severity": "medium", "title": f"[{name}] functional checks failed under load",
                        "detail": f"Requests returned but the response was wrong: {failing_checks}. See the error samples "
                                  "below for response bodies."})
        if lat_p95 and ph["latency_ms"].get("p95") and ph["ttfb_p95_ms"] > 0.9 * ph["latency_ms"]["p95"] and ph["latency_ms"]["p95"] > lat_p95:
            out.append({"severity": "info", "title": f"[{name}] time is spent server-side",
                        "detail": f"Time to first byte p95 is {ph['ttfb_p95_ms']:.0f} ms of {ph['latency_ms']['p95']:.0f} ms total: the "
                                  "server is slow to produce responses (not network/connection setup). Profile the handler and "
                                  "its downstream calls (DB, model provider)."})
        if ph["connect_p95_ms"] > 100 or ph["blocked_p95_ms"] > 100:
            out.append({"severity": "medium", "title": f"[{name}] slow connection setup",
                        "detail": f"connect p95 {ph['connect_p95_ms']:.0f} ms, blocked p95 {ph['blocked_p95_ms']:.0f} ms: the server's "
                                  "accept backlog or k6 host is struggling."})
        r = ph["resources"]
        cpus = (meta.get("host") or {}).get("cpus") or 1
        if r["cpu_max"] is not None and r["cpu_max"] > 85 * cpus:
            out.append({"severity": "medium", "title": f"[{name}] app is CPU-bound", "detail": f"CPU peaked at {r['cpu_max']:.0f}% ({cpus} cores)."})
        if r["rss_start_mb"] and r["rss_end_mb"] and r["rss_end_mb"] > 1.25 * r["rss_start_mb"] and r["rss_end_mb"] - r["rss_start_mb"] > 50:
            out.append({"severity": "medium", "title": f"[{name}] memory grew {r['rss_start_mb']:.0f} -> {r['rss_end_mb']:.0f} MB",
                        "detail": "Memory kept growing at steady load: possible leak or an unbounded in-memory cache/session store."})
    if capacity and capacity.get("broke_at_s") is not None:
        out.append({"severity": "info", "title": f"[stress] breaking point about {capacity['tps_at_break']} TPS",
                    "detail": f"{capacity['reason']} exceeded the NFR {capacity['broke_at_s']}s into the ramp "
                              f"(p95 {capacity['p95_ms']} ms, errors {capacity['error_rate']:.2%}). Compare with projected TPS "
                              f"{nfr['projected_tps']} to see the headroom."})
    if app_log["top"]:
        out.append({"severity": "info", "title": "most frequent errors in app.log",
                    "detail": "; ".join(f"{t['message']} (x{t['count']})" for t in app_log["top"][:4])})
    return out


# --- rendering ------------------------------------------------------------------


def _fmt(v, unit=""):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:,.1f}{unit}" if abs(v) < 1000 else f"{v:,.0f}{unit}"
    return f"{v}{unit}"


def render_md(r: dict) -> str:
    meta = r["meta"]
    nfr = r["nfr"]
    L = [f"# Load test report: {r['app_name']}", "",
         f"**Verdict: {r['verdict']}** | profile `{r['profile']}` | LLM `{r['llm_mode']}` | "
         f"commit `{(meta.get('git') or {}).get('commit', '')[:10]}`{' (dirty)' if (meta.get('git') or {}).get('dirty') else ''} | "
         f"{meta.get('started', '')}", ""]
    for note in r["notes"]:
        L.append(f"> **Note:** {note}")
    L += ["", "## NFRs tested", "",
          f"- Expected TPS: **{nfr['expected_tps']}**, projected (future) TPS: **{nfr['projected_tps']}**, concurrent users: **{nfr['concurrent_users']}**",
          f"- Latency: {', '.join(f'{k} < {v} ms' for k, v in nfr['latency_ms'].items())}; max error rate {nfr['max_error_rate']:.2%}",
          f"- Load-test DB: `{meta.get('db_url')}`; target: `{meta.get('base_url')}`", ""]
    L += ["## NFR results", "", "| Phase | Criterion | Target | Actual | Result |", "|---|---|---|---|---|"]
    for c in r["nfr_checks"]:
        L.append(f"| {c['phase']} | {c['criterion']} | {c['target']} | {c['actual']} | {'PASS' if c['pass'] else '**FAIL**'} |")
    if r["regression"]:
        reg = r["regression"]
        L += ["", f"## Regression vs baseline (tolerance {reg['tolerance_pct']}%, baseline commit `{(reg['baseline_commit'] or '')[:10]}`)", ""]
        if not reg["comparable"]:
            L += [f"> **Not gating:** the baseline was measured with a different {', '.join(reg['mismatch'])}, so these "
                  "differences reflect the load change, not the code. Save a new baseline for these NFRs with `--save-baseline`.", ""]
        L += ["| Phase | Metric | Baseline | Current | Change | Result |", "|---|---|---|---|---|---|"]
        for row in reg["rows"]:
            ch = f"{row['change_pct']:+.1f}%" if row["change_pct"] is not None else "-"
            L.append(f"| {row['phase']} | {row['metric']} | {row['baseline']} | {row['current']} | {ch} | {'**REGRESSED**' if row['regressed'] else 'ok'} |")
    else:
        L += ["", "_No baseline yet: run with `--save-baseline` once a run passes to enable regression checks._"]
    L += ["", "## Phases", "", "Stress is a ramp: its target is the peak, and achieved TPS is the average over the ramp.", "",
          "| Phase | Target TPS | Achieved TPS | Requests | Error rate | p50 | p95 | p99 | max | Dropped | Max VUs |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in r["phases"]:
        lat = p["latency_ms"]
        target = f"peak {_fmt(p['target_tps'])}" if p["name"] == "stress" else _fmt(p["target_tps"])
        L.append(f"| {p['name']} | {target} | {_fmt(p['achieved_tps'])} | {p['requests']} | "
                 f"{_fmt(p['error_rate'] * 100 if p['error_rate'] is not None else None, '%')} | {_fmt(lat.get('p50'))} | "
                 f"{_fmt(lat.get('p95'))} | {_fmt(lat.get('p99'))} | {_fmt(lat.get('max'))} | {p['dropped_iterations']} | {_fmt(p['max_vus'])} |")
    if r["capacity"]:
        c = r["capacity"]
        L += ["", "**Stress / capacity:** " + (f"broke at about {c['tps_at_break']} TPS ({c['reason']}, p95 {c['p95_ms']} ms)"
                                               if c.get("broke_at_s") is not None else c.get("note", ""))]
    L += ["", "## Diagnostics", ""]
    L += [f"- **{d['severity'].upper()}** {d['title']}: {d['detail']}" for d in r["diagnostics"]] or ["- Nothing unusual detected."]
    L += ["", "## Per-endpoint latency (ms)", "", "| Phase | Endpoint | count | p50 | p95 | p99 | max |", "|---|---|---|---|---|---|---|"]
    for p in r["phases"]:
        for name, s in p["endpoints"].items():
            L.append(f"| {p['name']} | `{name}` | {s.get('count', 0):.0f} | {_fmt(s.get('p50'))} | {_fmt(s.get('p95'))} | {_fmt(s.get('p99'))} | {_fmt(s.get('max'))} |")
    L += ["", "## Status codes and checks", ""]
    for p in r["phases"]:
        L.append(f"- **{p['name']}**: status {p['status_codes']}" + (f", errors {p['error_codes']}" if p["error_codes"] else ""))
        for name, c in p["checks"].items():
            if c["failed"]:
                L.append(f"  - check `{name}`: {c['failed']} failed / {c['passed']} passed")
    if r["error_samples"]:
        L += ["", "## Error samples (first per VU)", ""]
        for s in r["error_samples"][:15]:
            L.append(f"- `{s.get('scenario')}` {s.get('step')} status={s.get('status')} {s.get('error') or ''} "
                     f"({s.get('duration_ms', 0):.0f} ms): {s.get('reason')} -- body: `{(s.get('body') or '')[:200]}`")
    L += ["", "## Slowest requests", "", "| ms | t (s) | Phase | Request | Status |", "|---|---|---|---|---|"]
    L += [f"| {s['ms']} | {s['t_s']} | {s['phase']} | {s['request']} | {s['status']} |" for s in r["slowest"]]
    L += ["", "## Environment", "", f"```json\n{json.dumps(meta, indent=2)}\n```", "",
          "Artifacts in this folder: `plan.json` (exact k6 scenarios/thresholds), `metrics.csv` (every sample), "
          "`k6_summary.json`, `k6.log`, `app.log`, `mock_llm.log`, `resources.csv`."]
    return "\n".join(L) + "\n"


def _svg_chart(title: str, series: list[tuple[str, list[tuple[float, float]], str]], y_unit: str,
               bands: list[dict], limit: float | None = None, width: int = 900, height: int = 220) -> str:
    pts = [p for _, s, _ in series for p in s if p[1] is not None]
    if not pts:
        return f"<p class='muted'>{html.escape(title)}: no data.</p>"
    x_max = max(p[0] for p in pts) or 1
    y_max = max([p[1] for p in pts] + ([limit] if limit else [])) * 1.1 or 1
    pad_l, pad_b, pad_t = 56, 24, 10
    w, h = width - pad_l - 10, height - pad_b - pad_t
    X = lambda x: pad_l + w * x / x_max  # noqa: E731
    Y = lambda y: pad_t + h - h * y / y_max  # noqa: E731
    parts = [f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='{html.escape(title)}'>"]
    for i, b in enumerate(bands):
        x0, x1 = X(b["start_s"]), X(min(x_max, b["start_s"] + b["duration_s"]))
        parts.append(f"<rect x='{x0:.1f}' y='{pad_t}' width='{max(0, x1 - x0):.1f}' height='{h}' class='band{i % 2}'/>"
                     f"<text x='{x0 + 3:.1f}' y='{pad_t + 12}' class='bandlabel'>{html.escape(b['name'])}</text>")
    for frac in (0, 0.5, 1):
        y = y_max / 1.1 * frac
        parts.append(f"<line x1='{pad_l}' x2='{pad_l + w}' y1='{Y(y):.1f}' y2='{Y(y):.1f}' class='grid'/>"
                     f"<text x='{pad_l - 6}' y='{Y(y) + 4:.1f}' class='axis' text-anchor='end'>{y:,.0f}{y_unit}</text>")
    parts.append(f"<text x='{pad_l + w}' y='{height - 6}' class='axis' text-anchor='end'>{x_max:,.0f}s</text>")
    if limit:
        parts.append(f"<line x1='{pad_l}' x2='{pad_l + w}' y1='{Y(limit):.1f}' y2='{Y(limit):.1f}' class='limit'/>"
                     f"<text x='{pad_l + w - 4}' y='{Y(limit) - 4:.1f}' class='limitlabel' text-anchor='end'>NFR {limit:,.0f}{y_unit}</text>")
    legend = []
    for label, s, cls in series:
        line = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in s if y is not None)
        parts.append(f"<polyline points='{line}' class='{cls}'/>")
        legend.append(f"<span class='key {cls}'></span>{html.escape(label)}")
    parts.append("</svg>")
    return f"<figure><figcaption>{html.escape(title)} <span class='legend'>{' '.join(legend)}</span></figcaption>{''.join(parts)}</figure>"


def render_html(r: dict, md_body: str) -> str:
    bands = [{"name": p["name"], "start_s": p["start_s"], "duration_s": p["duration_s"]} for p in r["phases"]]
    tl = r["_timeline"]
    p95_limit = r["nfr"]["latency_ms"].get("p95")
    charts = [
        _svg_chart("Throughput per second", [("transactions/s", [(t["t"], t["tps"]) for t in tl], "s1"),
                                             ("requests/s", [(t["t"], t["rps"]) for t in tl], "s2")], "", bands),
        _svg_chart("Latency p95 (5s buckets)", [("p95 ms", [(t["t"], t["p95"]) for t in tl], "s3")], " ms", bands, p95_limit),
        _svg_chart("Error rate", [("errors %", [(t["t"], t["err"] * 100) for t in tl], "s4")], "%", bands,
                   r["nfr"]["max_error_rate"] * 100),
        _svg_chart("Active virtual users", [("VUs", r["_vus"], "s2")], "", bands),
    ]
    if r["_resources"]:
        charts.append(_svg_chart("App CPU", [("CPU %", [(x["t"], x["cpu_percent"]) for x in r["_resources"]], "s1")], "%", bands))
        charts.append(_svg_chart("App memory (RSS)", [("MB", [(x["t"], x["rss_mb"]) for x in r["_resources"]], "s3")], " MB", bands))

    def table(rows: list[list], head: list[str], classes: list[str] | None = None) -> str:
        out = ["<div class='scroll'><table><thead><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in head) + "</tr></thead><tbody>"]
        for i, row in enumerate(rows):
            cls = f" class='{classes[i]}'" if classes and classes[i] else ""
            out.append(f"<tr{cls}>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>")
        return "".join(out) + "</tbody></table></div>"

    checks = table([[c["phase"], c["criterion"], c["target"], c["actual"], "PASS" if c["pass"] else "FAIL"] for c in r["nfr_checks"]],
                   ["Phase", "Criterion", "Target", "Actual", "Result"], ["" if c["pass"] else "bad" for c in r["nfr_checks"]])
    phases = table([[p["name"], _fmt(p["target_tps"]), _fmt(p["achieved_tps"]), p["requests"],
                     _fmt(p["error_rate"] * 100 if p["error_rate"] is not None else None, "%"),
                     _fmt(p["latency_ms"].get("p50")), _fmt(p["latency_ms"].get("p95")), _fmt(p["latency_ms"].get("p99")),
                     _fmt(p["latency_ms"].get("max")), p["dropped_iterations"], _fmt(p["max_vus"])] for p in r["phases"]],
                   ["Phase", "Target TPS", "Achieved TPS", "Requests", "Errors", "p50 ms", "p95 ms", "p99 ms", "max ms", "Dropped", "Max VUs"])
    reg = ""
    if r["regression"]:
        rows = r["regression"]["rows"]
        caveat = ("" if r["regression"]["comparable"] else
                  f"<p class='note'>Not gating: the baseline used a different {html.escape(', '.join(r['regression']['mismatch']))}.</p>")
        reg = "<h2>Regression vs baseline</h2>" + caveat + table(
            [[x["phase"], x["metric"], x["baseline"], x["current"], f"{x['change_pct']:+.1f}%" if x["change_pct"] is not None else "-",
              "REGRESSED" if x["regressed"] else "ok"] for x in rows],
            ["Phase", "Metric", "Baseline", "Current", "Change", "Result"], ["bad" if x["regressed"] else "" for x in rows])
    diag = "".join(f"<li class='sev-{d['severity']}'><strong>{html.escape(d['title'])}</strong><br>{html.escape(d['detail'])}</li>"
                   for d in r["diagnostics"]) or "<li>Nothing unusual detected.</li>"
    notes = "".join(f"<p class='note'>{html.escape(n)}</p>" for n in r["notes"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Load test: {html.escape(r['app_name'] or '')}</title>
<style>
:root{{--bg:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e8;--ok:#1a7f37;--bad:#c62828;--b0:#f5f6f9;--b1:#eceef4;
--s1:#2f6fd6;--s2:#8a63d2;--s3:#d9822b;--s4:#c62828;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16171a;--fg:#ececf0;--muted:#9a9aa3;--line:#2c2d33;--b0:#1d1e22;--b1:#22242a;color-scheme:dark}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px}}
main{{max-width:1000px;margin:auto}} h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:28px 0 8px}}
.verdict{{display:inline-block;padding:2px 10px;border-radius:4px;color:#fff;background:{'var(--ok)' if r['verdict'] == 'PASS' else 'var(--bad)'}}}
.muted,.axis,.bandlabel{{color:var(--muted);fill:var(--muted);font-size:11px}} .scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}} th,td{{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left;white-space:nowrap}}
tr.bad td{{color:var(--bad);font-weight:600}} figure{{margin:16px 0}} figcaption{{font-weight:600;margin-bottom:4px}}
svg{{width:100%;height:auto}} polyline{{fill:none;stroke-width:1.6}} .s1{{stroke:var(--s1);background:var(--s1)}} .s2{{stroke:var(--s2);background:var(--s2)}}
.s3{{stroke:var(--s3);background:var(--s3)}} .s4{{stroke:var(--s4);background:var(--s4)}} .grid{{stroke:var(--line)}}
.limit{{stroke:var(--bad);stroke-dasharray:4 3}} .limitlabel{{fill:var(--bad);font-size:11px}} .band0{{fill:var(--b0)}} .band1{{fill:var(--b1)}}
.legend{{font-weight:400;color:var(--muted);margin-left:8px}} .key{{display:inline-block;width:10px;height:3px;margin:0 4px 3px 10px}}
li{{margin:6px 0}} .sev-high strong{{color:var(--bad)}} .note{{border-left:3px solid var(--s3);padding-left:8px}}
details{{margin-top:24px}} pre{{white-space:pre-wrap;font-size:12px}}
</style></head><body><main>
<h1>Load test: {html.escape(r['app_name'] or '')}</h1>
<p><span class="verdict">{r['verdict']}</span> profile <code>{r['profile']}</code>, LLM <code>{r['llm_mode']}</code>,
commit <code>{html.escape(((r['meta'].get('git') or {}).get('commit') or '')[:10])}</code>, {html.escape(r['meta'].get('started', ''))}</p>
{notes}
<h2>NFR results</h2>{checks}{reg}
<h2>Phases</h2>{phases}
<h2>Timeline</h2>{''.join(charts)}
<h2>Diagnostics</h2><ul>{diag}</ul>
<details><summary>Full report (endpoints, status codes, error samples, slowest requests, environment)</summary><pre>{html.escape(md_body)}</pre></details>
</main></body></html>"""


def build_report(run_dir: Path, baseline: Path | None) -> dict:
    results = analyse(run_dir, baseline)
    md = render_md(results)
    (run_dir / "report.md").write_text(md, encoding="utf-8")
    (run_dir / "report.html").write_text(render_html(results, md), encoding="utf-8")
    public = {k: v for k, v in results.items() if not k.startswith("_")}
    (run_dir / "results.json").write_text(json.dumps(public, indent=2), encoding="utf-8")
    return public


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--baseline", type=Path)
    a = ap.parse_args()
    res = build_report(a.run_dir, a.baseline)
    print(f"{res['verdict']}: {a.run_dir / 'report.html'}")
    sys.exit(0 if res["verdict"] == "PASS" else 1)
