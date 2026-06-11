# KohdaLab PaperBot

[![CI](https://github.com/woolen-yarn/kohdalab-paperbot/actions/workflows/ci.yml/badge.svg)](https://github.com/woolen-yarn/kohdalab-paperbot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/woolen-yarn/kohdalab-paperbot?include_prereleases&sort=semver)](https://github.com/woolen-yarn/kohdalab-paperbot/releases)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](pyproject.toml)

KohdaLab PaperBot is a private lab knowledge platform for papers. It keeps
Zotero as the source of truth, stores PDFs and a SQLite RAG index on the
Synology NAS, sends heavy LLM and embedding work to the RTX PC, and exposes the
system through Slack.

主な用途は次の4つです。

- Zotero Group Libraryの論文メタデータとPDFをNASへ同期する
- PDF本文をSQLiteへ差分indexingし、Slack DMからRAG検索する
- arXiv / APS / Nature / AIP / nano・2D系の新着論文を監視して`#paper`へ投稿する
- 同期結果と失敗を`#paperbot-log`へ通知する

## Production Architecture

DS920+ is the always-on control plane. The RTX PC only runs Ollama and receives
chat, translation, and embedding requests over the LAN.

```mermaid
flowchart TB
  subgraph sources["Knowledge Sources"]
    direction TB
    zotero["Zotero Group Library\nmetadata + PDF attachments"]
    feeds["New paper feeds\narXiv / APS / Nature / AIP / Crossref"]
  end

  subgraph nas["Synology DS920+ Control Plane\n/volume1/docker/paperbot"]
    direction TB

    subgraph jobs["Scheduled Jobs"]
      sync["Paperbot 01:00 daily\nZotero sync + incremental ingest"]
      watch["Paper Watch weekly/monthly\nrecommend new papers"]
    end

    subgraph app["Runtime Service"]
      bot["paperbot container\nSlack Socket Mode RAG bot"]
    end

    subgraph state["Local Knowledge State"]
      pdfs[("PDF archive\nrag_poc/papers/zotero")]
      db[("SQLite knowledge DB\nchunks.sqlite3")]
      profile["Lab interest profile\nmaterials / methods / physics"]
      logs[("operation logs")]
    end
  end

  subgraph rtx["RTX PC AI Engine\nOllama http://10.32.145.143:11434"]
    direction TB
    embed["Embedding model\nnomic-embed-text"]
    chat["Reasoning / commentary\ngpt-oss:20b"]
    translate["Japanese translation\nqwen3:14b"]
  end

  subgraph slack["Slack Workspace"]
    direction TB
    dm["DM with PaperBot\nRAG Q&A"]
    paper["#paper\nnew paper recommendations"]
    ops["#paperbot-log\nsync success / failure"]
  end

  zotero -->|"metadata + unique PDFs"| sync
  feeds -->|"recent candidates"| watch

  sync --> pdfs
  sync --> db
  sync --> profile
  sync --> logs
  watch --> db
  watch --> logs
  bot --> db

  sync -->|"chunk embeddings"| embed
  bot -->|"query embedding"| embed
  watch -->|"candidate similarity"| embed
  bot -->|"grounded answer"| chat
  watch -->|"technical commentary"| chat
  bot -->|"JA output"| translate
  watch -->|"JA commentary"| translate

  bot <-->|"questions + cited answers"| dm
  watch -->|"one compact post per paper"| paper
  sync -->|"daily status"| ops

  classDef source fill:#fff7ed,stroke:#ea580c,color:#431407
  classDef nas fill:#eff6ff,stroke:#2563eb,color:#172554
  classDef state fill:#f8fafc,stroke:#64748b,color:#0f172a
  classDef rtx fill:#ecfdf5,stroke:#059669,color:#064e3b
  classDef slack fill:#f5f3ff,stroke:#7c3aed,color:#2e1065
  class zotero,feeds source
  class sync,watch,bot nas
  class pdfs,db,profile,logs state
  class embed,chat,translate rtx
  class dm,paper,ops slack
```

More detailed diagrams are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

The repository stores application code only. Local runtime data is intentionally
ignored by Git:

- `.env`
- `logs/`
- `rag_poc/papers/`
- `rag_poc/index/`

## Current Lab Schedule

These are the intended Synology DSM Task Scheduler entries. The tasks run as
`root`, so the command does not need `sudo` inside DSM.

| DSM task | Timing | Purpose | Command |
| --- | --- | --- | --- |
| `Paperbot` | Every day 01:00 | Zotero metadata/PDF sync, incremental RAG ingest, profile rebuild, Slack status notification | `cd /volume1/docker/paperbot && ./scripts/sync_zotero_pipeline.sh` |
| `Paperbot-collect` | Every day 02:00 | Collect broad paper metadata, then enrich recent candidates with RAG similarity without Slack posts | `cd /volume1/docker/paperbot && ./scripts/collect_paper_watch.sh --lookback-days 7` |
| `Paperbot-arXiv-report` | Every Monday 09:30 | Weekly arXiv report from stored metadata | `cd /volume1/docker/paperbot && ./scripts/report_paper_watch.sh --report-scope arxiv --lookback-days 7 --post-limit 5 --min-score 4.5 --report-title "Paper Watch Weekly arXiv"` |
| `Paperbot-APS-JP-report` | First Monday 09:30 | Monthly APS and Japan/applied physics report | `cd /volume1/docker/paperbot && ./scripts/report_paper_watch.sh --report-scope journals --report-groups aps_core,aps_ext_reviews,japan_physics --lookback-days 35 --post-limit 5 --min-score 4.5 --report-title "Paper Watch Monthly APS/JP"` |
| `Paperbot-Nature-report` | Second Monday 09:30 | Monthly Nature-family and broad high-impact report | `cd /volume1/docker/paperbot && ./scripts/report_paper_watch.sh --report-scope journals --report-groups nature_family,broad_high_impact --lookback-days 35 --post-limit 5 --min-score 4.5 --report-title "Paper Watch Monthly Nature/High Impact"` |
| `Paperbot-AIP-Optics-report` | Third Monday 09:30 | Monthly AIP, IOP, and optics report | `cd /volume1/docker/paperbot && ./scripts/report_paper_watch.sh --report-scope journals --report-groups aip_family,iop_optics --lookback-days 35 --post-limit 5 --min-score 4.5 --report-title "Paper Watch Monthly AIP/Optics"` |
| `Paperbot-Nano2D-report` | Fourth Monday 09:30 | Monthly nano, 2D materials, and applied materials report | `cd /volume1/docker/paperbot && ./scripts/report_paper_watch.sh --report-scope journals --report-groups nano_2d_materials --lookback-days 35 --post-limit 5 --min-score 4.5 --report-title "Paper Watch Monthly Nano/2D"` |

Paper Watch does not send immediate alerts in the production schedule. Daily
collection stores metadata, structured lab tags, report categories, and scores
in a dedicated `paper_watch.sqlite3` database. Slack posts are generated from
that stored database by weekly and monthly report tasks.

`collect_paper_watch.sh` runs two steps: first metadata collection, then RAG
enrichment for recently collected unscored candidates. Set
`PAPER_WATCH_RAG_AFTER_COLLECT=false` only when the NAS should collect metadata
without touching the RTX embedding endpoint.

Recommended cadence: collect metadata every day, report arXiv every week, and
report journal groups monthly. Monthly reports are spread across four Mondays
and capped at five papers so the Slack channel stays readable.

## Deployment

### 1. RTX PC

Install Ollama on the RTX PC and expose it to the LAN.

```bash
ollama pull nomic-embed-text
ollama pull gpt-oss:20b
ollama pull qwen3:14b
```

From DS920+ or another LAN machine, this must return JSON:

```bash
curl http://10.32.145.143:11434/api/tags
```

### 2. DS920+

The production path is:

```bash
/volume1/docker/paperbot
```

Clone or update the repository on the NAS, then create local runtime folders and
the secret environment file.

```bash
cd /volume1/docker
git clone git@github.com-paperbot:Kohdalab/kohdalab-paperbot.git paperbot
cd /volume1/docker/paperbot
mkdir -p rag_poc/papers/zotero rag_poc/index logs
cp .env.example .env
vi .env
```

Required `.env` values:

- `SLACK_BOT_TOKEN`
- `SLACK_APP_TOKEN`
- `OLLAMA_BASE_URL=http://10.32.145.143:11434`
- `OLLAMA_CHAT_MODEL=gpt-oss:20b`
- `PAPERBOT_TRANSLATION_MODEL=qwen3:14b`
- `OLLAMA_EMBED_MODEL=nomic-embed-text`
- `ZOTERO_LIBRARY_TYPE=group`
- `ZOTERO_LIBRARY_ID`
- `ZOTERO_API_KEY`
- `SYNC_NOTIFY_CHANNEL=#paperbot-log`
- `PAPER_WATCH_CHANNEL=#paper`

Start the Slack bot container:

```bash
sudo docker compose -f docker-compose.nas.yml up -d --build paperbot
```

Run the first Zotero/RAG pipeline:

```bash
sudo ./scripts/sync_zotero_pipeline.sh
```

### 3. Slack

PaperBot uses Slack Socket Mode, so the NAS does not need a public webhook URL.

- DM with the bot: ask questions against the RAG index.
- `#paper`: receives Paper Watch recommendations.
- `#paperbot-log`: receives sync success/failure notifications.

Useful bot commands:

- `status` or `状態`: show Ollama, SQLite, Zotero, and index status.
- `sources`: show detailed sources for the previous answer.
- Any paper question: run RAG search and answer with citations.

## Paper Watch

Paper Watch is split into collection and reporting.

Daily collection fetches metadata only, deduplicates by DOI or normalized title,
classifies papers into lab-oriented JSON tags, scores papers with the lab
profile and optional RAG similarity, and stores the result in
`rag_poc/index/paper_watch.sqlite3`. It does not post to Slack. Weekly and
monthly report tasks then select the highest-scoring stored papers and post
compact Slack messages one paper at a time. Each selected paper is sent with a
separate `chat.postMessage` call; an eight-paper report produces eight Slack
messages, not one combined digest.

Scoring inputs:

- weighted profile terms generated from the indexed lab PDFs
- material-method-physics combination bonuses from `lab_profile.json`
- negative-profile penalties for peripheral topics such as battery-only or catalysis-only papers
- title and abstract term matches with safer short-term matching
- optional candidate abstract embedding similarity to the SQLite RAG index
- journal/source group
- duplicate suppression via DOI and normalized title

Collected metadata and classifications:

- Paper Watch group: one shared group used for both collection and Slack reports
- paper type: `experiment`, `theory/simulation`, `review`, or `article`
- lab tags: `materials`, `methods`, `physics`, and `applications`
- profile score reasons: matched weighted terms, common theme combinations, and penalties
- expiry time: old paper-watch metadata is deleted after
  `PAPER_WATCH_RETENTION_DAYS`

Paper Watch groups:

| Group | Sources and report bucket |
| --- | --- |
| `arxiv_weekly` | arXiv API |
| `aps_core` | PRL, PRB, PR Applied, PR Research, PR Materials |
| `aps_ext_reviews` | PRX, PRX Quantum, PRX Energy, RMP |
| `nature_family` | Nature Physics, Nature Communications, Communications Physics, Communications Materials, npj Spintronics, npj Quantum Materials, and related Nature journals |
| `aip_family` | APL, JAP, APL Materials, APL Quantum, Applied Physics Reviews, AIP Advances |
| `japan_physics` | JJAP, APEX, JPSJ, STAM, NPG Asia Materials |
| `iop_optics` | Semiconductor Science and Technology, Journal of Physics D, Laser & Photonics Reviews, Optics Letters |
| `nano_2d_materials` | Nano Letters, ACS Nano, ACS Photonics, ACS Applied Nano Materials, ACS Applied Electronic Materials, Journal of Materials Chemistry C, 2D Materials, npj 2D Materials and Applications |
| `broad_high_impact` | Advanced Science, Advanced Materials, Science Advances, PNAS, Cell Reports Physical Science |

Use these canonical group names in `.env`, DSM schedules, and manual commands.

Access policy:

- arXiv is queried with a submitted-date window by default, so weekly runs only
  ask for recent matches.
- arXiv requests stay below the 2,000-result slice size and normally use one
  request per run.
- Crossref requests include `mailto`, sleep between queries, clamp rows per
  request, and stop/back off on 4XX or 429 responses.
- RSS is used where it works; Crossref ISSN-filtered journal supplements cover
  feeds that are missing, blocked, or too short.

Manual dry run:

```bash
sudo ./scripts/collect_paper_watch.sh --dry-run --sources arxiv --lookback-days 7
```

Dry run only the post-collection RAG enrichment step:

```bash
sudo ./scripts/run_paper_watch.sh --mode rag --dry-run --lookback-days 7
```

Dry run the weekly arXiv report from stored metadata:

```bash
sudo ./scripts/report_paper_watch.sh --dry-run --report-scope arxiv --lookback-days 7 --post-limit 3
```

Dry run a monthly journal report from stored metadata:

```bash
sudo ./scripts/report_paper_watch.sh --dry-run --report-scope journals --report-groups aps_core,japan_physics --lookback-days 35 --post-limit 3
```

Immediate fetch-and-post mode is retained for manual debugging only:

```bash
sudo ./scripts/run_paper_watch.sh --mode run --sources arxiv --post-limit 1 --min-score 0
```

Inspect which journals are actually common in the current Zotero-backed SQLite
database:

```bash
sudo docker compose -f docker-compose.nas.yml run --rm paperbot \
  python - <<'PY'
import sqlite3

conn = sqlite3.connect('/app/rag_poc/index/chunks.sqlite3')
for journal, count in conn.execute("""
    SELECT COALESCE(NULLIF(journal, ''), '(unknown)') AS journal, COUNT(*) AS n
    FROM unique_papers
    GROUP BY journal
    ORDER BY n DESC
    LIMIT 50
"""):
    print(f"{count:4d}  {journal}")
PY
```

## Zotero Sync and RAG Ingest

The daily pipeline performs:

```mermaid
flowchart TD
  start["DSM task: Paperbot 01:00"] --> check["Check Ollama embedding endpoint"]
  check --> zotero["Fetch Zotero metadata"]
  zotero --> dedup["Deduplicate papers\nDOI first, normalized title fallback"]
  dedup --> pdf["Download only missing/changed unique PDFs"]
  pdf --> ingest["Incremental PDF text extraction + chunking"]
  ingest --> embed["Embed only new/changed chunks"]
  embed --> profile["Rebuild lab interest profile"]
  profile --> restart["Restart Slack bot container"]
  restart --> notify["Post success/failure to #paperbot-log"]
```

Incremental behavior:

- Existing Zotero PDFs are skipped unless the attachment changed.
- Duplicate papers are retained in metadata but not downloaded/indexed twice.
- Existing PDF chunks are reused when file hashes are unchanged.
- Zero-text and duplicate PDFs are recorded in `ingest_report.json`.

Important files on the NAS:

| Path | Role |
| --- | --- |
| `/volume1/docker/paperbot/.env` | secrets and runtime configuration |
| `/volume1/docker/paperbot/rag_poc/papers/zotero/` | local Zotero PDF archive |
| `/volume1/docker/paperbot/rag_poc/index/chunks.sqlite3` | metadata, chunks, embeddings, seen papers |
| `/volume1/docker/paperbot/rag_poc/index/paper_watch.sqlite3` | collected external paper metadata, classifications, scores, and report state |
| `/volume1/docker/paperbot/rag_poc/index/lab_profile.md` | generated human-readable lab interest profile |
| `/volume1/docker/paperbot/rag_poc/index/lab_profile.json` | scoring-ready profile with core themes, hot topics, combinations, weighted terms, and negative terms |
| `/volume1/docker/paperbot/logs/sync_zotero_pipeline.log` | scheduled Zotero/RAG pipeline log |
| `/volume1/docker/paperbot/logs/paper_watch.log` | scheduled Paper Watch log |

## Operations

Update code on DS920+:

```bash
cd /volume1/docker/paperbot
sudo git pull origin master
sudo docker compose -f docker-compose.nas.yml up -d --build paperbot
```

All Compose services share the `kohdalab-paperbot:local` image. The scheduled
scripts quietly rebuild that image before one-off jobs by default, so
`paper-watch`, `zotero`, and `ingest` do not silently keep using an old image
after a Git pull. Set `COMPOSE_RUN_BUILD=0` only if you intentionally want to
skip that check.

Follow logs:

```bash
sudo tail -f /volume1/docker/paperbot/logs/sync_zotero_pipeline.log
sudo tail -f /volume1/docker/paperbot/logs/paper_watch.log
sudo docker logs -f kohdalab-paperbot
```

Clean up PaperBot test posts in `#paper`:

```bash
cd /volume1/docker/paperbot
sudo ./scripts/cleanup_slack_channel.sh --channel "#paper" --limit 50
sudo ./scripts/cleanup_slack_channel.sh --channel "#paper" --limit 50 --delete
```

The first command is a dry run and deletes nothing. The cleanup script only
targets messages posted by the current bot token.

Slack cleanup needs extra Bot Token OAuth scopes because it must read channel
history before deleting bot-authored messages:

- public channel such as `#paper`: `channels:read`, `channels:history`
- private channel: `groups:read`, `groups:history`
- deletion: existing `chat:write`

After changing scopes in Slack App > OAuth & Permissions, reinstall the app to
the workspace. If you pass a channel ID such as `C123...` instead of `#paper`,
`channels:read` is not needed for name lookup, but `channels:history` is still
needed to find the messages.

Rebuild the whole RAG index only when needed:

```bash
cd /volume1/docker/paperbot
sudo REBUILD=1 ./scripts/sync_zotero_pipeline.sh
```

Check Python files locally or in CI:

```bash
make check
```

## Development Notes

The code is intentionally simple:

- Python application code
- Docker Compose on DS920+
- SQLite as the single local database
- Zotero as the source of truth
- Ollama on RTX PC for local LLM operation
- Slack Socket Mode for user interaction

The project does not commit PDFs, embeddings, logs, or tokens. Public mirrors can
therefore expose the code without exposing the lab paper archive or Slack/Zotero
credentials.

## Release

Current release: `v0.2.0`

See [CHANGELOG.md](CHANGELOG.md) for release notes.

## License

MIT License. See [LICENSE](LICENSE).
