"""OpenAI-compatible client for PaperBot's llama.cpp generation and embedding servers."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_CHAT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_EMBED_BASE_URL = "http://127.0.0.1:8081/v1"
DEFAULT_QUERY_INSTRUCTION = (
    "Instruct: Retrieve the scientific paper passage that best answers the research question.\n"
    "Query: "
)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(Path(__file__).resolve().parents[1] / ".env")


class ModelServerError(RuntimeError):
    pass


# Keep the established exception import working while PaperBot migrates from Ollama.
OllamaError = ModelServerError


def _base_url(kind: str) -> str:
    name = "LLAMA_CHAT_BASE_URL" if kind == "chat" else "LLAMA_EMBED_BASE_URL"
    default = DEFAULT_CHAT_BASE_URL if kind == "chat" else DEFAULT_EMBED_BASE_URL
    return os.environ.get(name, default).rstrip("/")


def _post_json(kind: str, path: str, payload: dict, timeout: int) -> dict:
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("LLAMA_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        f"{_base_url(kind)}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ModelServerError(f"llama.cpp HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ModelServerError(f"llama.cpp connection failed: {exc}") from exc


def _number(name: str, fallback: str, cast: type[int] | type[float]):
    try:
        return cast(os.environ.get(name, fallback))
    except ValueError:
        return cast(fallback)


def generate(prompt: str, model: str, timeout: int = 240) -> str:
    temperature = max(0.0, _number("LLAMA_TEMPERATURE", "0.2", float))
    max_tokens = max(64, _number("LLAMA_MAX_TOKENS", "1024", int))
    reasoning_budget = max(0, _number("LLAMA_REASONING_BUDGET", "0", int))
    response = _post_json(
        "chat",
        "/chat/completions",
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "top_p": 0.8,
            "top_k": 20,
            "max_tokens": max_tokens,
            "stream": False,
            "reasoning_budget": reasoning_budget,
            "chat_template_kwargs": {"enable_thinking": reasoning_budget > 0},
        },
        timeout,
    )
    message = (response.get("choices") or [{}])[0].get("message") or {}
    return str(message.get("content") or "").strip()


def _embed_many_raw(texts: list[str], model: str, timeout: int) -> list[list[float]]:
    if not texts:
        return []
    response = _post_json(
        "embedding",
        "/embeddings",
        {"model": model, "input": texts, "encoding_format": "float"},
        timeout,
    )
    rows = sorted(response.get("data") or [], key=lambda row: row.get("index", 0))
    vectors = [row.get("embedding") for row in rows]
    if len(vectors) != len(texts) or any(not vector for vector in vectors):
        raise ModelServerError("llama.cpp did not return every requested embedding.")
    return vectors


def embed_many(texts: list[str], model: str, timeout: int = 180) -> list[list[float]]:
    """Embed retrieval queries with the instruction expected by Qwen3 Embedding."""
    instruction = os.environ.get("LLAMA_EMBED_QUERY_INSTRUCTION", DEFAULT_QUERY_INSTRUCTION)
    queries = [f"{instruction}{text}" for text in texts]
    return _embed_many_raw(queries, model, timeout)


def embed(text: str, model: str, timeout: int = 180) -> list[float]:
    vectors = embed_many([text], model, timeout=timeout)
    return vectors[0]


def embed_document(text: str, model: str, timeout: int = 180) -> list[float]:
    """Embed a corpus passage without the query-only retrieval instruction."""
    return _embed_many_raw([text], model, timeout)[0]
