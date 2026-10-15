"""Check that Azure OpenAI chat and embedding deployments work.

Usage (from the project root, with the venv active):
    python scripts/check_azure.py

Reads these variables from .env (or the environment):
    AZURE_OPENAI_ENDPOINT      e.g. https://moe-agent123.openai.azure.com/
    AZURE_OPENAI_API_KEY
    AZURE_OPENAI_DEPLOYMENT    chat deployment name, e.g. gpt-4.1-mini
    EMBEDDING_DEPLOYMENT       embedding deployment name, e.g. text-embedding-3-small
    AZURE_OPENAI_API_VERSION   optional, defaults to 2024-10-21
"""

import os
import sys

try:
    from dotenv import load_dotenv
except ImportError:
    print("[FAIL] python-dotenv is not installed. Run: pip install python-dotenv")
    sys.exit(1)

try:
    from openai import AzureOpenAI
except ImportError:
    print("[FAIL] openai is not installed. Run: pip install openai")
    sys.exit(1)

DEFAULT_API_VERSION = "2024-10-21"


def describe_error(exc: Exception) -> str:
    """Return the most useful exact error text available."""
    parts = [f"{type(exc).__name__}: {exc}"]
    status = getattr(exc, "status_code", None)
    if status is not None:
        parts.append(f"HTTP status: {status}")
    body = getattr(exc, "body", None)
    if body:
        parts.append(f"Response body: {body}")
    return "\n       ".join(parts)


def hint_for(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status == 401 or "invalid subscription key" in text or "incorrect api key" in text:
        return "Hint: wrong API key, or the key belongs to a different resource than the endpoint."
    if status == 404 or "deploymentnotfound" in text:
        return "Hint: the deployment name does not match the portal. Use the deployment name, not the model name."
    if status == 429:
        return "Hint: rate limit or quota exceeded. Lower request volume or raise the deployment's tokens per minute."
    if "connection" in text or "getaddrinfo" in text or "timed out" in text:
        return "Hint: network problem. Check the endpoint URL and your internet or proxy settings."
    return ""


def main() -> int:
    load_dotenv()

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    chat_deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
    embedding_deployment = os.environ.get("EMBEDDING_DEPLOYMENT", "").strip()
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "").strip() or DEFAULT_API_VERSION

    missing = [
        name
        for name, value in [
            ("AZURE_OPENAI_ENDPOINT", endpoint),
            ("AZURE_OPENAI_API_KEY", api_key),
            ("AZURE_OPENAI_DEPLOYMENT", chat_deployment),
            ("EMBEDDING_DEPLOYMENT", embedding_deployment),
        ]
        if not value
    ]
    if missing:
        print("[FAIL] Missing settings in .env: " + ", ".join(missing))
        return 1

    # The AzureOpenAI client wants the base address only.
    for suffix in ("/openai/v1/", "/openai/v1"):
        if endpoint.endswith(suffix):
            endpoint = endpoint[: -len(suffix)] + "/"
            print(f"[note] Trimmed '{suffix}' from the endpoint; using {endpoint}")
            break

    print("Settings")
    print(f"  endpoint:   {endpoint}")
    print(f"  api key:    {'*' * 8}{api_key[-4:] if len(api_key) > 4 else ''}")
    print(f"  chat:       {chat_deployment}")
    print(f"  embedding:  {embedding_deployment}")
    print(f"  api version:{' ' + api_version}")
    print()

    client = AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
        max_retries=1,
        timeout=30,
    )

    failures = 0

    print("Chat check")
    try:
        response = client.chat.completions.create(
            model=chat_deployment,
            messages=[{"role": "user", "content": "say ok"}],
            max_completion_tokens=20,
        )
        reply = (response.choices[0].message.content or "").strip()
        print(f"[OK]   Chat deployment '{chat_deployment}' replied: {reply!r}")
    except Exception as exc:  # noqa: BLE001 - we want the exact error shown
        failures += 1
        print(f"[FAIL] Chat deployment '{chat_deployment}'\n       {describe_error(exc)}")
        hint = hint_for(exc)
        if hint:
            print(f"       {hint}")
    print()

    print("Embedding check")
    try:
        response = client.embeddings.create(model=embedding_deployment, input="hello")
        dimensions = len(response.data[0].embedding)
        print(f"[OK]   Embedding deployment '{embedding_deployment}' returned a vector of {dimensions} dimensions")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"[FAIL] Embedding deployment '{embedding_deployment}'\n       {describe_error(exc)}")
        hint = hint_for(exc)
        if hint:
            print(f"       {hint}")
    print()

    if failures:
        print(f"Result: {failures} check(s) failed.")
        return 1
    print("Result: all checks passed. Azure is ready for the demo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())