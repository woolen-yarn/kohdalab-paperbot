# Git Workflow

The production repository lives on the DS920+ at:

```bash
/volume1/docker/paperbot
```

The main private remote is:

```text
origin  git@github.com-paperbot:Kohdalab/kohdalab-paperbot.git
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
git status
make check
git add README.md docs rag_poc scripts bot.py pyproject.toml
git commit -m "Describe the change"
git push origin master
git push personal master
```

Push tags for releases:

```bash
git tag v0.1.0
git push origin v0.1.0
git push personal v0.1.0
```

## Deploy Key

The DS920+ uses a read-only GitHub deploy key for the private KohdaLab
repository. The key is registered in:

```text
Kohdalab/kohdalab-paperbot
Settings > Deploy keys
```

`~/.ssh/config` on DS920+ should contain a host alias:

```text
Host github.com-paperbot
  HostName github.com
  User git
  IdentityFile ~/.ssh/paperbot_deploy_key
  IdentitiesOnly yes
```

Test:

```bash
ssh -T github.com-paperbot
```

## Release Checklist

1. Update `CHANGELOG.md`.
2. Run `make check`.
3. Commit and push to `origin` and `personal`.
4. Tag the release, for example `v0.1.0`.
5. Push tags to both remotes.
6. Create GitHub Releases for the public mirror and private origin when needed.
7. Pull the release on DS920+ and redeploy the container.

## Branch Policy

`master` is the deployed branch. Keep changes small, testable, and easy to pull
on DS920+.

Runtime data is never recovered from Git. Back up `rag_poc/papers/`,
`rag_poc/index/`, and `.env` through NAS backup jobs.
