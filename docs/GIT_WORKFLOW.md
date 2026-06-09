# Git Workflow

このrepoはGitHub private repoで管理し、DS920+はGitから取得してDocker Composeで起動します。

```text
Mac
  edit -> commit -> push

GitHub private repo
  source of code/config templates

DS920+
  git pull -> docker compose up -d --build paperbot

RTX PC
  Ollama API server
```

## Mac側

```bash
cd /Users/kikuchikeito/projects/llm
git status
git add .
git commit -m "Update PaperBot"
git push
```

GitHubに入れるもの:

```text
Dockerfile
docker-compose.nas.yml
bot.py
rag_poc/*.py
requirements.txt
README.md
docs/
.env.example
```

GitHubに入れないもの:

```text
.env
logs/
rag_poc/papers/*.pdf
rag_poc/index/
.venv/
__pycache__/
```

## DS920+ 初回

Private repoなので、DS920+からcloneするにはGitHub認証が必要です。
おすすめは読み取り専用のSSH deploy keyです。

DS920+にSSHで入り、鍵を作ります。

```bash
ssh-keygen -t ed25519 -C "ds920-paperbot" -f ~/.ssh/paperbot_deploy_key
cat ~/.ssh/paperbot_deploy_key.pub
```

GitHub repoの `Settings` -> `Deploy keys` -> `Add deploy key` に公開鍵を追加します。
`Allow write access` はOFFのままでOKです。

DS920+のSSH設定に、この鍵を使う設定を追加します。

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com-paperbot
  HostName github.com
  User git
  IdentityFile ~/.ssh/paperbot_deploy_key
  IdentitiesOnly yes
EOF

chmod 600 ~/.ssh/config ~/.ssh/paperbot_deploy_key
```

接続確認:

```bash
ssh -T github.com-paperbot
```

cloneします。

```bash
cd /volume1/docker
git clone git@github.com-paperbot:woolen-yarn/kohdalab-paperbot.git paperbot
cd paperbot
cp .env.example .env
```

`.env` にSlack tokenを入れます。

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
OLLAMA_BASE_URL=http://10.32.145.143:11434
```

PDFとindexを配置します。

```text
/volume1/docker/paperbot/rag_poc/papers/
/volume1/docker/paperbot/rag_poc/index/chunks.jsonl
```

起動します。

```bash
docker compose -f docker-compose.nas.yml up -d --build paperbot
```

## DS920+ 更新

```bash
cd /volume1/docker/paperbot
sh scripts/deploy_nas.sh
```

PDFを更新したとき:

```bash
cd /volume1/docker/paperbot
sh scripts/reindex_nas.sh
```

## Portainer

Portainerを使う場合は、StackのGit repository機能よりも、最初はDS920+上で `git pull` してからPortainerでredeployする方式が簡単です。

Stack settings:

```text
Name: paperbot
Path: /volume1/docker/paperbot
Compose file: docker-compose.nas.yml
```

更新時:

```text
DS920+ SSH: sh scripts/deploy_nas.sh
Portainer: Stackの状態とログを確認
```
