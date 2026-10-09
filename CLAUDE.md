# MOE Certificate Equivalency Agent

## Project goal

Prototype for a Ministry of Education technical assessment: a single AI agent
that helps students who completed Grade 12 abroad determine certificate
equivalency. The agent dynamically selects from a small set of executable
tools, retrieves answers from indexed public service rules, keeps context
across a multi-turn conversation, validates every answer before returning it,
and escalates to a human when it isn't sure. All applicant data is synthetic;
all external systems (document checks, ticketing) are mocked — nothing here
talks to a real Ministry system.

## Stack

- Python 3.11
- Azure OpenAI via the `openai` SDK (Chat Completions + function calling)
- Pydantic (typed data models and tool I/O)
- Streamlit (chat UI + visible agent trace)
- pytest (tests)

## Rules

1. **The agent decides, the tools execute.** Tool selection and sequencing
   happen inside the model's function-calling loop at runtime. Never hardcode
   a fixed tool call sequence in orchestration code — if a workflow needs to
   happen in a particular order, that ordering must come from the system
   prompt and the tools' own preconditions, not from scripted control flow.

2. **Every answer separates VERIFIED / ASSUMED / SIMULATED.** Any response
   that states a fact, decision, or status must label it:
   - `VERIFIED` — came from a retrieved official source (search_rules) or a
     deterministic tool computation over that source.
   - `ASSUMED` — inferred or default-filled because the user didn't provide
     it; must say what was assumed.
   - `SIMULATED` — the result of a mock action (e.g. escalation ticket
     creation, mock applicant lookup) standing in for a real integration.

3. **Never fabricate eligibility decisions, statuses, or requirements.** If
   a rule, document requirement, or applicant detail isn't available from a
   tool result, say explicitly that it's missing and name the concrete next
   step (e.g. "escalate to a human reviewer" or "ask the applicant for X") —
   never guess or state it with false confidence.

4. **Secrets only via environment variables.** Azure OpenAI/AI Search
   endpoints, keys, and any other credentials are read from the environment
   (e.g. via `.env` + `python-dotenv`, never committed). No keys, endpoints
   with embedded credentials, or tokens in source code.

5. **Every tool returns typed results and supports failure injection.** Each
   tool function returns a Pydantic model (never a raw dict). Each tool
   accepts a `simulate_failure: bool = False` parameter that, when true,
   returns a typed error/failure result instead of executing normally — used
   to test validation and escalation paths without needing real failure
   conditions.

## Commands

```bash
# Run the test suite
pytest

# Run the Streamlit app
streamlit run ui/app.py
```
