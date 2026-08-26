#!/usr/bin/env python3
"""Compare llama.cpp embedding models on PaperBot-like retrieval queries."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path

from evaluate_llamacpp_models import RemoteLlamaServer, parse_model_spec, post_json


DOCUMENTS = {
    "psh_theory": "Equal Rashba and linear Dresselhaus coupling creates an SU(2) symmetry and a persistent spin helix.",
    "psh_cubic": "Cubic Dresselhaus coupling beta_3 limits the persistent spin helix lifetime in GaAs quantum wells.",
    "trkr": "Time-resolved Kerr rotation uses a circular pump and delayed probe to measure spin polarization dynamics.",
    "spin_grating": "Transient spin-grating spectroscopy measures spin diffusion and helical-mode decay at a selected wave vector.",
    "spin_hall_intrinsic": "The intrinsic spin Hall effect arises from band-structure Berry curvature.",
    "spin_hall_extrinsic": "Extrinsic spin Hall response includes skew scattering and the side-jump mechanism.",
    "dp": "D'yakonov-Perel' relaxation exhibits motional narrowing: more frequent momentum scattering slows spin relaxation.",
    "ey": "Elliott-Yafet spin relaxation comes from spin mixing and spin flips during momentum-scattering events.",
    "sample_d7_15k": "Sample D7 has a 3.7 ns spin lifetime at 15 K, measured by TRKR.",
    "sample_d7_30k": "Sample D7 has a 1.2 ns spin lifetime at 30 K, measured by TRKR.",
    "sample_z9": "For sample Z9 at 22 K, wavelength-resolved TRKR measured 5.6 plus or minus 0.4 ns.",
    "silicon": "Electrical spin injection and detection were demonstrated in a silicon lateral spin valve.",
    "gan": "Optical orientation in wurtzite GaN shows anisotropic electron-spin relaxation.",
    "mos2": "Valley polarization in monolayer MoS2 is probed by polarization-resolved photoluminescence.",
    "quantum_hall": "The integer quantum Hall effect produces quantized Hall conductance in a two-dimensional electron gas.",
    "sample_growth": "Molecular beam epitaxy growth conditions and wafer cleaning are reported without spin-lifetime data.",
}


QUERIES = [
    ("PSHの成立条件は何か", "psh_theory", "ja_en"),
    ("PSH寿命を有限にするcubic項", "psh_cubic", "ja_en"),
    ("pump-probe法でspin dynamicsを測る手法", "trkr", "ja_en"),
    ("Which technique measures a helical decay rate at a chosen wave vector?", "spin_grating", "en"),
    ("intrinsic spin Hall effectのBerry curvature起源", "spin_hall_intrinsic", "mixed"),
    ("skew scatteringとside jump", "spin_hall_extrinsic", "keyword"),
    ("運動量散乱が増えると緩和が遅くなる機構", "dp", "semantic"),
    ("spin mixingによる散乱時のspin flip", "ey", "semantic"),
    ("sample D7, 15 K, 3.7 ns", "sample_d7_15k", "numeric"),
    ("D7の30 Kにおける寿命", "sample_d7_30k", "numeric"),
    ("Z9 at 22 K uncertainty 0.4 ns", "sample_z9", "numeric"),
    ("シリコン横型スピンバルブでの電気的注入", "silicon", "ja_en"),
    ("wurtzite GaN electron spin relaxation anisotropy", "gan", "en"),
    ("単層MoS2のvalley polarization測定", "mos2", "ja_en"),
]


QUERY_INSTRUCTION = (
    "Instruct: Retrieve the scientific paper passage that best answers the research question.\nQuery: "
)


def cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0


def embeddings(base_url: str, texts: list[str], timeout: int) -> list[list[float]]:
    response = post_json(
        base_url,
        "/v1/embeddings",
        {"model": "local", "input": texts, "encoding_format": "float"},
        timeout,
    )
    rows = sorted(response.get("data") or [], key=lambda row: row.get("index", 0))
    return [row["embedding"] for row in rows]


def evaluate(base_url: str, label: str, timeout: int) -> dict:
    started = time.monotonic()
    doc_ids = list(DOCUMENTS)
    doc_vectors = embeddings(base_url, list(DOCUMENTS.values()), timeout)
    use_instruction = "qwen" in label.lower()
    query_texts = [f"{QUERY_INSTRUCTION}{query}" if use_instruction else query for query, _, _ in QUERIES]
    query_vectors = embeddings(base_url, query_texts, timeout)
    rows = []
    for (query, expected, category), vector in zip(QUERIES, query_vectors):
        ranked = sorted(
            ((doc_id, cosine(vector, doc_vector)) for doc_id, doc_vector in zip(doc_ids, doc_vectors)),
            key=lambda item: item[1],
            reverse=True,
        )
        rank = next(index for index, (doc_id, _) in enumerate(ranked, 1) if doc_id == expected)
        rows.append(
            {
                "query": query,
                "category": category,
                "expected": expected,
                "rank": rank,
                "top3": ranked[:3],
            }
        )
    return {
        "model": label,
        "recall_at_1": round(sum(row["rank"] == 1 for row in rows) / len(rows), 3),
        "recall_at_3": round(sum(row["rank"] <= 3 for row in rows) / len(rows), 3),
        "mrr": round(statistics.mean(1 / row["rank"] for row in rows), 3),
        "seconds": round(time.monotonic() - started, 2),
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", required=True, help="LABEL=file:C:\\path\\model.gguf")
    parser.add_argument("--ssh-host", default="user@rtx-pc.example.invalid")
    parser.add_argument("--identity", type=Path, default=Path.home() / ".ssh/id_ed25519")
    parser.add_argument("--local-port", type=int, default=18081)
    parser.add_argument("--remote-port", type=int, default=8081)
    parser.add_argument("--context", type=int, default=8192)
    parser.add_argument("--fit-target-mib", type=int, default=1024)
    parser.add_argument("--gpu-layers", type=int)
    parser.add_argument("--startup-timeout", type=int, default=600)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--log-dir", type=Path, default=Path("/tmp/paperbot-embedding-logs"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.base_url = f"http://127.0.0.1:{args.local_port}"

    manager = RemoteLlamaServer(args)
    results = []
    try:
        manager.start_tunnel()
        for spec in args.models:
            label, reference = parse_model_spec(spec)
            try:
                manager.start_model(label, reference, embedding=True)
                result = evaluate(args.base_url, label, args.timeout)
                result["vram_mib"] = manager.gpu_memory_mib()
            except Exception as exc:
                result = {"model": label, "error": str(exc)}
            result["reference"] = reference
            results.append(result)
            print(json.dumps({key: value for key, value in result.items() if key != "cases"}, ensure_ascii=False))
    finally:
        manager.close()

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
