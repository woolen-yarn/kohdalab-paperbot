# Changelog

## v1.0.0 - Unreleased

First stable release of the KohdaLab DS920+ and RTX PaperBot platform.

### Added

- Multi-paper RAG scoring using the strongest representative chunk from each of
  the top three distinct indexed lab papers.
- Separate title and abstract embeddings with calibrated 0--100 recommendation
  scores.
- Adaptive RAG calibration blended with a stable absolute baseline.
- Automatic lab-profile regeneration after successful RAG ingest.
- Profile-derived author, journal, term, and theme-combination priorities.
- Unit tests for scoring, profile sampling, and live RAG-index reload behavior.

### Changed

- Added synonym grouping, title weighting, and diminishing returns for keyword
  concepts to reduce keyword stuffing and double counting.
- Selected semantically diverse reference chunks from across each indexed PDF.
- Cached normalized chunk embeddings to reduce repeated vector normalization.
- Batched each candidate's title and abstract into one Ollama embedding request.
- Streamed profile chunk sampling instead of retaining all chunk text in memory.
- Made unit tests part of the default CI check.
- Changed the default report threshold to 45 on the 0--100 scale.

### Fixed

- Removed scheduled restarts of the always-on Slack container. The bot now
  reloads the SQLite RAG index when its modification time changes.
- Prevented Synology from reporting the routine scheduled refresh as an
  unexpected `kohdalab-paperbot` container stop.

### Compatibility

- No Paper Watch database schema migration is required.
- Existing runtime databases, PDFs, `.env`, and scheduled metadata remain local
  to the NAS and are not included in the release.

## v0.2.0 - 2026-06-11

Production schedule and Paper Watch refinement release.

### Changed

- Updated DSM production schedule to match the deployed NAS tasks:
  - `Paperbot` daily at 01:00
  - `Paperbot-collect` daily at 02:00
  - weekly arXiv report every Monday at 09:30
  - monthly journal reports staggered across the first through fourth Mondays
- Capped Paper Watch reports at five papers per report.
- Unified Paper Watch groups so collection and report groups use the same
  canonical names.
- Removed legacy Paper Watch group aliases from the new configuration.
- Split Paper Watch collection from report posting:
  - daily collection stores metadata in `paper_watch.sqlite3`
  - post-collection RAG enrichment updates recent unscored candidates
  - weekly/monthly report tasks post from stored metadata

### Added

- Additional recommended Paper Watch journals including Communications
  Materials, npj Spintronics, npj Quantum Materials, APL Quantum, ACS Applied
  Nano Materials, ACS Applied Electronic Materials, Journal of Materials
  Chemistry C, and npj 2D Materials and Applications.
- GitHub badges for CI, MIT license, release, and Python version.

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
