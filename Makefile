.PHONY: bot ingest ask check

bot:
	uv run python bot.py

ingest:
	uv run python rag_poc/ingest.py

ask:
	uv run python rag_poc/ask.py "$(Q)"

check:
	uv run python -m py_compile bot.py rag_poc/ask.py rag_poc/ingest.py rag_poc/ollama_client.py
