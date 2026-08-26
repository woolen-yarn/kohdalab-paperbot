import json
import os
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_BASE_URL = "http://rtx-pc.example.invalid:11434"


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


class OllamaError(RuntimeError):
    pass


def base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _post_json(path: str, payload: dict, timeout: int = 180) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url()}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise OllamaError(f"Ollama HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise OllamaError(f"Ollama connection failed: {exc}") from exc


def _generation_payload(model: str) -> dict:
    """Return shared generation settings for both Ollama endpoints."""
    try:
        num_ctx = max(1024, int(os.environ.get("OLLAMA_NUM_CTX", "8192")))
    except ValueError:
        num_ctx = 8192
    try:
        temperature = max(0.0, float(os.environ.get("OLLAMA_TEMPERATURE", "0.2")))
    except ValueError:
        temperature = 0.2

    raw_think = os.environ.get("OLLAMA_THINK", "false").strip().lower()
    if model.lower().startswith("gpt-oss"):
        # Ollama's gpt-oss family supports reasoning levels but not think=false.
        think: bool | str = raw_think if raw_think in {"low", "medium", "high"} else "low"
    elif raw_think in {"low", "medium", "high"}:
        think = raw_think
    else:
        think = raw_think in {"1", "true", "yes", "on"}

    return {
        "model": model,
        "stream": False,
        "think": think,
        "keep_alive": os.environ.get("OLLAMA_KEEP_ALIVE", "10m").strip() or "10m",
        "options": {
            "num_ctx": num_ctx,
            "temperature": temperature,
        },
    }


def generate(prompt: str, model: str, timeout: int = 240) -> str:
    payload = _generation_payload(model)
    payload["prompt"] = prompt
    response = _post_json(
        "/api/generate",
        payload,
        timeout=timeout,
    )
    text = response.get("response", "").strip()
    if text:
        return text

    # Some chat-tuned models are more reliable through Ollama's chat endpoint.
    payload = _generation_payload(model)
    payload["messages"] = [{"role": "user", "content": prompt}]
    response = _post_json(
        "/api/chat",
        payload,
        timeout=timeout,
    )
    message = response.get("message") or {}
    return message.get("content", "").strip()


def embed_many(texts: list[str], model: str, timeout: int = 180) -> list[list[float]]:
    if not texts:
        return []
    response = _post_json(
        "/api/embed",
        {
            "model": model,
            "input": texts,
        },
        timeout=timeout,
    )

    embeddings = response.get("embeddings")
    if isinstance(embeddings, list) and len(embeddings) == len(texts):
        return embeddings

    # Older Ollama-compatible endpoint shape does not support batches.
    legacy_embeddings = []
    for text in texts:
        response = _post_json(
            "/api/embeddings",
            {
                "model": model,
                "prompt": text,
            },
            timeout=timeout,
        )
        embedding = response.get("embedding")
        if not embedding:
            raise OllamaError("Ollama did not return an embedding.")
        legacy_embeddings.append(embedding)
    return legacy_embeddings


def embed(text: str, model: str, timeout: int = 180) -> list[float]:
    embeddings = embed_many([text], model, timeout=timeout)
    if embeddings:
        return embeddings[0]

    raise OllamaError("Ollama did not return an embedding.")
