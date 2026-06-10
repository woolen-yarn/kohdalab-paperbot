# Changelog

## v0.1.0 - 2026-06-10

Initial production release for the DS920+ and RTX PC deployment.

### Added

- Slack Socket Mode bot for DM-based paper RAG.
- Zotero Group Library sync with metadata storage, duplicate detection, and
  unique PDF download.
- Incremental SQLite RAG index with PDF hash tracking, zero-text reporting, and
  duplicate PDF suppression.
- Local Ollama integration:
  - `gpt-oss:20b` for answers and English technical commentary
  - `qwen3:14b` for Japanese translation
  - `nomic-embed-text` for embeddings
- Synology DS920+ pipeline script with Slack success/failure notifications.
- Paper Watch for arXiv, APS Physical Review, Nature-family journals, AIP, and
  nano/2D materials sources.
- RAG-based candidate scoring for Paper Watch.
- Compact one-paper-per-message Slack posting for `#paper`.
- Generated lab interest profile from the indexed PDF corpus.
- Production README with architecture and DSM Task Scheduler documentation.
- MIT license.

### Operations

- Daily Zotero/RAG sync: `Paperbot` at 08:00.
- Weekly arXiv watch: `Paperbot-arXiv` every Monday at 09:00.
- Monthly journal watches staggered across the first through fourth Mondays.

### Notes

- Runtime data is intentionally excluded from Git: `.env`, `logs/`,
  `rag_poc/papers/`, and `rag_poc/index/`.
- Zotero and Slack credentials must be stored only in the NAS-local `.env`.
