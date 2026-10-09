# Slide outline — MOE Certificate Equivalency Agent

4 slides: problem, architecture, agent behavior & results, production approach.

---

## Slide 1 — The problem

**Students who complete Grade 12 abroad can't enroll locally without a
certificate equivalency decision — and the process is genuinely hard to
get right.**

- Modeled on the UAE Ministry of Education's public "Equivalency of
  Certificates" e-service.
- Why it's hard, not just paperwork:
  - Eligibility criteria vary by curriculum (American/British/Indian/etc.)
    and aren't uniformly documented publicly.
  - Multi-step document + attestation requirements, easy to get wrong.
  - Applicants frequently reveal new or contradictory information mid-case
    (wrong curriculum on file, can't obtain a required document).
  - A wrong or fabricated answer has real consequences — a confident
    eligibility statement with no basis is worse than no answer.
- **Ask:** a single AI agent that chooses its own tools dynamically,
  grounds every claim in retrieved public rules, validates itself before
  answering, and asks for help when it genuinely doesn't know.
- **Scope of this prototype:** synthetic applicants, mocked integrations,
  real Azure OpenAI + real retrieval over a real (if incomplete) public
  knowledge base.

---

## Slide 2 — Architecture

**One tool-calling loop on Azure OpenAI — the model decides, the tools
execute, nothing is scripted.**

```
Streamlit UI (chat + live trace + applicant picker + failure-injection toggle)
        |
        v
agent.orchestrator.run_turn()
  -- system prompt: Understand -> Plan -> Act -> Validate -> Explain
  -- function-calling loop, MAX_ITERATIONS=6 guard
        |
   6 tools, model picks any subset, any order, every turn:
   search_requirements   get_applicant_record   check_documents
   evaluate_eligibility  create_draft_application  escalate_to_human
        |
        v
agent.validation.validate_draft()
  -- rule-based: required sections present, claims grounded, no
     eligibility statement without a successful evaluate_eligibility call
  -- LLM verifier: independent pass checking claims against actual tool
     outputs
  -- fail twice -> escalate_to_human, never send an unvalidated draft
```

- **CaseState**, persisted per session: `facts`, `documents`, `curriculum`,
  `open_questions`, `assumptions`, `tool_history` — updated incrementally,
  never rebuilt from scratch, so contradictions get flagged instead of
  silently overwritten.
- **Knowledge base**: chunks labeled `OFFICIAL` (cited to a real
  `moe.gov.ae`/`u.ae` page) or `ASSUMPTION` (no official source found —
  `docs/assumptions.md`), retrieved by embedding similarity with a
  relevance floor so an irrelevant query returns nothing rather than the
  "best available" guess.
- **Every tool**: typed Pydantic input/output, a `simulate_failure` flag,
  retried once on failure before auto-escalating.
- **Every answer**: structured into Verified / Assumed / Simulated / Next
  step — never sent to the user without passing validation first.

---

## Slide 3 — Agent behavior & results

**Demonstrated via automated scenario tests, not just code review:**

- "What documents do I need?" → `search_requirements` only (no case
  context needed).
- "Am I missing anything?" → looks up the applicant, checks documents,
  reports the real gap.
- "Actually I can't get my attested transcript" → recognizes the blocker
  mid-conversation and **changes its plan** (escalates) instead of
  repeating the same document check — without restarting the conversation.
- Contradictory input (user states one curriculum, the on-file record says
  another) → surfaced as a clarifying question, never silently resolved.
- Tool failure → retried once, then gracefully escalated with a SIMULATED
  ticket.
- Out-of-scope request (e.g. a visa question) → declined with a concrete
  next step, no tool calls wasted.

**Sample trace** (real output, `agent.orchestrator.run_turn`, applicant
APP-002, "Am I missing anything? My applicant id is APP-002."):

```
step=1 tool=get_applicant_record
  args={'applicant_id': 'APP-002'}
  result_summary=found APP-002 (Fatima Noor)
step=2 tool=check_documents
  args={'applicant_id': 'APP-002', 'curriculum': 'British (GCE)'}
  result_summary=present=['grade_12_certificate', 'passport_copy',
    'emirates_id_copy'], missing=[], invalid=['transcript']
```

**Resulting answer:**

> Verified: Your transcript was submitted but is not yet attested; your
> certificate, passport, and Emirates ID are all present and valid.
> Assumed: None.
> Simulated: Applicant and document records were looked up from the mock
> case file.
> Next step: Get your transcript attested and resubmit it.

**Test coverage:** 65 automated tests — tool unit tests (incl. every
`simulate_failure` path), validator unit tests (every grounding rule, both
pass and fail), orchestrator integration tests (retry/escalation,
multi-turn state updates), and 6 end-to-end scenario tests asserting on
*behavior* (tool-call patterns, case-state mutations) rather than exact
wording.

---

## Slide 4 — Production approach

**What changes between this prototype and a real deployment:**

- **Auth** — replace the sidebar applicant picker with real identity (UAE
  Pass / Azure AD B2C), scoping each session to the authenticated
  applicant's own case only; service-to-service auth for any real backend
  integration replacing the mock JSON stores.
- **Audit logs** — `ToolCallTrace` already captures step/tool/args/result
  per turn; in production this streams to an immutable, queryable store
  (e.g. Azure Table/Cosmos DB with append-only access), retained per the
  Ministry's records-retention policy, with every escalation and every
  validation failure separately alertable.
- **Arabic/English** — bilingual system prompt and knowledge base
  (today's chunks are English-only, sourced from English MOE pages);
  RTL-aware Streamlit (or production front-end) layout; validator's
  section-parsing and grounding checks need to work against Arabic output
  too, not just English keyword matching.
- **Human-in-the-loop** — `escalate_to_human` today writes to a local JSON
  file; production needs a real case-management queue with SLAs, a
  reviewer dashboard showing the full trace (not just the final answer),
  and a feedback path so reviewer corrections improve the knowledge base
  over time instead of repeating the same gap.
- **Evals** — an offline golden-scenario suite (this prototype's
  `data/applicants.json` `expected_outcome` fields are a starting point)
  run on every prompt/model/knowledge-base change; online evals sampling
  live conversations against the same rule-based + LLM-verifier checks,
  with human QA spot-checking the LLM verifier's own accuracy.
- **Monitoring** — per-turn latency and cost, escalation rate and its
  reasons (tool failure vs. validation failure vs. max-iterations),
  validation pass/fail rate, retrieval relevance-score distribution (drift
  detection — are queries increasingly returning low-relevance or empty
  results, signaling knowledge-base gaps growing), and alerting on repeat
  escalations for the same case.
