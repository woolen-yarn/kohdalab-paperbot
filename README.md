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
  -> Mac: rag_poc/index/chunks.jsonl
  -> RTX PC: Ollama at 10.32.145.143:11434
  -> Slack reply with sources
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
  qwen3:8b               chat / answer model
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

The bot should reply with an answer and `Sources` such as `S1`, `S2`, etc.

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

## Test RAG Without Slack

```bash
cd /Users/kikuchikeito/projects/llm
make ask Q="PSHの寿命異方性について教えて"
```

## Logs

Runtime logs are written here:

```text
/Users/kikuchikeito/projects/llm/logs/paperbot.log
```

The log records Slack surface, user id, duration, question text, selected sources, and errors. It rotates automatically at about 5 MB.

## DS920+ Deployment

To run PaperBot on the Synology DS920+ and keep RTX PC as the LLM/embedding server, use:

[docs/DS920_DEPLOY.md](docs/DS920_DEPLOY.md)

For GitHub + DS920+ workflow, use:

[docs/GIT_WORKFLOW.md](docs/GIT_WORKFLOW.md)

Deployment files:

```text
Dockerfile
docker-compose.nas.yml
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
ollama pull qwen3:8b
ollama pull nomic-embed-text
```

## Notes

- This is the MVP. It intentionally uses a local JSONL index instead of Postgres/pgvector.
- `rag_poc/ingest.py` removes obvious References pages before indexing to reduce citation-list hallucinations.
- `rag_poc/ask.py` uses a light hybrid score: embedding similarity plus keyword/topic hints.
- `.env` and `logs/` are intentionally ignored by Git.
- DS920+ deployment keeps always-on Bot control on NAS and heavy LLM/embedding on RTX PC.
