"""Loads and validates application settings from environment variables.

Reads Azure OpenAI configuration from the environment (populated via a local
.env file in development, real environment variables in any deployed
setting). No secrets are hardcoded here or anywhere else in the codebase --
see CLAUDE.md rule 4.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel, SecretStr

load_dotenv()

_REQUIRED_VARS = (
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
    "EMBEDDING_DEPLOYMENT",
)


class Settings(BaseModel):
    azure_openai_endpoint: str
    azure_openai_api_key: SecretStr
    azure_openai_deployment: str
    embedding_deployment: str


def get_settings() -> Settings:
    """Build a validated Settings instance from environment variables.

    Raises a RuntimeError naming any missing variables instead of failing
    with an opaque KeyError, in keeping with CLAUDE.md rule 3 (never fail
    silently -- say what's missing and name the next step).
    """
    missing = [name for name in _REQUIRED_VARS if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in your Azure OpenAI credentials."
        )
    return Settings(
        azure_openai_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        azure_openai_api_key=os.environ["AZURE_OPENAI_API_KEY"],
        azure_openai_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        embedding_deployment=os.environ["EMBEDDING_DEPLOYMENT"],
    )
