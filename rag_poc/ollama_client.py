import json
import os
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_BASE_URL = "http://10.32.145.143:11434"


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


def generate(prompt: str, model: str, timeout: int = 240) -> str:
    response = _post_json(
        "/api/generate",
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
        },
        timeout=timeout,
    )
    return response.get("response", "").strip()


def embed(text: str, model: str, timeout: int = 180) -> list[float]:
    response = _post_json(
        "/api/embed",
        {
            "model": model,
            "input": text,
        },
        timeout=timeout,
    )

    embeddings = response.get("embeddings")
    if embeddings and isinstance(embeddings, list):
        return embeddings[0]

    # Older Ollama-compatible endpoint shape.
    response = _post_json(
        "/api/embeddings",
        {
            "model": model,
            "prompt": text,
        },
        timeout=timeout,
    )
    embedding = response.get("embedding")
    if embedding:
        return embedding

    raise OllamaError("Ollama did not return an embedding.")
