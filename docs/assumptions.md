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
**To confirm:** find the official criteria document referenced as
"Ministerial Decision No. 4443 of 2001" or its current successor, which
should state baseline thresholds directly.

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
5. **Curriculum-specific score thresholds** (curr-us-002, curr-uk-001,
   curr-in-001) — determines eligibility outcome directly.
6. **Whether the 2017-2018-scoped exception path is still active**
   (except-001/002/003) — could misrepresent appeal options as currently
   available when they may not be.
7. **Whether a formal appeals process exists** (except-004).
