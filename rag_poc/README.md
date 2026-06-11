# RAG Internals

`rag_poc/` contains the local RAG and paper monitoring implementation used by
KohdaLab PaperBot. Despite the directory name, the current deployment is the
DS920+ production setup described in the top-level README.

## Components

| File | Role |
| --- | --- |
| `zotero_sync.py` | Fetch Zotero metadata, deduplicate papers, and download unique PDFs |
| `ingest.py` | Extract PDF text, chunk it, embed chunks, and store them in SQLite |
| `ask.py` | Search SQLite and generate grounded answers through Ollama |
| `paper_watch.py` | Fetch new papers, score them, and post recommendations to Slack |
| `lab_profile.py` | Build the lab interest profile from the indexed paper collection |
| `ollama_client.py` | Minimal Ollama chat and embedding client |
| `sync_notify.py` | Send pipeline success/failure summaries to Slack |

## Data Layout

Runtime data is local to the NAS and ignored by Git.

```text
rag_poc/
├─ papers/
│  └─ zotero/          # Zotero PDF archive
└─ index/
   ├─ chunks.sqlite3   # papers, chunks, embeddings, seen papers
   ├─ ingest_report.json
   ├─ lab_profile.json
   └─ lab_profile.md
```

## SQLite Tables

The SQLite database is the single local index. Important records include:

- `papers`: Zotero paper metadata, duplicate flags, PDF status
- `pdf_documents`: file hash, chunk count, zero-text and duplicate status
- `chunks`: extracted text chunks and embedding vectors
- `seen_papers`: Paper Watch deduplication history

## Ingest Behavior

`ingest.py` is incremental by default.

- unchanged PDFs reuse existing chunks
- changed PDFs are re-extracted and re-embedded
- duplicate PDF binaries are detected by hash
- zero-text PDFs are recorded but not indexed
- deleted or removed sources are deactivated from the active index

Manual ingest on DS920+:

```bash
cd /volume1/docker/paperbot
sudo docker compose -f docker-compose.nas.yml run --rm ingest python rag_poc/ingest.py --source-prefix zotero/
```

Full rebuild:

```bash
sudo docker compose -f docker-compose.nas.yml run --rm ingest python rag_poc/ingest.py --rebuild --source-prefix zotero/
```

## Ask

Manual RAG query:

```bash
cd /volume1/docker/paperbot
sudo docker compose -f docker-compose.nas.yml run --rm ask python rag_poc/ask.py "Persistent Spin Helixについて一文で教えて"
```

The Slack bot uses the same search and answer path.

## Paper Watch

Paper Watch combines source-specific fetching with lab-specific scoring.

```bash
cd /volume1/docker/paperbot
sudo ./scripts/run_paper_watch.sh --dry-run --sources arxiv --post-limit 3
sudo ./scripts/run_paper_watch.sh --dry-run --sources rss --rss-groups nature_family --no-summary
```

The default Slack output is compact:

- title
- authors
- source/score
- English technical commentary
- Japanese translation
- nearest lab PDF
- link

## Lab Profile

The generated profile is rebuilt by the daily Zotero/RAG pipeline and overwrites
`lab_profile.json` and `lab_profile.md`. It is both a readable summary and a
scoring prior for Paper Watch.

- `core_themes`: curated lab themes such as PSH in III-V quantum wells,
  structured-light control of spin textures, and 2D exciton/valley spin dynamics
- `hot_topics`: recently growing materials, methods, physics, or applications
- `theme_combinations`: frequent material-method-physics combinations
- `weighted_terms`: terms and weights consumed by Paper Watch scoring
- `negative_profile`: peripheral topics that should reduce recommendation score
- `categories`: material systems, methods, physics concepts, applications,
  journals, and normalized authors

Paper Watch reads this profile automatically when it exists. Manual
`PAPER_WATCH_TERMS` still works as an override, but normal production should use
the generated profile plus the built-in default terms.
