# Local Model Evaluation

PaperBot runs generation and embeddings with two OpenAI-compatible `llama.cpp`
servers on Whale (RTX 4080 SUPER, 16 GB VRAM; 64 GB system RAM). The benchmark
uses paraphrased synthetic fixtures and no private paper text.

## 2026-08-26 generation result

`scripts/evaluate_llamacpp_models.py` tests 21 cases: cited RAG, missing-evidence
handling, prompt injection, scientific summaries, claim-preserving translation,
strict JSON, domain knowledge, calculation and experiment-design reasoning,
false-premise calibration, conflicting sources, and a roughly 10K-token retrieval
needle. Quality is weighted toward RAG, summaries, knowledge, and reasoning.

All candidates used a 16K context, Q8 KV cache, temperature 0.2, one parallel
slot, and automatic GPU fitting. Scores are out of 10.

| Model / quantization | Quality | RAG | Summary | Knowledge | Reasoning | tok/s | VRAM MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **Gemma 4 26B-A4B IQ4_XS (2-run final)** | **9.33** | **10.00** | **9.75** | **8.37** | **10.00** | 129.2 | 14,615 |
| Qwen3.8 27B UD-Q4_K_S | 9.09 | **10.00** | 9.50 | 8.20 | 8.80 | 21.5 | 14,569 |
| Ornith 1.5 35B-A3B IQ3_XXS | 8.57 | 9.80 | 7.50 | 7.62 | 9.20 | 153.0 | 13,786 |
| Qwen3.6 35B-A3B IQ3_S | 8.41 | 9.47 | 7.00 | 8.20 | 8.80 | 122.8 | 13,653 |
| Nemotron 3.5 Lightning 30B-A3B mixed 2.97bpw | 8.05 | 8.33 | 7.83 | 7.40 | 8.60 | 205.8 | 11,907 |
| Mistral Small 3.2 24B Q4_K_M | 7.39 | 7.47 | 8.00 | 7.40 | 7.73 | 23.8 | 14,687 |
| DeepSeek-R1-Distill-Qwen-32B IQ4_XS | 6.85 | 9.33 | 4.50 | 5.63 | 7.00 | 5.2 | 14,580 |
| gpt-oss 20B MXFP4 | 5.91 | 8.80 | 3.00 | 4.50 | 6.00 | 170.3 | 12,064 |

Additional screening included Phi-4 Q8 (8.40), the 12 GB abliterated
Qwen3.8-27B MTP quant (8.31 overall), and gpt-oss with a 256-token reasoning
budget. The latter did not fix its empty-final-answer failures. BGE-M3 could not
be loaded because this llama.cpp build does not support its `xlmr` architecture.

The automated rubric is a screening tool, so outputs were manually inspected.
Gemma repeated its lead with a mean per-case standard deviation of 0.05 and no
request errors. Qwen3.8 is not a bad model: it was perfect on these grounded RAG
cases and nearly matched Gemma on summaries. It nevertheless reversed the
D'yakonov-Perel'/Elliott-Yafet scattering-time relation and invented the PSH
expansion in strict, ungrounded tests. Gemma also failed those two isolated
knowledge checks. Therefore PaperBot must treat retrieved paper text as the
authority; no tested 16 GB local model should be trusted as an uncited scientific
knowledge base.

## Embedding result

`scripts/evaluate_embedding_models.py` uses 16 close scientific passages and 14
English/Japanese/mixed queries, including numeric hard negatives.

| Model | Device | Recall@1 | Recall@3 | MRR | Batch seconds |
| --- | --- | ---: | ---: | ---: | ---: |
| **Qwen3-Embedding-4B Q8_0** | CPU | **1.000** | **1.000** | **1.000** | 10.35 |
| Qwen3-Embedding-4B Q8_0 | GPU | **1.000** | **1.000** | **1.000** | 1.91 |
| Qwen3-Embedding-0.6B Q8_0 | GPU | 0.929 | **1.000** | 0.964 | 0.21 |

The 4B model runs on CPU so the selected generator can retain almost all GPU
memory. Query-only Qwen retrieval instructions are added by `llama_client.py`;
document embeddings remain unprefixed.

## Selected production layout

- Generation `:8080`: `gemma-4-26B-A4B-it-IQ4_XS.gguf`, 16K context, Q8 KV,
  reasoning disabled.
- Embeddings `:8081`: `Qwen3-Embedding-4B-Q8_0.gguf`, CPU-only, 8K context.
- The SQLite index records its embedding-model label. A changed or legacy model
  is rejected until `python rag_poc/ingest.py --rebuild` performs a full reindex;
  vectors from different models can never be silently mixed.

Re-run the benchmarks after a llama.cpp or model upgrade:

```bash
make eval-llama-models
make eval-embedding-models
```
