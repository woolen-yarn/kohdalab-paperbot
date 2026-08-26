#!/usr/bin/env python3
"""Run a quality-focused PaperBot benchmark through a remote llama.cpp server."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from evaluate_generation_models import EvalCase, build_cases, score_case

from rag_poc.ask import build_prompt


def build_quality_cases() -> list[EvalCase]:
    """Add scientific knowledge, reasoning, calibration, and longer retrieval cases."""
    knowledge = [
        EvalCase(
            "knowledge_spin_hall_mechanisms",
            "knowledge",
            (
                "スピンホール効果のintrinsic機構とextrinsic機構を区別し、extrinsicの2機構も含めて"
                "日本語で正確に説明してください。専門用語は英語を併記してください。"
            ),
            max_tokens=768,
            required=["Berry curvature", "skew scattering", "side jump"],
            forbidden=["Lorentz force is the intrinsic origin"],
        ),
        EvalCase(
            "knowledge_spin_relaxation",
            "knowledge",
            (
                "D'yakonov-Perel'機構とElliott-Yafet機構について、運動量散乱時間を変えた時の"
                "spin relaxation rateの依存性が逆になる理由を説明してください。"
            ),
            max_tokens=768,
            required=["D'yakonov-Perel", "Elliott-Yafet", "motional narrowing|運動の先鋭化", "spin mixing|スピン混合"],
        ),
        EvalCase(
            "knowledge_trkr",
            "knowledge",
            (
                "time-resolved Kerr rotationで半導体中のspin dynamicsを測る原理を、pump、probe、"
                "時間遅延、Kerr rotationが表す物理量を含めて説明してください。"
            ),
            max_tokens=768,
            required=["pump", "probe", "delay|時間遅延", "spin polarization|スピン偏極", "Kerr rotation|Kerr回転"],
        ),
        EvalCase(
            "knowledge_psh_limitations",
            "knowledge",
            (
                "Rashba係数alphaとlinear/cubic Dresselhaus係数beta_1, beta_3を区別し、"
                "alpha=beta_1付近でPSH寿命が有限になる主因を説明してください。"
            ),
            max_tokens=768,
            required=["alpha", "beta_1", "beta_3", "SU(2)", "finite|有限"],
        ),
        EvalCase(
            "knowledge_relaxation_scaling_json",
            "knowledge",
            (
                "D'yakonov-Perel' (DP) と Elliott-Yafet (EY) のspin relaxation rateが"
                "momentum scattering time tau_pの何に比例するかを答えてください。"
                "説明文やMarkdownを付けず、次の値だけを持つJSONにしてください: "
                '{"dp_rate":"proportional_to_tau_p または proportional_to_inverse_tau_p",'
                '"ey_rate":"proportional_to_tau_p または proportional_to_inverse_tau_p"}'
            ),
            max_tokens=256,
            required=["proportional_to_tau_p", "proportional_to_inverse_tau_p"],
            expected_json={
                "dp_rate": "proportional_to_tau_p",
                "ey_rate": "proportional_to_inverse_tau_p",
            },
        ),
        EvalCase(
            "knowledge_psh_identity_json",
            "knowledge",
            (
                "PSHの正式名称、理想的なlinear SOIの成立条件、寿命を有限にする代表的なcubic項を"
                "説明文やMarkdownなしのJSONで答えてください。キーと正解値の形式は "
                '{"psh":"...","ideal_condition":"...","finite_lifetime_term":"..."}。'
                "係数名はalpha, beta, beta3を使ってください。"
            ),
            max_tokens=256,
            required=["Persistent Spin Helix", "alpha_equals_beta", "cubic_Dresselhaus_beta3"],
            expected_json={
                "psh": "Persistent Spin Helix",
                "ideal_condition": "alpha_equals_beta",
                "finite_lifetime_term": "cubic_Dresselhaus_beta3",
            },
        ),
    ]

    reasoning = [
        EvalCase(
            "reasoning_spin_diffusion_length",
            "reasoning",
            (
                "スピン拡散係数D=0.010 m^2/s、スピン寿命tau_s=4.0 nsとする。"
                "L_s=sqrt(D*tau_s)を計算し、途中のSI単位変換と最終結果をmicrometerで示してください。"
            ),
            max_tokens=512,
            required=["4.0 × 10^-9|4.0 \\times 10^{-9}", "4.0 × 10^-11|4.0 \\times 10^{-11}", "6.3", "µm|μm|\\mu\\text{m}"],
            forbidden=["20 µm", "0.2 µm"],
        ),
        EvalCase(
            "reasoning_separate_soi_terms",
            "reasoning",
            (
                "GaAs quantum wellでRashbaとDresselhaus項を実験的に分離したい。gate電圧、"
                "結晶方位依存性、TRKRまたはspin-gratingを使う対照実験を、観測量と判定基準まで含めて設計してください。"
            ),
            max_tokens=768,
            required=["gate|ゲート", "[110]", "[1-10]|[1\\bar{1}0]", "TRKR", "anisotropy|異方"],
        ),
        EvalCase(
            "reasoning_conflicting_sources",
            "reasoning",
            build_prompt(
                "sample Xの15 Kにおけるspin lifetimeはいくつですか。食い違いを隠さず答えてください。",
                [
                    {
                        "source_label": "Measurement A",
                        "page_start": 2,
                        "page_end": 2,
                        "score": 0.91,
                        "text": "At 15 K, sample X showed a spin lifetime of 2.1 ns by TRKR.",
                    },
                    {
                        "source_label": "Measurement B",
                        "page_start": 4,
                        "page_end": 4,
                        "score": 0.90,
                        "text": "At 15 K, sample X showed a spin lifetime of 3.4 ns by spin-grating spectroscopy.",
                    },
                ],
            ),
            required=["2.1 ns", "3.4 ns", "TRKR", "spin-grating"],
            citations=["S1", "S2"],
            forbidden=["2.75 ns"],
        ),
    ]

    calibration = [
        EvalCase(
            "calibration_fictional_paper",
            "calibration",
            (
                "2027年の論文『Room-temperature eternal spin helix in silicon』DOI 10.9999/fake.2027.001"
                "が報告した寿命と試料構造を教えてください。確認できない情報は推測しないでください。"
            ),
            required=["確認できません", "推測"],
            forbidden=["ns", "µs", "quantum well", "nanowire"],
        ),
        EvalCase(
            "calibration_false_premise",
            "calibration",
            (
                "Persistent Spin Helixは外部磁場だけで必ず永久に安定化される、という前提に基づき"
                "デバイス設計を説明してください。前提が誤っている場合は先に訂正してください。"
            ),
            required=["誤", "SU(2)", "Rashba", "Dresselhaus"],
            forbidden=["永久に安定化できます"],
        ),
    ]

    filler = (
        "This control section describes wafer cleaning, contact alignment, generic calibration, "
        "and transport checks. It contains no sample Z9 temperature or spin lifetime result. "
    ) * 12
    long_contexts = [
        {
            "source_label": f"Long control {index}",
            "page_start": index,
            "page_end": index,
            "score": 0.9 - index / 1000,
            "text": filler,
        }
        for index in range(1, 19)
    ]
    long_contexts.insert(
        13,
        {
            "source_label": "Long-context needle",
            "page_start": 19,
            "page_end": 19,
            "score": 0.88,
            "text": (
                "For sample Z9 at 22 K, wavelength-resolved TRKR gave a spin lifetime of "
                "5.6 ns. The uncertainty was 0.4 ns. No room-temperature value was measured."
            ),
        },
    )
    long_context = EvalCase(
        "rag_long_context_10k",
        "long_context",
        build_prompt("sample Z9の22 Kでのspin lifetime、uncertainty、測定法を答えてください。", long_contexts),
        max_tokens=384,
        required=["5.6 ns", "0.4 ns", "wavelength-resolved TRKR"],
        citations=["S14"],
    )
    return build_cases() + knowledge + reasoning + calibration + [long_context]


def post_json(base_url: str, path: str, payload: dict, timeout: int) -> dict:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(base_url: str, path: str, timeout: int = 5) -> dict:
    with urllib.request.urlopen(f"{base_url.rstrip('/')}{path}", timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_model_spec(value: str) -> tuple[str, str]:
    if "=" not in value:
        return value, value
    label, reference = value.split("=", 1)
    return label.strip(), reference.strip()


class RemoteLlamaServer:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.server_process: subprocess.Popen | None = None
        self.tunnel_process: subprocess.Popen | None = None
        self.log_handle = None

    def ssh_prefix(self) -> list[str]:
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-i",
            str(self.args.identity),
            self.args.ssh_host,
        ]

    def start_tunnel(self) -> None:
        command = self.ssh_prefix()[:-1] + [
            "-o",
            "ExitOnForwardFailure=yes",
            "-N",
            "-L",
            f"{self.args.local_port}:127.0.0.1:{self.args.remote_port}",
            self.args.ssh_host,
        ]
        self.tunnel_process = subprocess.Popen(command)
        time.sleep(1)
        if self.tunnel_process.poll() is not None:
            raise RuntimeError("SSH tunnel exited before evaluation started")

    def stop_remote(self) -> None:
        subprocess.run(
            self.ssh_prefix() + ["taskkill", "/F", "/IM", "llama-server.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if self.server_process is not None:
            try:
                self.server_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.server_process.kill()
            self.server_process = None
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None

    def start_model(self, label: str, reference: str, *, embedding: bool = False) -> Path:
        self.stop_remote()
        slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", label)
        log_path = Path(self.args.log_dir) / f"llama-{slug}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_handle = log_path.open("w", encoding="utf-8")
        if reference.startswith("file:"):
            model_args = ["-m", reference.removeprefix("file:")]
        else:
            model_args = ["-hf", reference]

        remote_command = [
            "llama-server",
            *model_args,
            "--host",
            "127.0.0.1",
            "--port",
            str(self.args.remote_port),
            "-c",
            str(self.args.context),
            "-np",
            "1",
            "--fit",
            "on",
            "--fit-target",
            str(self.args.fit_target_mib),
            "--fit-ctx",
            str(self.args.context),
            "--flash-attn",
            "on",
            "--cache-type-k",
            "q8_0",
            "--cache-type-v",
            "q8_0",
            "--jinja",
            "--no-webui",
        ]
        if getattr(self.args, "gpu_layers", None) is not None:
            remote_command.extend(["-ngl", str(self.args.gpu_layers)])
        if embedding:
            # Use each GGUF's pooling metadata (Qwen uses last-token; BGE uses CLS).
            remote_command.append("--embedding")
        self.server_process = subprocess.Popen(
            self.ssh_prefix() + remote_command,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + self.args.startup_timeout
        while time.monotonic() < deadline:
            if self.server_process.poll() is not None:
                self.log_handle.flush()
                tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-30:])
                raise RuntimeError(f"llama-server failed for {label}:\n{tail}")
            try:
                health = get_json(self.args.base_url, "/health", timeout=2)
                if health.get("status") == "ok":
                    return log_path
            except Exception:
                pass
            time.sleep(2)
        raise TimeoutError(f"llama-server startup timed out for {label}; see {log_path}")

    def gpu_memory_mib(self) -> int | None:
        command = self.ssh_prefix() + [
            "nvidia-smi",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        match = re.search(r"\d+", result.stdout)
        return int(match.group()) if match else None

    def close(self) -> None:
        self.stop_remote()
        if self.tunnel_process is not None:
            self.tunnel_process.terminate()
            try:
                self.tunnel_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.tunnel_process.kill()


def evaluate_model(
    base_url: str,
    label: str,
    cases: list[EvalCase],
    timeout: int,
    repetitions: int,
    reasoning_budget: int,
) -> dict:
    rows = []
    for repetition in range(repetitions):
        for case in cases:
            case_reasoning_budget = reasoning_budget if case.role == "reasoning" else 0
            payload = {
                "model": "local",
                "messages": [{"role": "user", "content": case.prompt}],
                "temperature": 0.2,
                "top_p": 0.8,
                "top_k": 20,
                "max_tokens": case.max_tokens,
                "seed": 20260826 + repetition,
                "cache_prompt": False,
                # Reserve most of max_tokens for the user-visible final answer.
                "reasoning_budget": case_reasoning_budget,
                "chat_template_kwargs": {"enable_thinking": case_reasoning_budget > 0},
            }
            started = time.monotonic()
            try:
                result = post_json(base_url, "/v1/chat/completions", payload, timeout)
                wall_seconds = time.monotonic() - started
                message = (result.get("choices") or [{}])[0].get("message") or {}
                response = str(message.get("content") or "").strip()
                reasoning_text = str(message.get("reasoning_content") or "").strip()
                score, notes = score_case(case, response)
                timings = result.get("timings") or {}
                rows.append(
                    {
                        "case": case.name,
                        "role": case.role,
                        "repetition": repetition + 1,
                        "score": score,
                        "notes": notes,
                        "wall_s": round(wall_seconds, 2),
                        "prompt_tokens": timings.get("prompt_n") or 0,
                        "prompt_tps": round(float(timings.get("prompt_per_second") or 0), 1),
                        "generation_tokens": timings.get("predicted_n") or 0,
                        "generation_tps": round(float(timings.get("predicted_per_second") or 0), 1),
                        "reasoning_chars": len(reasoning_text),
                        "response": response,
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "case": case.name,
                        "role": case.role,
                        "repetition": repetition + 1,
                        "score": 0.0,
                        "notes": [f"error={exc}"],
                        "wall_s": round(time.monotonic() - started, 2),
                        "response": "",
                    }
                )

    by_role = {}
    for role in sorted({row["role"] for row in rows}):
        role_rows = [row for row in rows if row["role"] == role]
        by_role[role] = round(statistics.mean(row["score"] for row in role_rows), 2)
    speeds = [row.get("generation_tps", 0) for row in rows if row.get("generation_tps", 0) > 0]
    quality_weights = {
        "rag": 0.25,
        "summary": 0.20,
        "knowledge": 0.20,
        "reasoning": 0.15,
        "calibration": 0.10,
        "long_context": 0.05,
        "translation": 0.03,
        "classification": 0.02,
    }
    weighted_quality = sum(
        by_role.get(role, 0.0) * weight for role, weight in quality_weights.items()
    )
    case_scores: dict[str, list[float]] = {}
    for row in rows:
        case_scores.setdefault(row["case"], []).append(row["score"])
    repeat_stddevs = [
        statistics.pstdev(scores) for scores in case_scores.values() if len(scores) > 1
    ]
    return {
        "model": label,
        "role_scores": by_role,
        "overall_score": round(statistics.mean(row["score"] for row in rows), 2),
        "weighted_quality_score": round(weighted_quality, 2),
        "mean_repeat_stddev": round(statistics.mean(repeat_stddevs), 2) if repeat_stddevs else 0.0,
        "error_rate": round(sum(not row["response"] for row in rows) / len(rows), 3),
        "average_generation_tps": round(statistics.mean(speeds), 1) if speeds else 0.0,
        "median_wall_s": round(statistics.median(row["wall_s"] for row in rows), 2),
        "cases": rows,
    }


def print_summary(results: list[dict]) -> None:
    roles = ["rag", "long_context", "summary", "translation", "knowledge", "reasoning", "calibration", "classification"]
    print("model\tquality\toverall\t" + "\t".join(roles) + "\tgen_tps\tvram_mib")
    for result in results:
        values = [result["model"], str(result["weighted_quality_score"]), str(result["overall_score"])]
        values.extend(str(result["role_scores"].get(role, "-")) for role in roles)
        values.extend([str(result["average_generation_tps"]), str(result.get("vram_mib", "-"))])
        print("\t".join(values))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", required=True, help="LABEL=HF_REPO:QUANT")
    parser.add_argument("--ssh-host", default="user@rtx-pc.example.invalid")
    parser.add_argument("--identity", type=Path, default=Path.home() / ".ssh/id_ed25519")
    parser.add_argument("--local-port", type=int, default=18080)
    parser.add_argument("--remote-port", type=int, default=8080)
    parser.add_argument("--context", type=int, default=16384)
    parser.add_argument("--fit-target-mib", type=int, default=1024)
    parser.add_argument("--startup-timeout", type=int, default=3600)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument(
        "--reasoning-budget",
        type=int,
        default=0,
        help="Enable hidden reasoning only for reasoning-role cases (default: disabled)",
    )
    parser.add_argument("--log-dir", type=Path, default=Path("/tmp/paperbot-llamacpp-logs"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.base_url = f"http://127.0.0.1:{args.local_port}"

    cases = build_quality_cases()
    manager = RemoteLlamaServer(args)
    results = []
    try:
        manager.start_tunnel()
        for raw_spec in args.models:
            label, reference = parse_model_spec(raw_spec)
            print(f"Starting {label}: {reference}", flush=True)
            log_path = manager.start_model(label, reference)
            print(f"Evaluating {label} ({len(cases)} cases x {args.repetitions})...", flush=True)
            result = evaluate_model(
                args.base_url,
                label,
                cases,
                args.timeout,
                args.repetitions,
                args.reasoning_budget,
            )
            result["reference"] = reference
            result["vram_mib"] = manager.gpu_memory_mib()
            result["server_log"] = str(log_path)
            results.append(result)
            print_summary([result])
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
                )
    finally:
        manager.close()

    print_summary(results)
    if args.output:
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
