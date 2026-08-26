.PHONY: bot ingest ask zotero lab-profile paper-watch bench eval-models eval-llama-models eval-embedding-models test check

MODELS ?= hf.co/AtomicChat/gemma-4-26B-A4B-it-GGUF:IQ4_XS hf.co/empero-ai/Qwen3.8-27B-Ridge-GGUF:latest gemma4:12b qwen3.5:9b
LLAMA_MODELS ?= gemma4-26b=AtomicChat/gemma-4-26B-A4B-it-GGUF:IQ4_XS qwen38-27b=unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_S
EMBEDDING_MODELS ?= qwen3-embed-0.6b=Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0 qwen3-embed-4b=Qwen/Qwen3-Embedding-4B-GGUF:Q8_0
INGEST_ARGS ?=
ZOTERO_ARGS ?=

bot:
	uv run python bot.py

ingest:
	uv run python rag_poc/ingest.py $(INGEST_ARGS)

ask:
	uv run python rag_poc/ask.py "$(Q)"

zotero:
	uv run python rag_poc/zotero_sync.py $(ZOTERO_ARGS)

lab-profile:
	uv run python -m rag_poc.lab_profile $(LAB_PROFILE_ARGS)

paper-watch:
	uv run python -m rag_poc.paper_watch $(PAPER_WATCH_ARGS)

bench:
	uv run python scripts/benchmark_ollama_models.py --models $(MODELS)

eval-models:
	uv run python scripts/evaluate_generation_models.py --models $(MODELS)

eval-llama-models:
	uv run python scripts/evaluate_llamacpp_models.py --models $(LLAMA_MODELS)

eval-embedding-models:
	uv run python scripts/evaluate_embedding_models.py --models $(EMBEDDING_MODELS)

test:
	uv run python -m unittest discover -v

check:
	uv run python -m py_compile bot.py rag_poc/ask.py rag_poc/ingest.py rag_poc/lab_profile.py rag_poc/llama_client.py rag_poc/ollama_client.py rag_poc/paper_watch.py rag_poc/sync_notify.py rag_poc/zotero_sync.py scripts/benchmark_ollama_models.py scripts/evaluate_generation_models.py scripts/evaluate_llamacpp_models.py scripts/evaluate_embedding_models.py scripts/cleanup_slack_channel.py
	uv run python -m unittest discover -v
