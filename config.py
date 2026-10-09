"""Loads and validates application settings from environment variables.

Reads model-client configuration from the environment (populated via a
local .env file in development, real environment variables in any deployed
setting). No secrets are hardcoded here or anywhere else in the codebase --
see CLAUDE.md rule 4.

Two mutually exclusive chat-client modes, selected by whether
AZURE_OPENAI_ENDPOINT is set:
  - Azure mode (AZURE_OPENAI_ENDPOINT set): requires AZURE_OPENAI_API_KEY
    and AZURE_OPENAI_DEPLOYMENT too. Also the only mode search_requirements'
    embeddings call supports, via EMBEDDING_DEPLOYMENT -- that's unaffected
    by which chat client is in use.
  - Plain OpenAI mode (AZURE_OPENAI_ENDPOINT unset): requires
    OPENAI_API_KEY and OPENAI_MODEL instead.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel, SecretStr

load_dotenv()

_AZURE_REQUIRED_VARS = (
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
    "EMBEDDING_DEPLOYMENT",
)
_OPENAI_REQUIRED_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)


class Settings(BaseModel):
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_deployment: str | None = None
    embedding_deployment: str | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None


def get_settings() -> Settings:
    """Build a validated Settings instance from environment variables.

    Raises a RuntimeError naming any missing variables instead of failing
    with an opaque KeyError, in keeping with CLAUDE.md rule 3 (never fail
    silently -- say what's missing and name the next step).
    """
    if os.environ.get("AZURE_OPENAI_ENDPOINT"):
        missing = [name for name in _AZURE_REQUIRED_VARS if not os.environ.get(name)]
        if missing:
            raise RuntimeError(
                f"Missing required environment variables for Azure OpenAI: {', '.join(missing)}. "
                "Copy .env.example to .env and fill in your Azure OpenAI credentials."
            )
        return Settings(
            azure_openai_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            azure_openai_api_key=os.environ["AZURE_OPENAI_API_KEY"],
            azure_openai_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            embedding_deployment=os.environ["EMBEDDING_DEPLOYMENT"],
        )

    missing = [name for name in _OPENAI_REQUIRED_VARS if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables for OpenAI: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in your OpenAI credentials, or set "
            "AZURE_OPENAI_ENDPOINT (plus AZURE_OPENAI_API_KEY and AZURE_OPENAI_DEPLOYMENT) "
            "to use Azure OpenAI instead."
        )
    return Settings(
        openai_api_key=os.environ["OPENAI_API_KEY"],
        openai_model=os.environ["OPENAI_MODEL"],
    )
