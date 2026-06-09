#!/usr/bin/env python3
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_BASE_URL = "http://10.32.145.143:11434"
DEFAULT_PROMPT = (
    "Persistent Spin Helixを日本語で3文で説明してください。"
    "Rashba, Dresselhaus, SU(2), spin-orbit interactionは英語表記のまま使ってください。"
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


def post_json(base_url: str, path: str, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama connection failed: {exc}") from exc


def seconds(ns: int | float | None) -> float:
    return float(ns or 0) / 1_000_000_000


def tokens_per_second(tokens: int | None, duration_ns: int | float | None) -> float:
    duration = seconds(duration_ns)
    if not tokens or duration <= 0:
        return 0.0
    return float(tokens) / duration


def benchmark_model(base_url: str, model: str, prompt: str, timeout: int) -> dict:
    started = time.monotonic()
    response = post_json(
        base_url,
        "/api/generate",
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_predict": 256,
            },
        },
        timeout=timeout,
    )
    wall_seconds = time.monotonic() - started

    text = response.get("response", "")
    eval_count = response.get("eval_count") or 0
    eval_duration = response.get("eval_duration") or 0
    prompt_eval_count = response.get("prompt_eval_count") or 0
    prompt_eval_duration = response.get("prompt_eval_duration") or 0

    return {
        "model": model,
        "wall_s": wall_seconds,
        "load_s": seconds(response.get("load_duration")),
        "prompt_tokens": prompt_eval_count,
        "prompt_tps": tokens_per_second(prompt_eval_count, prompt_eval_duration),
        "gen_tokens": eval_count,
        "gen_s": seconds(eval_duration),
        "gen_tps": tokens_per_second(eval_count, eval_duration),
        "total_s": seconds(response.get("total_duration")) or wall_seconds,
        "response_preview": " ".join(text.split())[:120],
    }


def print_table(rows: list[dict]) -> None:
    headers = [
        "model",
        "run",
        "wall_s",
        "load_s",
        "prompt_tps",
        "gen_tokens",
        "gen_s",
        "gen_tps",
    ]
    widths = {header: len(header) for header in headers}
    formatted_rows = []
    for row in rows:
        formatted = {
            "model": row["model"],
            "run": str(row["run"]),
            "wall_s": f"{row['wall_s']:.2f}",
            "load_s": f"{row['load_s']:.2f}",
            "prompt_tps": f"{row['prompt_tps']:.1f}",
            "gen_tokens": str(row["gen_tokens"]),
            "gen_s": f"{row['gen_s']:.2f}",
            "gen_tps": f"{row['gen_tps']:.1f}",
        }
        for key, value in formatted.items():
            widths[key] = max(widths[key], len(value))
        formatted_rows.append(formatted)

    print("  ".join(header.ljust(widths[header]) for header in headers))
    print("  ".join("-" * widths[header] for header in headers))
    for row in formatted_rows:
        print("  ".join(row[header].ljust(widths[header]) for header in headers))


def print_summary(rows: list[dict]) -> None:
    by_model: dict[str, list[dict]] = {}
    for row in rows:
        by_model.setdefault(row["model"], []).append(row)

    print("\nAverage generation speed")
    for model, model_rows in by_model.items():
        speeds = [row["gen_tps"] for row in model_rows if row["gen_tps"] > 0]
        avg = sum(speeds) / len(speeds) if speeds else 0.0
        print(f"- {model}: {avg:.1f} tokens/sec")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    load_env_file(root / ".env")

    parser = argparse.ArgumentParser(description="Benchmark Ollama model generation speed.")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL),
        help="Ollama base URL.",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=(os.environ.get("OLLAMA_BENCH_MODELS") or os.environ.get("OLLAMA_CHAT_MODEL") or "qwen3:8b").split(","),
        help="Models to benchmark, separated by spaces.",
    )
    parser.add_argument("--runs", type=int, default=2, help="Runs per model.")
    parser.add_argument("--timeout", type=int, default=600, help="HTTP timeout seconds.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Benchmark prompt.")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of a table.")
    args = parser.parse_args()

    rows = []
    for model in args.models:
        model = model.strip()
        if not model:
            continue
        for run in range(1, args.runs + 1):
            print(f"Running {model} run {run}/{args.runs}...", flush=True)
            row = benchmark_model(args.base_url, model, args.prompt, args.timeout)
            row["run"] = run
            rows.append(row)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print()
        print_table(rows)
        print_summary(rows)


if __name__ == "__main__":
    main()
