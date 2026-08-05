# Git Workflow

The production repository lives on the DS920+ at:

```bash
/volume1/docker/paperbot
```

The main private remote is:

```text
origin  git@github.com:woolen-yarn/kohdalab-paperbot.git
```

An optional public mirror can be kept as:

```text
personal  git@github.com:woolen-yarn/kohdalab-paperbot.git
```

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
git push -u personal codex/describe-change
```

Open pull requests from the same branch to `master` in both repositories. Do
not push feature or release changes directly to `master`.

After both release PRs are reviewed and merged, tag the shared reviewed branch
head so the same tag identifies the same source commit in both repositories:

```bash
git switch codex/prepare-v1-release
make check
git tag -a v1.0.0 -m "KohdaLab PaperBot v1.0.0"
git push origin v1.0.0
git push personal v1.0.0
```

## Deploy Key

The DS920+ uses a read-only GitHub deploy key for the private KohdaLab
repository. The key is registered in:

```text
woolen-yarn/kohdalab-paperbot
Settings > Deploy keys
```

`~/.ssh/config` on DS920+ should contain a host alias:

```text
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
```

Test:

```bash
ssh -T github.com
```

## Release Checklist

1. Set the version in `pyproject.toml` and replace `Unreleased` in
   `CHANGELOG.md` with the release date.
2. Run `make check` and the NAS Docker Compose smoke check.
3. Commit the release candidate and push the same branch to `origin` and
   `personal`.
4. Open PRs to `master` in both repositories and wait for CI/review.
5. Merge both PRs and verify their source trees are identical.
6. Create the annotated tag on the shared reviewed branch-head commit.
7. Push the identical tag to both remotes.
8. Create GitHub Releases for the private origin and public mirror.
9. Pull `origin/master` on DS920+, rebuild, and verify Slack/RAG health.

## Branch Policy

`master` is the deployed branch. Keep changes small, testable, and easy to pull
on DS920+.

Runtime data is never recovered from Git. Back up `rag_poc/papers/`,
`rag_poc/index/`, and `.env` through NAS backup jobs.
