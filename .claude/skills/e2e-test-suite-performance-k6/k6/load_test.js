// Generic, plan-driven k6 load test.
//
// Everything app-specific (base URL, flows, data pools, scenarios built
// from the NFRs, thresholds) comes from the plan JSON that run_perf.py
// writes. Run via run_perf.py, or directly:
//   k6 run -e PLAN=/abs/path/plan.json -e OUT_DIR=/abs/out load_test.js
//
// One iteration = one TRANSACTION = one flow (one or more HTTP steps).
// "TPS" in the NFRs means transactions/second, i.e. arrival rate of flows.

import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { Trend, Counter } from 'k6/metrics';

const PLAN = JSON.parse(open(__ENV.PLAN));
const OUT_DIR = __ENV.OUT_DIR || '.';
const MAX_ERROR_SAMPLES_PER_VU = 5;

export const options = {
  scenarios: PLAN.scenarios,
  thresholds: PLAN.thresholds,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
  setupTimeout: '120s',
  userAgent: 'e2e-test-suite-performance-k6',
  insecureSkipTLSVerify: !!PLAN.insecure_skip_tls_verify,
};

const transactionDuration = new Trend('transaction_duration', true);
const transactionFailed = new Counter('transaction_failed');
let errorSamples = 0;

// Seeded PRNG (mulberry32) so a given VU/iteration always picks the same
// flow and data -- runs are repeatable, not random.
function rng(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pickWeighted(items, r) {
  const total = items.reduce((s, it) => s + (it.weight || 1), 0);
  let x = r() * total;
  for (const it of items) {
    x -= it.weight || 1;
    if (x <= 0) return it;
  }
  return items[items.length - 1];
}

function uuid(r) {
  const h = () => Math.floor(r() * 0x10000).toString(16).padStart(4, '0');
  return `${h()}${h()}-${h()}-4${h().slice(1)}-${h()}-${h()}${h()}${h()}`;
}

function render(value, ctx) {
  if (Array.isArray(value)) return value.map((v) => render(v, ctx));
  if (value && typeof value === 'object') {
    const out = {};
    for (const k of Object.keys(value)) out[k] = render(value[k], ctx);
    return out;
  }
  if (typeof value !== 'string') return value;
  return value.replace(/\{\{(\w+)(?::([^}]+))?\}\}/g, (_, kind, arg) => {
    switch (kind) {
      case 'pool': {
        const pool = PLAN.data_pools[arg];
        if (!ctx.picked[arg]) ctx.picked[arg] = pool[Math.floor(ctx.r() * pool.length)];
        return String(ctx.picked[arg]);
      }
      case 'var': return String(ctx.vars[arg] ?? '');
      case 'env': return String(__ENV[arg] ?? '');
      case 'uuid': return uuid(ctx.r);
      case 'session': return ctx.session;
      case 'vu': return String(exec.vu.idInTest);
      case 'iter': return String(exec.vu.iterationInScenario);
      default: return `{{${kind}${arg ? ':' + arg : ''}}}`;
    }
  });
}

function getPath(obj, path) {
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function logError(step, res, reason) {
  if (errorSamples >= MAX_ERROR_SAMPLES_PER_VU) return;
  errorSamples += 1;
  console.warn('PERF_ERROR ' + JSON.stringify({
    scenario: exec.scenario.name,
    step: step.name,
    status: res.status,
    error: res.error || null,
    error_code: res.error_code || null,
    duration_ms: res.timings.duration,
    reason: reason,
    body: (res.body || '').toString().slice(0, 400),
    time: new Date().toISOString(),
  }));
}

function runStep(step, ctx) {
  const url = PLAN.base_url.replace(/\/$/, '') + render(step.path, ctx);
  const headers = Object.assign({ 'Content-Type': 'application/json' }, render(PLAN.headers || {}, ctx), render(step.headers || {}, ctx));
  const body = step.body === undefined ? null : JSON.stringify(render(step.body, ctx));
  const params = { headers, tags: { name: step.name }, timeout: PLAN.request_timeout || '60s' };
  const res = http.request(step.method || 'GET', url, body, params);

  const expect = step.expect_status || [200];
  let json = null;
  const checks = {};
  checks[`${step.name}: status in [${expect}]`] = (r) => expect.includes(r.status);
  for (const field of step.json_not_empty || []) {
    checks[`${step.name}: ${field} not empty`] = () => {
      try { json = json || res.json(); } catch (e) { return false; }
      const v = getPath(json, field);
      return v !== undefined && v !== null && v !== '';
    };
  }
  for (const [field, expected] of Object.entries(step.json_equals || {})) {
    checks[`${step.name}: ${field} == ${JSON.stringify(expected)}`] = () => {
      try { json = json || res.json(); } catch (e) { return false; }
      return getPath(json, field) === expected;
    };
  }
  for (const text of step.body_contains || []) {
    checks[`${step.name}: body contains "${text}"`] = (r) => (r.body || '').includes(text);
  }
  const ok = check(res, checks, { name: step.name });
  if (!ok) {
    const failed = Object.keys(checks).filter((k) => { try { return !checks[k](res); } catch (e) { return true; } });
    logError(step, res, failed.join('; '));
  }

  for (const [name, path] of Object.entries(step.extract || {})) {
    try { ctx.vars[name] = getPath(json || res.json(), path); } catch (e) { /* leave unset */ }
  }
  if (step.think_time_s) sleep(step.think_time_s);
  return ok;
}

export default function () {
  run();
}

export function run() {
  const seed = (PLAN.seed || 1) * 1000003 + exec.vu.idInTest * 7919 + exec.vu.iterationInScenario;
  const r = rng(seed);
  const flow = pickWeighted(PLAN.flows, r);
  const ctx = { r, vars: {}, picked: {}, session: `perf-${exec.scenario.name}-${exec.vu.idInTest}-${exec.vu.iterationInScenario}` };
  if (flow.session === 'per_vu') ctx.session = `perf-vu-${exec.vu.idInTest}`;

  const started = Date.now();
  let ok = true;
  for (const step of flow.steps) {
    ok = runStep(step, ctx) && ok;
    if (!ok && flow.stop_on_failure !== false) break;
  }
  const tags = { flow: flow.name };
  transactionDuration.add(Date.now() - started, tags);
  if (!ok) transactionFailed.add(1, tags);
  if (PLAN.think_time_s && exec.scenario.executor === 'constant-vus') sleep(PLAN.think_time_s);
}

export function handleSummary(data) {
  const lines = ['', 'k6 finished. Per-scenario numbers are in the generated report.', ''];
  for (const [name, t] of Object.entries(data.metrics)) {
    if (name.startsWith('http_req_duration{') || name.startsWith('http_req_failed{')) {
      const v = t.values;
      lines.push(`  ${name}: ${v.rate !== undefined ? (v.rate * 100).toFixed(2) + '%' : 'p95=' + (v['p(95)'] || 0).toFixed(0) + 'ms p99=' + (v['p(99)'] || 0).toFixed(0) + 'ms'}`);
    }
  }
  const out = {};
  out[`${OUT_DIR}/k6_summary.json`] = JSON.stringify(data, null, 2);
  out.stdout = lines.join('\n') + '\n';
  return out;
}
