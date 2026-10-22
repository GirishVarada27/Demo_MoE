# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project goal

Prototype for a Ministry of Education technical assessment, modeled on the
UAE Ministry of Education's public "Equivalency of Certificates" service: a
single AI agent that helps students who completed Grade 12 abroad
determine certificate equivalency. The agent dynamically selects from six
executable tools, retrieves grounding from an indexed public rule set,
keeps context across a multi-turn conversation, validates every answer
before returning it, and escalates to a human when it isn't sure. All
applicant data is synthetic; every external system (document checks,
ticketing, draft applications) is mocked — nothing here talks to a real
Ministry system. Full design rationale: `docs/architecture.md`. Knowledge-
base provenance and unverified gaps: `docs/assumptions.md`.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt
cp .env.example .env   # then fill in credentials -- see Environment below

# Run the full test suite (118 tests, no API credentials needed --
# every test injects a fake model client, never calls a real API)
pytest

# Run a single test file or test
pytest tests/test_tools.py
pytest tests/test_tools.py::test_check_documents_complete_file

# Run the Streamlit app (needs real credentials in .env)
streamlit run ui/app.py

# Verify Azure OpenAI chat + embedding deployments are actually reachable
# (separate from the app -- useful when debugging .env/deployment issues)
python check_azure.py
```

No linter/formatter is configured in this repo.

## Environment

`config.get_settings()` selects the chat client mode by presence of
`AZURE_OPENAI_ENDPOINT`: set it (plus `AZURE_OPENAI_API_KEY` and
`AZURE_OPENAI_DEPLOYMENT`) for Azure OpenAI, or leave it unset and set
`OPENAI_API_KEY`/`OPENAI_MODEL` instead for plain OpenAI. `EMBEDDING_DEPLOYMENT`
is required either way — `search_requirements` only supports Azure OpenAI
embeddings regardless of which chat client is active. Missing variables
raise a `RuntimeError` naming exactly what's missing, never a silent
`KeyError`. Full variable table: `README.md`.

## Architecture

**Core loop:** `agent/orchestrator.py::run_turn()` is the single entry
point. Every turn: inject the system prompt (`agent/prompts.py`, a
five-stage Understand → Plan → Act → Validate → Explain instruction) plus
a fresh JSON dump of `CaseState` into context, call the chat model with
all seven tool schemas, dispatch whatever tools it chooses (never a
hardcoded sequence, and never the same exact tool+args call twice in one
turn -- a repeat is detected and the cached result reused instead of
re-invoking), fold each typed result back into `CaseState`, and repeat
until the model returns a final answer or `MAX_TOOL_CALLS` (12) is spent.
Only *successful* non-validation tool calls count toward that budget;
duplicates, declined/rejected calls, and validations don't. A
draft answer is *never* returned directly — it always passes through
`agent/validation.py::validate_draft()` first, and in a case turn (one
that called `get_applicant_record`/`check_documents`/
`create_draft_application`) only after `evaluate_eligibility` has run
*this turn*. An earlier draft gets an ordering error back, which doesn't
count as a validation failure. `record_user_fact` is rejected in code
unless the user's message states a new first-person fact. An applicant
id, a greeting, or a value already on file never qualifies. When the
budget runs out, the reply names the last step reached and the last
claim that failed.

**The seven tools** (`tools/`, registered in `tools/__init__.py`'s
`TOOL_SCHEMAS`/`TOOL_FUNCTIONS` — add both when adding a new tool):
`search_requirements` (embeddings search over `knowledge/equivalency_rules/
*.json`, Azure-only, with a `MIN_RELEVANCE_SCORE` floor so an irrelevant
query returns nothing rather than a misleading "best available" match),
`get_applicant_record`, `check_documents`, `evaluate_eligibility`,
`create_draft_application`, `escalate_to_human`, `record_user_fact` (the
only one with no real side effect -- a stateless pass-through; the actual
`CaseState.user_reported_facts` mutation and conflict-vs-tool-data
detection happens centrally in `_apply_result_to_case_state`, same as
every other tool's effect on `CaseState`). Every tool returns a Pydantic
model (never a dict) and accepts `simulate_failure: bool`.

**Knowledge base labeling:** every chunk in `knowledge/equivalency_rules/`
carries `label: "OFFICIAL"` (cited to a real source) or `"ASSUMPTION"` (no
official source found). `evaluate_eligibility` hardcodes its numeric
pass/fail logic to specific chunk IDs (e.g. `except-002`'s MSAT Math ≥
500) rather than deriving thresholds from the JSON — editing a chunk's
content without updating `tools/equivalency_calc.py::_evaluate_chunk`
silently desyncs the two.

**Two CaseState types, easy to confuse:** `schemas.CaseState` is the full
session-level state (`facts`, `documents`, `curriculum`, `open_questions`,
`assumptions`, `tool_history`), persisted across turns by the caller
(Streamlit session state, or a test) and passed into `run_turn` each turn.
`schemas.EligibilityCaseState` is unrelated — just the narrow
`{applicant_id, curriculum}` pair `evaluate_eligibility` takes as its own
tool-call argument.

**Validation (`agent/validation.py`):** rule-based checks first (required
`Verified`/`Assumed`/`Simulated`/`Next step` sections present; a
non-trivial "Verified" claim needs an `OFFICIAL`-tagged `search_requirements`/
`evaluate_eligibility` result or a successful `check_documents` call *this
session*; an eligibility/status statement needs a successful
`evaluate_eligibility` call) — if any rule-based check fails, the LLM
verifier pass is skipped entirely (no point double-checking a draft
already known to be unsupported). Only if all rule-based checks pass does
a second, independent model call (`llm_verify`) check the draft against
the actual tool outputs. A failed draft gets the specific failure reasons
and up to `MAX_REWRITES` (2) rewrites. If it still fails, the
orchestrator forces `escalate_to_human`. If `check_documents` found a
document missing that the applicant can supply, the draft must name it,
say it blocks the application, and ask for it. The model's own
`escalate_to_human` call is declined in that case, unless the user asked
for a human.

**Retry-then-escalate is the house style**, applied to tool calls
(`_execute_tool_with_retry`) and the main chat-completion call
(`_call_model_with_retry`): retry exactly once, then escalate. Validation
failures are the exception, with two rewrites instead of one. `escalate_to_human` and
`create_draft_application` deliberately catch their own file-I/O errors
and return a typed error instead of raising, since `escalate_to_human`
specifically is the system's own safety net called directly (unwrapped)
from multiple places in `run_turn` — it must never be the thing that
crashes a turn.

**Streamlit (`ui/app.py`) needs an explicit `sys.path` fix** at the top of
the file (inserting the repo root) before its absolute imports —
`streamlit run ui/app.py` puts `ui/`'s own directory on `sys.path`, not the
repo root, so `from agent...`/`from tools...` fail without it. This bug is
invisible to a plain HTTP check (Streamlit renders the traceback as a 200
OK page); verify the UI with `streamlit.testing.v1.AppTest` instead of
curl/requests if you touch import-related code there.

**Tests never need real credentials.** `conftest.py` inserts the repo root
onto `sys.path` (pytest's own mechanism, separate from the Streamlit issue
above). `tests/_fakes.py` has shared fake chat-completion response/client
helpers (`dispatching_create` routes the LLM-verifier's call to a separate
fixed response so tests don't have to predict how many extra calls
validation makes). Tools with real side effects
(`tools/escalation.py::_QUEUE_PATH`, `tools/draft_application.py::
_STORE_PATH`) must be monkeypatched to a `tmp_path` in any test that
exercises them for real, or they'll write into the actual `data/*.json`
mock stores.

## Project rules

1. **The agent decides, the tools execute.** Tool selection and sequencing
   happen inside the model's function-calling loop at runtime. Never
   hardcode a fixed tool-call sequence in orchestration code.
2. **Every answer separates VERIFIED / ASSUMED / SIMULATED** (plus a
   required `Next step` section — enforced by `agent/validation.py`, not
   just instructed in the prompt): `VERIFIED` from a retrieved OFFICIAL
   chunk or a deterministic tool computation over one; `ASSUMED` when
   inferred/default-filled, stating what was assumed; `SIMULATED` for a
   mock action (escalation ticket, draft creation, applicant lookup)
   standing in for a real integration.
3. **Never fabricate eligibility decisions, statuses, or requirements.**
   Missing/undeterminable info gets a named concrete next step (ask the
   applicant for X, or escalate), never a guess.
4. **Secrets only via environment variables**, never committed. API keys
   are typed as `pydantic.SecretStr`, never a plain `str` — an accidental
   print/log/repr must show `**********`, not the real value.
5. **Every tool returns a typed Pydantic result and supports
   `simulate_failure: bool = False`** for testing retry/escalation paths
   without needing real failure conditions.
