"""OpenAI/Azure-OpenAI-compatible mock model server for load tests.

Stands in for the real model provider so load tests are free, don't hit
provider rate limits, and are repeatable: latency is drawn from a seeded
RNG, and replies follow a per-app JSON script. Stdlib only.

Routes (POST unless noted):
  /openai/deployments/<deployment>/chat/completions   (Azure)
  /openai/deployments/<deployment>/embeddings         (Azure)
  /v1/chat/completions, /chat/completions             (OpenAI)
  /v1/embeddings, /embeddings                         (OpenAI)
  GET /health

Script file (all keys optional):
  {
    "ignore_user_prefixes": ["Your draft answer failed validation"],
    "tool_steps": [{"tool": "get_applicant_record", "args": {"applicant_id": "{{user:APP-\\d+}}"}}],
    "final_answer": "text returned once all tool_steps are done",
    "no_tools_reply": "reply when the request offers no tools (e.g. a verifier call)"
  }
A turn = everything after the last user message not starting with an
ignored prefix; the next tool step is the number of assistant tool-call
messages so far in that turn. Templates in args/answers:
  {{user:REGEX}}               first match in that user message ("" if none)
  {{tool:NAME:dot.path}}       field from the latest NAME tool result (JSON)

Usage: python mock_llm_server.py --port 18080 [--script FILE]
         [--chat-latency-ms 300] [--chat-jitter-ms 100] [--embed-latency-ms 30] [--seed 42]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_TEMPLATE = re.compile(r"\{\{(user|tool):(.+?)\}\}")
_EMBED_DIM = 256


class Mock:
    def __init__(self, script: dict, chat_ms: float, jitter_ms: float, embed_ms: float, seed: int):
        self.script = script
        self.chat_ms, self.jitter_ms, self.embed_ms = chat_ms, jitter_ms, embed_ms
        self._rng = random.Random(seed)
        self._rng_lock = threading.Lock()
        self.counts = {"chat": 0, "embeddings": 0}

    def _sleep(self, base_ms: float, jitter_ms: float) -> None:
        with self._rng_lock:
            delay = max(0.0, self._rng.gauss(base_ms, jitter_ms / 2) if jitter_ms else base_ms)
        time.sleep(delay / 1000)

    # --- chat ---------------------------------------------------------------

    def _turn(self, messages: list[dict]) -> tuple[str, list[dict]]:
        ignore = tuple(self.script.get("ignore_user_prefixes", []))
        start = 0
        for i, m in enumerate(messages):
            if m.get("role") == "user" and not str(m.get("content") or "").startswith(ignore):
                start = i
        user_text = str(messages[start].get("content") or "") if messages else ""
        return user_text, messages[start + 1 :]

    def _render(self, value, user_text: str, turn: list[dict]):
        if isinstance(value, dict):
            return {k: self._render(v, user_text, turn) for k, v in value.items()}
        if isinstance(value, list):
            return [self._render(v, user_text, turn) for v in value]
        if not isinstance(value, str):
            return value

        def sub(match: re.Match) -> str:
            kind, expr = match.groups()
            if kind == "user":
                found = re.search(expr, user_text)
                return found.group(0) if found else ""
            name, _, path = expr.partition(":")
            return str(_tool_field(turn, name, path) or "")

        return _TEMPLATE.sub(sub, value)

    def chat(self, body: dict) -> dict:
        self.counts["chat"] += 1
        self._sleep(self.chat_ms, self.jitter_ms)
        messages = body.get("messages", [])
        if not body.get("tools"):
            return _completion(self.script.get("no_tools_reply", "PASS"))
        user_text, turn = self._turn(messages)
        step = sum(1 for m in turn if m.get("role") == "assistant" and m.get("tool_calls"))
        steps = self.script.get("tool_steps", [])
        if step < len(steps):
            spec = steps[step]
            args = self._render(spec.get("args", {}), user_text, turn)
            return _completion(None, [{"id": f"call_{step}_{time.time_ns()}", "type": "function",
                                       "function": {"name": spec["tool"], "arguments": json.dumps(args)}}])
        answer = self.script.get("final_answer", "Verified: None.\nAssumed: None.\nSimulated: None.\nNext step: none.")
        return _completion(self._render(answer, user_text, turn))

    # --- embeddings -----------------------------------------------------------

    def embeddings(self, body: dict) -> dict:
        self.counts["embeddings"] += 1
        self._sleep(self.embed_ms, 0)
        inputs = body.get("input", [])
        inputs = [inputs] if isinstance(inputs, str) else inputs
        data = [{"object": "embedding", "index": i, "embedding": _embed(str(t))} for i, t in enumerate(inputs)]
        return {"object": "list", "data": data, "model": body.get("model", "mock-embedding"),
                "usage": {"prompt_tokens": 0, "total_tokens": 0}}


def _tool_field(turn: list[dict], name: str, path: str):
    ids = {tc["id"]: tc["function"]["name"] for m in turn if m.get("role") == "assistant" for tc in m.get("tool_calls") or []}
    for m in reversed(turn):
        if m.get("role") == "tool" and ids.get(m.get("tool_call_id")) == name:
            try:
                value = json.loads(m.get("content") or "{}")
            except json.JSONDecodeError:
                return None
            for key in filter(None, path.split(".")):
                value = value.get(key) if isinstance(value, dict) else None
            return value
    return None


def _embed(text: str) -> list[float]:
    """Deterministic bag-of-words hash vector: same text -> same vector, and
    texts sharing words score as similar, so retrieval still behaves."""
    vec = [0.0] * _EMBED_DIM
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        h = int.from_bytes(hashlib.blake2b(word.encode(), digest_size=4).digest(), "little")
        vec[h % _EMBED_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _completion(content: str | None, tool_calls: list | None = None) -> dict:
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": f"mock-{time.time_ns()}", "object": "chat.completion", "created": int(time.time()), "model": "mock",
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def make_handler(mock: Mock):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep load-test logs quiet
            pass

        def _send(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.split("?")[0] == "/health":
                self._send(200, {"status": "ok", **mock.counts})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?")[0]
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if path.endswith("/chat/completions"):
                self._send(200, mock.chat(body))
            elif path.endswith("/embeddings"):
                self._send(200, mock.embeddings(body))
            else:
                self._send(404, {"error": f"no mock route for {path}"})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--script", help="JSON reply script (see module docstring)")
    parser.add_argument("--chat-latency-ms", type=float, default=300)
    parser.add_argument("--chat-jitter-ms", type=float, default=100)
    parser.add_argument("--embed-latency-ms", type=float, default=30)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    script = json.loads(open(args.script, encoding="utf-8").read()) if args.script else {}
    mock = Mock(script, args.chat_latency_ms, args.chat_jitter_ms, args.embed_latency_ms, args.seed)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(mock))
    server.daemon_threads = True
    print(f"mock LLM listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
