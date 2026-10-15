# Assumptions in the knowledge base

Every chunk in `knowledge/equivalency_rules/*.json` is labeled `OFFICIAL` or
`ASSUMPTION`. This file lists every `ASSUMPTION` chunk, why it couldn't be
confirmed against an official source, and what a confirmed replacement
would need to show. Research session: 2026-10-08, UAE Ministry of Education
(moe.gov.ae / u.ae) public pages only, via WebSearch + WebFetch.

## doc-004 — detailed document checklist
**Unverified claim:** any required document beyond Emirates ID, passport,
and original grade 10-12 certificates (e.g. photos, per-subject
transcripts, attestation-stamp copies).
**Why unconfirmed:** the official "Guide to Academic Certificate
Equivalency" PDF linked from MOE's e-services pages returned a 404 when
fetched; a second official "Applicant Welcome Kit" PDF is a scanned image
with no extractable text in this session.
**To confirm:** retrieve a working copy of either PDF (or the live MOE
e-services application form itself) and read the actual checklist.

## attest-002 — attestation chain sequence
**Unverified claim:** the 3-step attestation chain (issuing country's
Ministry of Foreign Affairs → UAE Embassy in that country → UAE Ministry
of Foreign Affairs and International Cooperation / MOFAIC).
**Why unconfirmed:** this sequence is UAE's general document-attestation
convention and appears on multiple attestation-agency sites, but no
official MOE/u.ae page was found stating this exact sequence specifically
for Grade 12 certificate equivalency.
**To confirm:** check the MOE equivalency e-service application flow or
MOFAIC's own attestation-service pages for a certificate-equivalency-specific
statement of the required attestation order.

## curr-us-002 — SAT Math / TOEFL baseline thresholds
**Unverified claim:** specific minimum passing scores for the baseline
SAT Math and TOEFL requirement for American-curriculum equivalency (only
the *existence* of this requirement is officially confirmed, via the
exception article's alternatives).
**Why unconfirmed, but reasonably well-sourced:** a translated copy of
Ministerial Decision No. 199 of 2019, hosted on ibo.org (not moe.gov.ae/
u.ae directly, and from 2019 — may be superseded), states SAT Math >= 450
(or EmSAT Achieve Math >= 500 as an alternative) and TOEFL iBT >= 61 (or
IELTS Academic >= 5.0, or EmSAT English >= 1100). These numbers are
corroborated by an independent source — GEMS American Academy Abu Dhabi's
own admissions guide states the identical thresholds for UAE-resident
American-curriculum applicants. Two independent sources agreeing is
better grounding than a single secondary blog, but neither is the
Ministry's own current equivalency page, so this stays ASSUMPTION, not
OFFICIAL. `tools/equivalency_calc.py::evaluate_eligibility` now computes a
PASS/FAIL from these numbers when an applicant's SAT Math/TOEFL scores are
available, but always labels the result provisional ("based on assumed
rules, verify with MoE") rather than VERIFIED.
**To confirm:** find the official criteria document referenced as
"Ministerial Decision No. 4443 of 2001" (an older decision than 199/2019 —
unclear how the two relate) or whichever is the current successor, and
read it directly on an official moe.gov.ae/u.ae page rather than a
third-party-hosted translation.

**Note on curr-uk-001/curr-in-001 below: these were NOT given the same
treatment.** Only curr-us-002 was upgraded to a provisional computed
threshold in this pass, since that's the specific gap raised in testing;
British and Indian curricula still return UNDETERMINABLE unconditionally
whenever the only grounding is their ASSUMPTION chunk, pending the same
kind of corroborated secondary-source research curr-us-002 just got.

## curr-uk-001 — British curriculum thresholds
**Unverified claim:** minimum A-level/AS-level pass counts and
GCSE/IGCSE pass counts for British-curriculum equivalency.
**To confirm:** same as above — locate the current official criteria
decision/circular rather than secondary aggregator sites.

## curr-in-001 — Indian curriculum (CBSE/ICSE) requirements
**Unverified claim:** any curriculum-specific numeric requirement for
CBSE/ICSE certificates.
**Why unconfirmed:** no official MOE/u.ae page addressing this curriculum
specifically was found; only generic, non-curriculum-specific secondary
guidance exists.
**To confirm:** same as above.

## fees-001 — equivalency service fee
**Unverified claim:** any specific AED fee amount. Secondary sources
conflict sharply (figures seen ranging AED 50-1,250).
**To confirm:** the official MOE e-services portal (eservices.moe.gov.ae)
fee schedule at time of application — this is the single highest-priority
item to verify live, since a wrong fee is a concrete, checkable
user-facing error.

## fees-002 — processing timeline
**Unverified claim:** any specific number of working days/months.
Secondary sources conflict sharply (5 working days to 3 months).
**To confirm:** same official e-services portal, or MOE's published
service-level-agreement / customer charter if one exists.

## except-001 through except-003 — exception procedure currency
**Unverified claim:** whether the documented exception procedure
(Ministerial Decision No. 4443 of 2001 shortfall path) is still active
today. The source article explicitly scopes itself to graduates of
academic year 2017-2018 and earlier.
**To confirm:** check for a newer MOE circular/news item superseding or
reaffirming this exception mechanism for current applicants.

## except-004 — appeals process
**Unverified claim:** existence of any standing appeals/review committee
for equivalency decisions.
**To confirm:** search moe.gov.ae for a dedicated "appeals" or "grievance"
procedure page, or contact MOE customer service directly.

---

## Priority list to verify against the live MoE website

In order of how much a wrong answer would mislead a real applicant:

1. **Fee amount** (fees-001) — conflicting secondary figures, no official
   number found at all.
2. **Processing timeline** (fees-002) — same issue, sets user expectations.
3. **Attestation chain and order** (attest-002) — a wrong sequence could
   cause a rejected application or wasted embassy visits.
4. **Detailed document checklist** (doc-004) — missing a required document
   causes real-world application delay.
5. **Curriculum-specific score thresholds** — determines eligibility
   outcome directly. curr-us-002 now has corroborated (two independent
   sources) secondary numbers and a provisional computed verdict; verify
   it against the current official page before trusting it further.
   curr-uk-001/curr-in-001 remain fully open (no numbers added yet).
6. **Whether the 2017-2018-scoped exception path is still active**
   (except-001/002/003) — could misrepresent appeal options as currently
   available when they may not be.
7. **Whether a formal appeals process exists** (except-004).
