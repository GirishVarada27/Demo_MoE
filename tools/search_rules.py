"""Retrieval tool: search_requirements.

Embeds the query (folding the curriculum hint into the embedded text, since
knowledge chunks carry no structured curriculum field -- see
tools/_curriculum.py) via the Azure OpenAI embedding deployment, embeds
every chunk under knowledge/equivalency_rules, and ranks by cosine
similarity. The embedding call itself is isolated in _embed() so tests can
monkeypatch it instead of hitting live Azure OpenAI.
"""

from __future__ import annotations

import json
from pathlib import Path

import config
from schemas import RuleChunk, RuleChunkMatch, SearchRequirementsResult

_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "equivalency_rules"
_EMBEDDING_API_VERSION = "2024-02-01"

SEARCH_REQUIREMENTS_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_requirements",
        "description": (
            "Search the indexed public equivalency-service rules (eligibility, "
            "required documents, attestation, curriculum-specific requirements, "
            "fees/timelines, exceptions) for the passage most relevant to a "
            "question. Returns ranked rule chunks with source citations and "
            "OFFICIAL/ASSUMPTION labels."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language question or topic to search for.",
                },
                "curriculum": {
                    "type": ["string", "null"],
                    "description": "Curriculum hint (e.g. 'American', 'British (GCE)') to bias retrieval, or null if not curriculum-specific.",
                },
                "simulate_failure": {
                    "type": "boolean",
                    "description": "If true, return a simulated failure instead of searching.",
                    "default": False,
                },
            },
            "required": ["query"],
        },
    },
}


def load_rule_chunks(topics: list[str] | None = None) -> list[RuleChunk]:
    """Load every knowledge chunk, optionally filtered to a set of topics."""
    chunks: list[RuleChunk] = []
    for path in sorted(_KNOWLEDGE_DIR.glob("*.json")):
        for entry in json.loads(path.read_text(encoding="utf-8")):
            chunk = RuleChunk(**entry)
            if topics is None or chunk.topic in topics:
                chunks.append(chunk)
    return chunks


def _embed(texts: list[str]) -> list[list[float]]:
    from openai import AzureOpenAI

    settings = config.get_settings()
    client = AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        api_version=_EMBEDDING_API_VERSION,
    )
    response = client.embeddings.create(model=settings.embedding_deployment, input=texts)
    return [item.embedding for item in response.data]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


MIN_RELEVANCE_SCORE = 0.3


def search_requirements(
    query: str,
    curriculum: str | None = None,
    top_k: int = 5,
    simulate_failure: bool = False,
) -> SearchRequirementsResult:
    """Embeddings search over the knowledge base. Returns typed, ranked results.

    Only returns chunks scoring at or above MIN_RELEVANCE_SCORE -- otherwise
    an unrelated query would still get back the "best available" chunks
    even when none are actually relevant, which the agent could mistake for
    real grounding (CLAUDE.md rule 3). An empty result here is the correct,
    honest answer when nothing in the knowledge base actually matches.
    """
    if simulate_failure:
        return SearchRequirementsResult(error="Simulated search failure (simulate_failure=True).")

    chunks = load_rule_chunks()
    if not chunks:
        return SearchRequirementsResult(error="Knowledge base is empty.")

    search_text = query if not curriculum else f"{query} ({curriculum})"
    try:
        query_vector, *chunk_vectors = _embed([search_text, *(c.text for c in chunks)])
    except Exception as exc:  # pragma: no cover - network/config failure path
        return SearchRequirementsResult(error=f"Embedding request failed: {exc}")

    scored = sorted(
        (RuleChunkMatch(chunk=chunk, score=round(_cosine(query_vector, vector), 4)) for chunk, vector in zip(chunks, chunk_vectors)),
        key=lambda match: match.score,
        reverse=True,
    )
    relevant = [match for match in scored if match.score >= MIN_RELEVANCE_SCORE]
    return SearchRequirementsResult(results=relevant[:top_k])
