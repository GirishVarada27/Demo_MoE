"""Bearer-token authentication for the HTTP API.

Each token identifies exactly one applicant. Tokens are never stored: the
MOE_API_TOKENS environment variable holds a JSON object mapping the SHA-256
hex digest of each token to its applicant id, e.g.
    MOE_API_TOKENS={"9f86d08...": "APP-001"}
With no tokens configured, every protected request is refused (fail closed).

Issue a token for an applicant:
    python -m api.auth new-token APP-001
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys

from fastapi import Header, HTTPException


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_map() -> dict[str, str]:
    raw = os.environ.get("MOE_API_TOKENS", "").strip()
    if not raw:
        return {}
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("MOE_API_TOKENS must be a JSON object of {sha256(token): applicant_id}.") from exc
    return {str(k).lower(): str(v) for k, v in mapping.items()}


def authenticated_applicant(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency: the applicant id the caller's bearer token belongs to."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Missing bearer token.", headers={"WWW-Authenticate": "Bearer"})
    applicant = _token_map().get(token_hash(token.strip()))
    if applicant is None:
        raise HTTPException(status_code=401, detail="Invalid token.", headers={"WWW-Authenticate": "Bearer"})
    return applicant


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "new-token":
        print("usage: python -m api.auth new-token <APPLICANT_ID>", file=sys.stderr)
        return 2
    token = secrets.token_urlsafe(32)
    print(f"token (give to the applicant's client; shown once): {token}")
    print(f"MOE_API_TOKENS entry: {json.dumps({token_hash(token): argv[1]})}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
