# Git Workflow

The production repository lives on the DS920+ at:

```bash
/volume1/docker/paperbot
```

Clone the public repository from GitHub.

Only source code and documentation are committed. Runtime files stay local:

- `.env`
- `logs/`
- `rag_poc/papers/`
- `rag_poc/index/`

## DS920+ Pull and Redeploy

```bash
cd /volume1/docker/paperbot
sudo git pull origin master
sudo docker compose -f docker-compose.nas.yml up -d --build paperbot
```

If scripts changed, run a manual smoke test:

```bash
sudo ./scripts/run_paper_watch.sh --dry-run --sources arxiv --post-limit 1 --no-summary
```

If RAG schema or chunking changed:

```bash
sudo REBUILD=1 ./scripts/sync_zotero_pipeline.sh
```

## Development Push

From a development workstation:

```bash
git switch master
git pull --ff-only origin master
git switch -c codex/describe-change
git status
make check
git add README.md docs rag_poc scripts bot.py pyproject.toml
git commit -m "Describe the change"
git push -u origin codex/describe-change
```

Open a pull request to `master` and wait for CI and review.

## Release Checklist

1. Update the version in `pyproject.toml`, the lock file, and the changelog.
2. Run `make check`.
3. Verify that the release tree contains only public configuration examples, source, and documentation.
4. Merge the reviewed pull request and create the GitHub release from the merge commit.

## Branch Policy

`master` is the deployed branch. Keep changes small, testable, and easy to pull
on DS920+.

Runtime data is never recovered from Git. Back up `rag_poc/papers/`,
`rag_poc/index/`, and `.env` through NAS backup jobs.
