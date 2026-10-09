# knowledge/

Source documents for the public service rules the agent retrieves against,
modeled on the UAE Ministry of Education's public "Equivalency of
Certificates" service for Grade 12 certificates completed abroad. Files
under `equivalency_rules/` are chunked JSON (`id`, `topic`, `text`,
`source_url`, `label`), one file per topic area (eligibility, required
documents, attestation, curriculum-specific requirements, fees and
timelines, exceptions). `build_index.py` embeds these chunks and pushes
them to the Azure AI Search index that `tools/search_rules.py` queries at
runtime.

Every chunk is labeled `"label": "OFFICIAL"` (grounded in a real
moe.gov.ae/u.ae page, cited via `source_url`) or `"label": "ASSUMPTION"`
(no official source found; `source_url` is `null`). See
`docs/assumptions.md` for the full list of assumptions and what should be
verified against the live MoE website before this moves past prototype
status.
