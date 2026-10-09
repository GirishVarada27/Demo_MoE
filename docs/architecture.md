# Architecture (approved design)

## Overview

```
Streamlit UI (ui/app.py: chat + trace panel)
        |
        v
Agent Orchestrator (agent/orchestrator.py, single loop)
  -- Azure OpenAI Chat Completions + function calling
  -- decides per-turn which tool(s) to call, if any
        |
   +----+-------------+---------------+------------------+
   v    v              v               v                  v
search_  get_applicant_ validate_       compute_          escalate_
rules    record         documents       equivalency       to_human
(Azure   (mock JSON     (mock rule      (rule-based calc,  (mock ticket
AI       "DB")          lookup)         not free LLM text)  queue)
Search)
        |
        v
Pre-response Validator (agent/validation.py: grounding/citation check + confidence gate)
        |
        v
Final answer + full trace -> Streamlit
```

One agent, one loop. Function calling gives dynamic tool selection for
free -- no multi-agent framework needed.

## Agent loop

1. Append user message to session history.
2. Call Azure OpenAI with full history + tool schemas + system prompt.
3. Model either answers directly or emits one/more tool calls.
4. Execute requested tool(s) locally, append results as `tool` messages,
   loop back to step 2 (capped at ~6 iterations).
5. On a final message with no further tool calls, hand it to the validator.
6. Validator passes it through, forces one self-correction retry, or
   redirects to `escalate_to_human`.
7. Every step (tool name, args, result summary, latency, confidence) is
   appended to a trace log rendered in the Streamlit sidebar/expander.

Context across turns is kept as both the raw message list (for the LLM)
and a structured `SessionContext` object (applicant id, accumulated
grades/docs, last validation result) that tools read/write.

## Tools

```
search_equivalency_rules(query, country=None, curriculum=None, top_k=5)
    -> list[RuleSnippet{text, source, country, curriculum, score}]

get_applicant_record(applicant_id)
    -> ApplicantRecord | NotFound

validate_documents(applicant_id, submitted_documents)
    -> {missing, extra, complete}

compute_equivalency(country, curriculum, subject_grades)
    -> {local_equivalent, per_subject_mapping, confidence, rule_citations}

escalate_to_human(applicant_id, reason, summary)
    -> {ticket_id, status}
```

`compute_equivalency` is deterministic against indexed rules, never a
free-form LLM paraphrase -- the single most important anti-hallucination
decision in this design.

## Data models

See `schemas.py`: ApplicantRecord, EquivalencyRule, DocumentRequirement,
RuleSnippet, ToolCallTrace, ValidationResult, EscalationTicket,
SessionContext.

## Validation strategy

**Per-tool (defensive):** each tool validates its own inputs/outputs --
e.g. `compute_equivalency` returns `confidence=0` rather than inventing a
mapping when no rule matches.

**Pre-response gate:** before any draft answer reaches the user:
- Grounding check -- every claim must trace to a citation/tool result from
  this turn's trace.
- Confidence gate -- below-threshold confidence or missing required docs
  routes to escalation instead of an asserted answer.
- Optional LLM-judge pass -- a short second call checking the draft only
  states facts supported by attached tool outputs.
- On failure: one bounded self-correction retry, then forced escalation.

**Escalation triggers:** no matching rule; confidence below threshold;
unresolved missing documents; explicit user request for a human;
validator fails twice in a row; conflicting rules.

## Azure services

- **Azure OpenAI** (Chat Completions + function calling) -- core reasoning
  and native dynamic tool selection.
- **Azure AI Search** -- retrieval over the rule corpus (hybrid
  vector+keyword, filterable by country/curriculum). Acknowledged
  trade-off: a local vector store would suffice at this corpus size, but
  AI Search demonstrates the production-scale retrieval path.
- Nothing else -- the mock applicant DB and escalation queue are local
  JSON (`data/`) deliberately, to keep the demo self-contained.

## Risks

1. Hallucinated equivalency claims -- mitigated structurally by routing
   all equivalency output through `compute_equivalency` + citation
   grounding in the validator.
2. Escalation threshold guesswork -- thresholds are config-driven and
   confidence is logged in the trace for calibration.
3. Suboptimal tool-call ordering -- bounded by the system prompt plus the
   validator acting as a backstop regardless of path taken.
4. Context drift across turns -- mitigated by the structured
   `SessionContext` object, not reliance on raw chat history alone.
5. Azure AI Search may be oversized for this corpus -- acknowledged as a
   deliberate production-readiness trade-off.
6. Synthetic data realism -- system prompt and UI banner mark all data as
   synthetic; no live external lookups.
7. Latency from the loop + validator + optional judge call -- offset by
   showing the live trace and capping iterations.
8. Escalation is mock -- framed in the demo narrative as an integration
   point with a real case-management system.
