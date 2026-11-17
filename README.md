# KohdaLab PaperBot

[![CI](https://github.com/woolen-yarn/kohdalab-paperbot/actions/workflows/ci.yml/badge.svg)](https://github.com/woolen-yarn/kohdalab-paperbot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/woolen-yarn/kohdalab-paperbot?sort=semver)](https://github.com/woolen-yarn/kohdalab-paperbot/releases)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](pyproject.toml)

KohdaLab PaperBot collects, searches, and recommends research papers for the lab. Zotero remains the source of truth, a Synology NAS stores PDFs and SQLite databases, an RTX PC runs two llama.cpp servers for generation and embeddings, and Slack provides the user interface.

## What It Does

- Incrementally synchronizes paper metadata and PDFs from a Zotero Group Library.
- Indexes PDF text for cited RAG answers through Slack direct messages.
- Collects new papers from arXiv and major journal sources.
- Scores each candidate from 0 to 100 against the lab's indexed research.
- Posts high-scoring papers to `#paper` on weekly and monthly schedules.
- Reports synchronization and job status to `#paperbot-log`.

## System Overview

| Component | Responsibility |
| --- | --- |
| Synology DS920+ | Docker, scheduled jobs, PDFs, SQLite, and the Slack bot |
| RTX PC | llama.cpp generation, CPU embeddings, and Japanese translation |
| Zotero | Source of truth for paper metadata and PDF attachments |
| Slack | RAG questions, paper recommendations, and operations notices |

See [Architecture](docs/ARCHITECTURE.md) for diagrams and data flows.

## Quick Start

The production deployment uses:

```text
/volume1/docker/paperbot
```

Follow the [DS920+ deployment and operations guide](docs/DS920_DEPLOY.md) for the full `.env`, llama.cpp, Slack, Zotero, and DSM Task Scheduler setup.

The basic startup and first synchronization are:

```bash
cd /volume1/docker/paperbot
cp .env.example .env
sudo docker compose -f docker-compose.nas.yml up -d --build paperbot
sudo ./scripts/sync_zotero_pipeline.sh
```

## Slack Usage

- Send the bot a direct message to query the indexed papers with RAG.
- Send `status` to inspect llama.cpp, SQLite, Zotero, and index status.
- Send `sources` to display the references used for the previous answer.
- Read `#paper` for Paper Watch recommendations.
- Read `#paperbot-log` for synchronization and scheduled-job results.

## Recommendation Scoring

Paper Watch assigns every candidate a score from 0 to 100:

\[
S = 45K + 35R + 10C + 5A + 5J
\]

| Symbol | Weight | Signal |
| --- | ---: | --- |
| `K` | 45 | Important terms in the title and abstract |
| `R` | 35 | RAG similarity to indexed lab PDFs |
| `C` | 10 | Material, method, physics, and application combinations |
| `A` | 5 | Authors prioritized by the lab profile or configuration |
| `J` | 5 | Journals prioritized by the lab profile or configuration |

A title match receives 1.5 times the weight of an abstract-only match. Synonyms are grouped so that only the strongest match for one concept contributes. Additional matched concepts receive diminishing weight, using a default decay of 0.85.

RAG embeds the title and abstract separately, keeps one best match from each lab PDF, and aggregates the top three distinct papers with weights of `0.6 / 0.3 / 0.1`. It then blends stable absolute calibration with calibration relative to the current candidate set.

Every successful RAG ingest regenerates `lab_profile.json` and `lab_profile.md`. Their terms, themes, authors, journals, and negative signals affect later Paper Watch scores as the indexed lab collection changes.

See [Scoring](docs/SCORING.md) for the complete Term-score formula, multi-paper RAG aggregation, calibration, environment variables, and stored database fields.

## Rescoring Existing Data

Apply the current profile and scoring model to existing unposted records. Preview the result before writing:

```bash
sudo ./scripts/run_paper_watch.sh --mode rescore --lookback-days 180 --dry-run
sudo ./scripts/run_paper_watch.sh --mode rescore --lookback-days 180
```

The write run creates a timestamped `paper_watch.sqlite3.backup-*` file first. It does not change the database schema or posted history. Add `--include-posted` only when historical posted records must also be rescored.

## Runtime Data

| NAS path | Contents |
| --- | --- |
| `rag_poc/papers/zotero/` | PDFs synchronized from Zotero |
| `rag_poc/index/chunks.sqlite3` | Papers, chunks, and embeddings |
| `rag_poc/index/paper_watch.sqlite3` | Candidates, classifications, scores, and post state |
| `rag_poc/index/lab_profile.json` | Machine-readable lab scoring profile |
| `rag_poc/index/lab_profile.md` | Human-readable lab profile |
| `logs/` | Synchronization and Paper Watch logs |

Git does not track `.env`, PDFs, SQLite databases, embeddings, or logs.

## Documentation

- [DS920+ deployment and operations](docs/DS920_DEPLOY.md): production setup, schedules, upgrades, and troubleshooting.
- [Scoring](docs/SCORING.md): Term scoring, RAG, final scoring, and rescoring.
- [Architecture](docs/ARCHITECTURE.md): relationships among the NAS, RTX PC, Slack, and Zotero.
- [Local model evaluation](docs/MODEL_EVALUATION.md): PaperBot task benchmark and selected llama.cpp configuration.
- [RAG internals](rag_poc/README.md): synchronization, ingest, retrieval, and Paper Watch.
- [Git workflow](docs/GIT_WORKFLOW.md): `origin`, `personal`, and pull request workflow.
- [Changelog](CHANGELOG.md): release history.

## Development

Run the same checks used by CI:

```bash
make check
```

## Release

The current stable release is [v1.1.0](https://github.com/woolen-yarn/kohdalab-paperbot/releases/tag/v1.1.0).

## License

[MIT License](LICENSE)
