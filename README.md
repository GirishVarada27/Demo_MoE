# MOE Certificate Equivalency Agent

A prototype AI agent, built for a Ministry of Education technical assessment, that
helps students who completed Grade 12 abroad navigate certificate equivalency.
Modeled on the UAE Ministry of Education's public "Equivalency of Certificates"
service. A single Azure OpenAI-backed agent dynamically selects from six
tools, retrieves grounding from an indexed public rule set, keeps context
across turns, validates every answer before sending it, and escalates to a
human when it's unsure. **All applicant data is synthetic and every external
integration is mocked** — nothing in this repo talks to a real Ministry
system or a real person.

See `CLAUDE.md` for the project's standing rules (VERIFIED/ASSUMED/SIMULATED
labeling, no hardcoded tool sequences, typed tool results, secrets handling)
and `docs/architecture.md` for the full design writeup.

## Setup

Requires Python 3.11+ (developed and tested against 3.12.10).

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env with your Azure OpenAI credentials
```

If you're on a machine with no usable `pip`/Python on `PATH` (this project
was built in exactly that environment — only Windows Store alias stubs,
and `winget`'s MSI-based installer failed since `msiserver` doesn't exist
there), the python.org **embeddable** distribution is a reliable fallback:
download the `embed-amd64.zip` for your target version, extract it,
uncomment `import site` in the extracted `python3XX._pth` file (it ships
with site-packages disabled), then run `get-pip.py` (from
`https://bootstrap.pypa.io/get-pip.py`) with that `python.exe` before
`pip install -r requirements.txt` as usual.

## Environment variables

Set in `.env` (see `.env.example`). The **chat client is configurable**:
if `AZURE_OPENAI_ENDPOINT` is set, the agent uses `AzureOpenAI`; otherwise
it falls back to plain `OpenAI` using `OPENAI_API_KEY`/`OPENAI_MODEL`. Pick
one mode — don't mix and match.

| Variable | Purpose |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | Your Azure OpenAI resource endpoint. Set this to select Azure mode for the chat client |
| `AZURE_OPENAI_API_KEY` | API key for that resource (loaded as a `pydantic.SecretStr` — never logged or printed in plain text). Required in Azure mode |
| `AZURE_OPENAI_DEPLOYMENT` | Chat-completion deployment name (must support function/tool calling). Required in Azure mode |
| `EMBEDDING_DEPLOYMENT` | Embedding deployment name, used by `search_requirements`. **Always required regardless of chat-client mode** — retrieval only supports Azure OpenAI embeddings today |
| `OPENAI_API_KEY` | Plain OpenAI API key (also a `SecretStr`). Required only if `AZURE_OPENAI_ENDPOINT` is left unset |
| `OPENAI_MODEL` | Plain OpenAI chat model name (e.g. `gpt-4o`, must support tool calling). Required only if `AZURE_OPENAI_ENDPOINT` is left unset |

`config.get_settings()` raises a `RuntimeError` naming exactly which
variables are missing for whichever mode you're in — it never fails with an
opaque `KeyError`, and never needs real credentials to import or run the
test suite (every test injects a fake model client).

## How to run

```bash
# Run the test suite (65 tests, no Azure credentials required)
pytest

# Run the Streamlit app (requires real Azure OpenAI credentials in .env)
streamlit run ui/app.py
```

The UI gives you: a chat panel, a live agent-trace panel (every tool call
with its step number, inputs, and outputs), a sidebar to pick one of the 10
synthetic applicants and drop them into the conversation, a toggle to force
every tool call to simulate failure (demos the retry-then-escalate path),
and every answer broken out into its Verified / Assumed / Simulated / Next
step sections.

## Data sources

- **Knowledge base** (`knowledge/equivalency_rules/*.json`): chunks
  covering eligibility, required documents, attestation, curriculum-specific
  requirements, fees/timelines, and exceptions. Each chunk carries an `id`,
  `topic`, `text`, `source_url`, and a `label` of `OFFICIAL` (grounded in a
  real `moe.gov.ae`/`u.ae` page, cited) or `ASSUMPTION` (no official source
  found — see **Assumptions** below). Researched via live web search against
  UAE MOE's public pages on 2026-10-08.
- **Synthetic applicants** (`data/applicants.json`): 10 fabricated
  applicants (`tools/applicant_lookup.py` loads and validates them via
  Pydantic), covering a complete file, a missing attestation, a missing
  identity document, a contradictory stated-vs-document curriculum, an
  expired document, an unsupported country/curriculum, two exception cases,
  and two additional edge cases. Each carries an `expected_outcome` note for
  testing.
- **Mock stores** (`data/draft_applications.json`,
  `data/escalation_queue.json`): empty by default, populated at runtime by
  `create_draft_application` / `escalate_to_human`. Nothing here is a real
  case-management system.

## Assumptions

Full list with reasoning and what would need to be confirmed:
**`docs/assumptions.md`**. In priority order, what you'd verify against the
live MoE site before this moves past prototype status: the equivalency
service **fee** (no official figure found; secondary sources conflict
wildly), the **processing timeline** (same gap), the **attestation chain
order** (the commonly-cited 3-step sequence is UAE's general convention,
not confirmed specifically for this service), the **detailed document
checklist** (the official PDF guide 404'd during research), **curriculum-
specific score thresholds** (American curriculum's baseline requirement is
confirmed to exist, exact numbers aren't; British/Indian curricula have no
official numeric rule at all), and whether the one officially-documented
**exception mechanism** is still active (it scopes itself to "academic year
2017-2018 and previous years").

## Dependencies

See `requirements.txt`: `openai` (Azure OpenAI client), `pydantic` (typed
models everywhere — every tool returns one instead of a raw dict),
`python-dotenv` (loads `.env`), `streamlit` (UI), `pytest` (tests). No
pinned upper bounds currently — a fresh install pulled `openai` 3.26.1 (two
major versions past the `>=1.30` floor); its API surface was verified
compatible with this codebase's usage, but an unbounded floor leaves you
exposed to a future breaking release landing silently on a clean install.

## Known limitations

- **Knowledge-base gaps are real, not hypothetical.** Fees, timelines, the
  exact attestation order, and most curriculum-specific thresholds were
  never found on an official page (see Assumptions above). The agent is
  designed to say so and escalate rather than guess, but the underlying
  knowledge really is incomplete.
- **Grounding is claim-present, not claim-specific.** The validator's
  rule-based check confirms *some* OFFICIAL chunk was retrieved this
  session before allowing a non-trivial "Verified" claim — it does not
  confirm that chunk's content actually supports *that specific* claim.
  The second-pass LLM verifier (which sees the actual claim and tool
  outputs together) is the real backstop here, and it's probabilistic, not
  a hard guarantee.
- **`evaluate_eligibility`'s numeric thresholds are hardcoded to specific
  chunk IDs** (e.g. `except-002`'s MSAT Math ≥ 500) rather than being
  data-driven from the knowledge JSON. Accurate as of the last knowledge-
  base update, but will silently desync if that content changes without a
  matching code change.
- **No persistence beyond local JSON files.** `CaseState` lives in
  Streamlit's session state (lost on refresh/restart); the mock draft and
  escalation stores are flat JSON files with no concurrency control —
  fine for a single-user demo, not for concurrent real usage.
- **No authentication, audit logging, or multi-language support** — this
  is a single-session, English-only prototype. See `docs/slides.md` for
  what a production version would add.
- **The UI is functional, not polished**, and was built/validated via
  Streamlit's headless `AppTest` harness rather than manual browser testing
  in this environment — worth a manual pass in a real browser before any
  demo.
- **This machine had no usable Python by default** (only Windows Store
  alias stubs, and `winget`'s MSI-based installer fails here since
  `msiserver` doesn't exist in this environment). The embeddable
  distribution workaround above is what made running the test suite and
  the Streamlit smoke tests possible at all during development.
