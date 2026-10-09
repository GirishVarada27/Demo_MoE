"""One-off ingestion script.

Chunks the source rule documents in knowledge/equivalency_rules, embeds
them via the Azure OpenAI embedding deployment (EMBEDDING_DEPLOYMENT), and
pushes the resulting vectors into the Azure AI Search index that
tools/search_rules.py queries at runtime. Not part of the request-time
agent loop -- run manually whenever the source rule documents change.
"""
