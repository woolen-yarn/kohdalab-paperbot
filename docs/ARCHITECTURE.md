# Architecture

KohdaLab PaperBot is designed as a small lab-scale knowledge platform: the NAS
is the reliable always-on controller, and the RTX PC is the local AI accelerator.

## System Topology

```mermaid
flowchart LR
  subgraph internet["External Knowledge Layer"]
    zotero["Zotero Group Library\nsource of truth"]
    arxiv["arXiv"]
    journals["APS / Nature / AIP / Crossref"]
  end

  subgraph lan["KohdaLab LAN"]
    subgraph nas["Synology DS920+\nControl plane + storage"]
      compose["Docker Compose"]
      bot["paperbot\nSlack bot"]
      scheduler["DSM Task Scheduler"]
      sqlite[("SQLite\npapers + chunks + embeddings")]
      archive[("PDF archive")]
    end

    subgraph gpu["RTX PC\nOllama AI engine"]
      embed["nomic-embed-text"]
      english["gpt-oss:20b"]
      japanese["qwen3:14b"]
    end
  end

  subgraph slack["Slack"]
    dm["DM RAG"]
    paper["#paper"]
    log["#paperbot-log"]
  end

  zotero --> scheduler
  arxiv --> scheduler
  journals --> scheduler
  scheduler --> compose
  compose --> bot
  compose --> sqlite
  compose --> archive
  compose --> embed
  bot --> sqlite
  bot --> english
  bot --> japanese
  bot <--> dm
  scheduler --> paper
  scheduler --> log

  classDef external fill:#fff7ed,stroke:#ea580c,color:#431407
  classDef nas fill:#eff6ff,stroke:#2563eb,color:#172554
  classDef gpu fill:#ecfdf5,stroke:#059669,color:#064e3b
  classDef slack fill:#f5f3ff,stroke:#7c3aed,color:#2e1065
  class zotero,arxiv,journals external
  class compose,bot,scheduler,sqlite,archive nas
  class embed,english,japanese gpu
  class dm,paper,log slack
```

## Daily Knowledge Pipeline

```mermaid
sequenceDiagram
  participant DSM as DSM Task Scheduler
  participant Sync as sync_zotero_pipeline.sh
  participant Zotero as Zotero API
  participant DB as SQLite
  participant PDF as PDF Archive
  participant Ollama as RTX PC Ollama
  participant Slack as Slack paperbot-log

  DSM->>Sync: run daily at 08:00
  Sync->>Ollama: verify embedding model
  Sync->>Zotero: fetch changed metadata
  Zotero-->>Sync: papers + attachment metadata
  Sync->>DB: upsert papers and duplicate flags
  Sync->>Zotero: download only missing/changed unique PDFs
  Sync->>PDF: store PDF files
  Sync->>DB: reuse unchanged chunks
  Sync->>Ollama: embed only new/changed chunks
  Ollama-->>Sync: embedding vectors
  Sync->>DB: save chunks and vectors
  Sync->>DB: rebuild lab interest profile
  Sync->>Slack: post success/failure summary
```

## Paper Watch Flow

```mermaid
flowchart TD
  start["Scheduled Paper Watch"] --> fetch["Fetch candidates\narXiv / RSS / Crossref"]
  fetch --> merge["Merge duplicates\nDOI first, normalized title fallback"]
  merge --> term["Profile term scoring\nmaterials / methods / physics / journals"]
  term --> rag["RAG similarity scoring\ncandidate abstract vs lab PDF chunks"]
  rag --> rank["Rank and filter\nmin score + post limit"]
  rank --> explain["Generate English commentary\ngpt-oss:20b"]
  explain --> translate["Translate to Japanese\nqwen3:14b"]
  translate --> post["Post compact messages\none paper per Slack post"]
  post --> seen[("seen_papers\navoid reposts")]

  classDef job fill:#eff6ff,stroke:#2563eb,color:#172554
  classDef ai fill:#ecfdf5,stroke:#059669,color:#064e3b
  classDef data fill:#f8fafc,stroke:#64748b,color:#0f172a
  classDef slack fill:#f5f3ff,stroke:#7c3aed,color:#2e1065
  class start,fetch,merge,term,rag,rank job
  class explain,translate ai
  class seen data
  class post slack
```

## Slack Interaction Flow

```mermaid
flowchart LR
  user["Lab member"] -->|"DM question"| bot["PaperBot"]
  bot -->|"embed query"| embed["nomic-embed-text"]
  bot -->|"retrieve chunks"| db[("SQLite RAG index")]
  db -->|"top sources"| bot
  bot -->|"grounded answer"| llm["gpt-oss:20b"]
  llm --> bot
  bot -->|"Japanese translation when needed"| qwen["qwen3:14b"]
  qwen --> bot
  bot -->|"answer + sources"| user

  classDef userCls fill:#f5f3ff,stroke:#7c3aed,color:#2e1065
  classDef botCls fill:#eff6ff,stroke:#2563eb,color:#172554
  classDef aiCls fill:#ecfdf5,stroke:#059669,color:#064e3b
  classDef dataCls fill:#f8fafc,stroke:#64748b,color:#0f172a
  class user userCls
  class bot botCls
  class embed,llm,qwen aiCls
  class db dataCls
```

## Ownership Boundaries

| Layer | Owner | Stored where | Notes |
| --- | --- | --- | --- |
| Paper metadata | Zotero | Zotero + SQLite mirror | Zotero remains the source of truth |
| PDFs | Zotero / NAS | `rag_poc/papers/zotero/` | Git never stores PDFs |
| RAG index | PaperBot | `rag_poc/index/chunks.sqlite3` | Incremental and rebuildable |
| LLM models | Ollama | RTX PC | No model weights on the NAS |
| Slack UI | Slack app | Slack workspace | Socket Mode, no public NAS endpoint |
| Secrets | Lab admin | NAS-local `.env` | Never committed |
