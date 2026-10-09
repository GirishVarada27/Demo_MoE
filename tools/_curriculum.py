"""Shared curriculum-family classification.

Knowledge chunks and submitted documents only carry free-text curriculum
descriptions (e.g. "American (US High School Diploma)", "GCE Advanced
Level"), not a structured curriculum field. check_documents and
evaluate_eligibility both need to compare curriculum strings against each
other and against chunk text, so the keyword mapping lives here once
rather than risking the two tools disagreeing on what counts as a match.
"""

from __future__ import annotations

_FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "american": ("american", "us high school"),
    "british": ("british", "gce", "a-level", "a level", "igcse", "gcse"),
    "ib": ("international baccalaureate", " ib "),
    "indian": ("indian", "cbse", "icse"),
    "waec": ("waec", "west african"),
}


def classify_curriculum_family(text: str) -> str | None:
    """Return a coarse curriculum family for text, or None if unrecognized."""
    lowered = f" {text.lower()} "
    for family, keywords in _FAMILY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return family
    return None
