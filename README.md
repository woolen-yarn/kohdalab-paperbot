# KohdaLab PaperBot MVP

Slack DMからMac上のPDF RAGを呼び、RTX PCのOllamaで回答する最小構成です。

GitHub:

```text
origin   https://github.com/Kohdalab/kohdalab-paperbot      private
personal https://github.com/woolen-yarn/kohdalab-paperbot   public
```

```text
Slack DM
  -> Mac: bot.py
  -> Mac/NAS: rag_poc/index/chunks.sqlite3
  -> RTX PC: Ollama at 10.32.145.143:11434
  -> Slack reply with short source IDs
```

## Current Roles

```text
Mac /Users/kikuchikeito/projects/llm
  bot.py                 Slack Bot
  rag_poc/ingest.py      PDF extraction, chunking, embedding
  rag_poc/ask.py         RAG search and answer generation
  rag_poc/papers/        PDF input folder
  rag_poc/index/         generated local index

RTX PC
  Ollama
  gpt-oss:20b            chat / answer model
  nomic-embed-text       embedding model
```

## Start Slack Bot

First create a local `.env` file. Do not commit it.

```bash
cd /Users/kikuchikeito/projects/llm
cp .env.example .env
```

Edit `.env` and set the real Slack tokens:

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

Start the bot:

```bash
make bot
```

Keep this terminal running. Stop with `Ctrl+C`.

## Ask From Slack

Open a DM with PaperBot and send a question, for example:

```text
Persistent Spin Helixに関係する内容を教えて
```

The bot should reply with an answer and short `Sources` such as `S1, S2, S3`.
Send `sources` to show the full source list from your previous answer.

In DM, PaperBot replies as normal sequential messages. In channel mentions, it replies in a thread to avoid cluttering the channel.

## Slack Commands

PaperBot understands these commands in DM or mention replies:

```text
/help      Show available commands
/model     Show current Ollama chat/embedding model settings
/sources   Show sources from your previous answer in the same DM/channel
/recent    Show recent PDFs on the local papers volume
```

If Slack intercepts slash-style text, send the command without `/`, for example `model` or `sources`.

## Add Or Replace PDFs

Put PDFs here:

```text
/Users/kikuchikeito/projects/llm/rag_poc/papers/
```

Then rebuild the index:

```bash
cd /Users/kikuchikeito/projects/llm
make ingest
```

Restart `bot.py` after rebuilding the index, because the bot caches the index in memory.
The RAG index is stored as:

```text
rag_poc/index/chunks.sqlite3
```

The ingest summary is stored as:

```text
rag_poc/index/ingest_report.json
```

If the indexed PDF count is lower than the number of PDF files, check this report.
`zero_text_sources` are PDFs where text extraction produced no chunks, and
`duplicate_sources` are exact duplicate files skipped by SHA-256 hash.

## Test RAG Without Slack

```bash
cd /Users/kikuchikeito/projects/llm
make ask Q="PSHの寿命異方性について教えて"
```

## Sync Zotero Metadata

Set Zotero credentials in `.env`:

```text
ZOTERO_LIBRARY_TYPE=group
ZOTERO_LIBRARY_ID=1234567
ZOTERO_API_KEY=...
ZOTERO_SYNC_LIMIT=25
```

Then fetch recent top-level Zotero items and save paper metadata into SQLite:

```bash
cd /Users/kikuchikeito/projects/llm
make zotero
```

For a connection-only check without writing to SQLite:

```bash
uv run python rag_poc/zotero_sync.py --limit 5 --dry-run
```

Metadata is stored in the `papers` table inside:

```text
rag_poc/index/chunks.sqlite3
```

## Logs

Runtime logs are written here:

```text
/Users/kikuchikeito/projects/llm/logs/paperbot.log
```

The log records Slack surface, user id, chat model, embedding model, duration, answer length, question text, selected sources, commands, and errors. It rotates automatically at about 5 MB.

## DS920+ Deployment

To run PaperBot on the Synology DS920+ and keep RTX PC as the LLM/embedding server, use:

[docs/DS920_DEPLOY.md](docs/DS920_DEPLOY.md)

For GitHub + DS920+ workflow, use:

[docs/GIT_WORKFLOW.md](docs/GIT_WORKFLOW.md)

For NAS-local repo operation with Portainer management, use:

[docs/PORTAINER_DEPLOY.md](docs/PORTAINER_DEPLOY.md)

Deployment files:

```text
Dockerfile
docker-compose.nas.yml
docker-compose.portainer.yml
docker-compose.stack-local.yml
.dockerignore
requirements.txt
scripts/deploy_nas.sh
scripts/reindex_nas.sh
```

## Check RTX PC Ollama

```bash
curl -s http://10.32.145.143:11434/api/tags | python3 -m json.tool
```

Required models on the RTX PC:

```powershell
ollama pull gpt-oss:20b
ollama pull nomic-embed-text
```

## Benchmark Ollama Models

Measure generation speed for candidate chat models:

```bash
cd /Users/kikuchikeito/projects/llm
make bench MODELS="qwen3:8b gemma4:12b gpt-oss:20b"
```

The benchmark reports Ollama's `eval_count / eval_duration` as generation tokens/sec.

Direct usage:

```bash
uv run python scripts/benchmark_ollama_models.py \
  --base-url http://10.32.145.143:11434 \
  --models qwen3:8b gemma4:12b gpt-oss:20b \
  --runs 2
```

## Notes

- This is the MVP. It intentionally uses a local SQLite index instead of Postgres/pgvector.
- `rag_poc/ingest.py` removes obvious References pages before indexing to reduce citation-list hallucinations.
- `rag_poc/ask.py` uses a light hybrid score: embedding similarity plus keyword/topic hints.
- Short questions such as "一文で" use fewer retrieved chunks; deeper comparison/review questions use more.
- `.env` and `logs/` are intentionally ignored by Git.
- DS920+ deployment keeps always-on Bot control on NAS and heavy LLM/embedding on RTX PC.
