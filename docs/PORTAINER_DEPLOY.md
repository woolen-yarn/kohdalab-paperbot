# Portainer Stack Deployment

Portainer can manage the PaperBot container while the repository remains on the
DS920+ filesystem. This is the preferred Portainer style for this project:

```text
DS920+ local repository
  /volume1/docker/paperbot
        |
        | build context
        v
Portainer Stack
        |
        v
kohdalab-paperbot container
```

Do not paste Slack or Zotero tokens into the Portainer UI. Keep secrets in:

```bash
/volume1/docker/paperbot/.env
```

## 1. Prepare Repository

```bash
cd /volume1/docker
git clone git@github.com-paperbot:Kohdalab/kohdalab-paperbot.git paperbot
cd /volume1/docker/paperbot
mkdir -p rag_poc/papers/zotero rag_poc/index logs
cp .env.example .env
vi .env
```

## 2. Confirm Portainer Can See the Repository

The Portainer container must have access to `/volume1/docker`.

Check mounts:

```bash
sudo docker inspect portainer --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
```

Recommended mount:

```text
/volume1/docker -> /volume1/docker
```

If it is missing, add the volume mount through Synology Container Manager and
restart Portainer.

## 3. Create Stack

In Portainer:

```text
Stacks
> Add stack
> Web editor
```

Use this compose file:

```yaml
services:
  paperbot:
    build:
      context: /volume1/docker/paperbot
      dockerfile: Dockerfile
    image: kohdalab-paperbot:local
    container_name: kohdalab-paperbot
    restart: unless-stopped
    env_file:
      - /volume1/docker/paperbot/.env
    volumes:
      - /volume1/docker/paperbot/rag_poc/papers:/app/rag_poc/papers
      - /volume1/docker/paperbot/rag_poc/index:/app/rag_poc/index
      - /volume1/docker/paperbot/logs:/app/logs
    command: ["python", "bot.py"]
```

Deploy the stack.

## 4. Update Stack

Pull code on DS920+:

```bash
cd /volume1/docker/paperbot
sudo git pull origin master
```

Then in Portainer:

```text
Stacks
> paperbot
> Editor
> Update the stack
> Re-pull image and redeploy OFF
```

Because the image is built locally from `/volume1/docker/paperbot`, updating the
stack rebuilds from the current repository content.

## 5. Runtime Jobs

Scheduled jobs are easier to run with DSM Task Scheduler than Portainer.

Daily sync:

```bash
cd /volume1/docker/paperbot && ./scripts/sync_zotero_pipeline.sh
```

Weekly arXiv:

```bash
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources arxiv
```

Monthly journal jobs:

```bash
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources rss --rss-groups pr,pr_ext
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources rss --rss-groups nature,nature_ext
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources rss --rss-groups aip
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources rss --rss-groups nano_2d,broad_high
```

Run these DSM tasks as `root`.

## Troubleshooting

`env file ... .env not found`

Create `/volume1/docker/paperbot/.env` from `.env.example`.

`Bind mount failed: logs does not exist`

Create runtime folders:

```bash
mkdir -p /volume1/docker/paperbot/rag_poc/papers/zotero
mkdir -p /volume1/docker/paperbot/rag_poc/index
mkdir -p /volume1/docker/paperbot/logs
```

`unable to prepare context: path "/volume1/docker/paperbot" not found`

Portainer cannot see the NAS path. Mount `/volume1/docker` into the Portainer
container.

`token is invalid`

Check `SLACK_BOT_TOKEN` in `.env`, then recreate the container.
