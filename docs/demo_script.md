# Demo script — 5 rehearsal scenarios + 15 panel Q&A

These scenarios are deliberately different from the 6 automated scenarios in
`tests/test_scenarios.py` and the sample trace in `docs/slides.md` — fresh
material for rehearsal, not a replay of what's already scripted. Each lists
the expected tool path (what *should* happen if the agent is working
correctly) and what to narrate about *why* the agent made each call — the
panel will care more about the reasoning than the answer text.

Before each scenario: `streamlit run ui/app.py`, pick the applicant from the
sidebar (or just say the ID in chat — both work, since the sidebar button
is a convenience that sends a normal chat message, not a shortcut around
the agent).

---

## Scenario 1 — Complete file, but "complete" isn't the same as "eligible"

**Setup:** "Hi, I finished my American high school diploma abroad. My
applicant ID is APP-001 — can you check if I'm missing anything?"

**Expected tool path:**
1. `get_applicant_record(APP-001)` → found, Daniel Thompson, American curriculum on file.
2. `check_documents(APP-001, "American (US High School Diploma)")` → all 4 base documents present and valid, nothing missing or invalid.
3. `evaluate_eligibility({applicant_id: APP-001, curriculum: "American..."})` → `curr-us-001` (OFFICIAL, UNDETERMINABLE — confirms a baseline SAT Math/TOEFL requirement exists but not the passing score), `curr-us-002` (ASSUMPTION, UNDETERMINABLE), `except-002` (UNDETERMINABLE — APP-001 has no MSAT/IELTS scores, so the exception path doesn't even apply), `except-001` (UNDETERMINABLE, temporal-scope caveat).

**What to explain:** This is the "everything looks fine, but watch the agent stay honest" moment. Documents are genuinely complete — that's a real VERIFIED claim, grounded in a successful `check_documents` call. But the agent should **not** say "you're eligible," because the actual numeric SAT Math/TOEFL passing threshold was never found on an official MOE page during the knowledge-base research (it's an `ASSUMPTION`-labeled chunk) — `evaluate_eligibility` returns UNDETERMINABLE rather than guessing. Point out that this is exactly the gap documented in `docs/assumptions.md`, and that the agent's Next step should be an honest "documents are in order; a definitive eligibility call needs human confirmation of the current scoring threshold" — not a fabricated yes.

---

## Scenario 2 — A country/curriculum the knowledge base doesn't cover at all

**Setup:** "My son studied under the Nigerian WAEC system — is that something you can check? His ID is APP-006."

**Expected tool path:**
1. `get_applicant_record(APP-006)` → found, Chidinma Okafor, WAEC curriculum on file, documents all present/attested.
2. `check_documents(APP-006, "WAEC (West African Senior School Certificate)")` → all 4 base documents present and valid (this part doesn't depend on curriculum-specific rules, so it succeeds normally).
3. `evaluate_eligibility(...)` → no `curriculum_requirements`/`exceptions` chunk matches the "waec" family at all → returns the single synthetic `NONE` rule result: "no equivalency rule chunk found... likely an unsupported country/curriculum combination and should be escalated rather than guessed."
4. `escalate_to_human(reason="unsupported curriculum", ...)` → ticket created.

**What to explain:** This is the cleanest demonstration of "never fabricate." The documents check is genuinely fine and gets reported as such — but when it comes to the actual equivalency decision, the knowledge base has *zero* content for WAEC, and the agent's own tool tells it so explicitly rather than returning a plausible-sounding guess. Walk through why that's structurally different from "I don't know" as a vague LLM hedge — it's a concrete, typed signal (`label: "NONE"`) the validator and orchestrator both act on, which is why escalation happens automatically rather than depending on the model remembering to ask for help.

---

## Scenario 3 — An exception path that looks good, with a caveat worth surfacing anyway

**Setup:** "I graduated in 2018 under the American system — can you check my eligibility? ID is APP-007."

**Expected tool path:**
1. `get_applicant_record(APP-007)` → found, Brandon Michaels, graduated 2018, documents complete/attested.
2. `check_documents(APP-007, "American (US High School Diploma)")` → all present and valid.
3. `evaluate_eligibility(...)` → `except-002` returns **PASS** (MSAT Math 520 ≥ 500, IELTS 5.5 ≥ 5.0 — a real, officially-documented exception path for applicants below the standard SAT Math/TOEFL bar), but `except-001` (auto-included alongside it) returns UNDETERMINABLE: the exception mechanism's source article scopes itself to "academic year 2017-2018 and previous years," so whether it's *still active* for someone asking today is unconfirmed.

**What to explain:** The interesting moment here isn't the PASS — it's that the agent surfaces the PASS *and* the caveat in the same breath, rather than leading with good news and burying the asterisk. This is the "temporal scope" finding from `docs/assumptions.md` made concrete: a real official exception existed as of a 2018 article, but nothing confirms it wasn't superseded since. Good panel talking point: this is the kind of nuance a confident-sounding LLM would normally just drop.

---

## Scenario 4 — Change of circumstances: the user's own correction conflicts with their file

**Setup, turn 1:** "I'm applicant APP-009, I completed British A-levels — can you check if I'm all set?"

**Expected tool path (turn 1):**
1. `get_applicant_record(APP-009)` → found, Henry Whitfield, British (GCE) on file, strong grades.
2. `check_documents(APP-009, "British (GCE)")` → all present and valid.
3. `evaluate_eligibility(...)` → `curr-uk-001` (ASSUMPTION) and `except-003` (OFFICIAL, but its applicability itself depends on `curr-uk-001`) both UNDETERMINABLE — same "documents fine, exact threshold unconfirmed" shape as scenario 1, just for British curriculum this time.
4. Answer: documents complete; eligibility can't be confirmed numerically; offer to escalate if they want a definitive call.

**Setup, turn 2 (the change):** "Actually, I need to correct something — what I did wasn't straight A-levels, it was a mixed program that's officially classified as an International Baccalaureate Diploma."

**Expected tool path (turn 2):**
1. `check_documents(APP-009, "International Baccalaureate (IB) Diploma")` — re-run under the corrected curriculum. APP-009's actual certificate is labeled "GCE Advanced Level" on file, so this now comes back with the certificate flagged **invalid**: curriculum label on the document doesn't match the newly-stated curriculum.
2. (Likely) `evaluate_eligibility(...)` under "IB Diploma" — no chunk actually resolves to a pure "ib" family in this knowledge base (the one chunk that mentions IB, `except-003`, classifies as "british" first), so this also comes back as the unsupported-combination `NONE` result.

**What to explain:** This is the real teaching moment — the agent does **not** just accept the user's correction at face value. It re-runs the check under the new curriculum (re-planning, not restarting — turn 1's findings aren't thrown away), and the actual document evidence contradicts what the user just said. The right behavior is a second clarifying question ("your file says GCE Advanced Level, not IB — which is correct?"), not silently picking either the old or new claim. Emphasize: `case_state.curriculum` updates to reflect what's currently being evaluated, but the conflict goes into `open_questions`, not into a guess.

---

## Scenario 5 — Injected tool failure (use the UI toggle live)

**Setup:** Turn on **"Inject tool failures"** in the sidebar first, then: "Can you check applicant APP-010's file?"

**Expected tool path:**
1. `get_applicant_record(APP-010, simulate_failure=True)` — forced to fail by the toggle regardless of what the model actually requested.
2. Automatic retry: `get_applicant_record(APP-010, simulate_failure=True)` again — fails again (deterministic, not transient, since the toggle forces it every time).
3. `escalate_to_human(reason="Tool 'get_applicant_record' failed twice: ...")` → ticket created automatically by the orchestrator, *not* something the model had to decide to do.
4. Answer: Verified: none (the lookup never succeeded); Simulated: an escalation ticket was filed; Next step: a human reviewer will complete this manually.

**What to explain:** Point out explicitly that the toggle overrides `simulate_failure` on the tool call itself — this is a demo/test knob, not something the model is asked to role-play, so it reliably reproduces the failure path on demand. Narrate the three trace steps live in the trace panel (attempt, retry, auto-escalation) so the panel can see the retry actually happened rather than taking your word for it. Good moment to mention this exact mechanism (`escalate_to_human` itself being made exception-safe) was a finding from a self-review pass — the system's own safety net used to be able to crash if its own file write failed, which would have been a genuinely bad failure mode for the fallback path specifically.

---

# 15 likely panel questions

## Architecture

**1. Why a single agent instead of a multi-agent framework?**
Azure OpenAI function calling already gives dynamic tool selection for free. `agent/orchestrator.py::run_turn` is one loop over six tools, chosen per-turn by the model — no graph of agents handing off to each other, no extra orchestration surface to get wrong, and the whole decision trace stays in one place (`CaseState.tool_history`) instead of being split across agent boundaries.

**2. What actually stops the model from settling into a fixed tool order, defeating "dynamic orchestration"?**
Nothing stops the *model* from forming habits, but the orchestrator never imposes one — there's no code path mapping intent to a scripted tool sequence; every tool call comes straight from `message.tool_calls`, the model's own choice that turn. Confirmed empirically in `tests/test_scenarios.py`: a pure "what documents do I need" question calls `search_requirements` alone, nothing else.

**3. How is multi-turn context preserved, and what happens on contradiction?**
Two things persist across turns: the raw conversation history, and `CaseState` (facts, curriculum, documents, open_questions, assumptions, tool_history). When a new fact conflicts with something already cached — a different curriculum than previously recorded, say — the orchestrator updates the field and raises an `open_question` rather than silently overwriting or merging. See scenario 4 above, or `tests/test_scenarios.py::test_contradictory_curriculum_is_detected_and_flagged_as_open_question`.

## Prompt design

**4. Walk us through the system prompt's structure.**
`agent/prompts.py`, one constant, five stages every turn: Understand (read the user + case state, spot new/contradictory facts), Plan (decide which of the six tools this turn actually needs — don't re-run what hasn't changed), Act (call them, read the real typed results), Validate (self-check before answering), Explain (answer, every claim labeled Verified/Assumed/Simulated, missing info gets a named next step).

**5. Is the Verified/Assumed/Simulated labeling enforced, or just an instruction?**
Both. The prompt instructs it; `agent/validation.py` enforces it in code. `check_required_sections` fails a draft missing any of the four required sections. `check_requirement_claims_grounded` fails a non-trivial "Verified" claim if no OFFICIAL chunk was actually retrieved this session. The model can't just *claim* to be grounded.

**6. What stops the model skipping straight to an answer without validating?**
It isn't something the prompt asks for — it's a non-optional step in `run_turn`: every candidate final answer is passed through `validate_draft()` before being returned to the user, regardless of what the model intended. Fail it once → the model gets the specific failure reasons and one chance to revise. Fail twice → forced escalation; the draft is never sent as-is.

## Hallucination control

**7. How do you stop the agent inventing an eligibility decision or a document requirement?**
Three layers. (1) The actual eligibility computation lives in deterministic code, not the model — `evaluate_eligibility` returns PASS/FAIL/UNDETERMINABLE from rule-matching against the knowledge base, defaulting to UNDETERMINABLE whenever the only source is an `ASSUMPTION`-labeled chunk. (2) The rule-based validator blocks an un-grounded "Verified" claim. (3) An independent second LLM call (`llm_verify`) checks the draft against the session's actual tool outputs and can fail it even when the rule-based checks pass.

**8. What happens when retrieval returns nothing actually relevant?**
`search_requirements` enforces a minimum cosine-similarity floor (`MIN_RELEVANCE_SCORE = 0.3`). Below that, results are dropped entirely rather than returning the "best available" chunks regardless of relevance — an honest empty list instead of something the agent could mistake for grounding. This was added during a self-review pass that found the gap.

**9. What's the weakest link in your grounding approach, honestly?**
The rule-based check confirms *some* OFFICIAL chunk was retrieved this session — not that it supports *this specific* claim. A model could retrieve one legitimate chunk and assert something unrelated under "Verified," and the rule-based layer alone wouldn't catch that mismatch. The LLM verifier is the real backstop (it sees the specific claim and the tool outputs together), but that's probabilistic, not a hard guarantee. Documented explicitly in `README.md`'s Known Limitations rather than papered over.

## Security

**10. How are secrets handled?**
Only via environment variables (`.env`, gitignored), loaded through `config.get_settings()`, which raises a clear `RuntimeError` naming exactly which variables are missing rather than ever hardcoding a key. The API key is typed as `pydantic.SecretStr`, not a plain string — an accidental print/log/repr shows `**********`, not the real value. Found and fixed in a review pass.

**11. Could the UI leak anything it shouldn't?**
It specifically separates our own safe `RuntimeError` (missing-env-var message, no secrets in it) from any other exception, which gets a generic sanitized message instead of raw exception text — a real Azure SDK error could otherwise embed request details into a persisted, screenshot-able chat message. No prompt or credential is ever rendered in the UI.

**12. This runs on mock/synthetic data — what changes with real PII?**
Today `data/applicants.json` is flat JSON with no access control, fine for 10 fabricated people. Real applicant data needs encryption at rest, per-user authorization scoping each session to one person's own case (not a sidebar dropdown over everyone), and audit logging on every read — none of which exists yet, called out explicitly in `docs/slides.md`'s production slide.

## Production readiness

**13. What's the single biggest gap before production?**
No real identity or authorization — anyone running the app can pick any of the 10 applicants from a sidebar dropdown. Production needs real auth (UAE Pass / Azure AD B2C style) scoping each session to the authenticated applicant's own case.

**14. How would you monitor this live?**
`CaseState.tool_history` already gives per-step tool/args/result for free. Production would stream that to an audit store and alert on: escalation rate broken down by reason (tool failure vs. validation failure vs. max-iterations), validation pass/fail rate, and retrieval relevance-score drift — a rising zero-result rate signals the knowledge base is falling behind real usage.

**15. How would you test a prompt or knowledge-base change before shipping it?**
Run the existing pytest suite first (65 tests, sub-second, no live API needed) to catch orchestration/validation regressions, then an offline golden-scenario eval — `data/applicants.json`'s `expected_outcome` fields are exactly that seed set already — re-run against the new prompt/knowledge base before merging, the same way you'd gate any other change with CI.
