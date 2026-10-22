# Deploying to the Hetzner server

Target: Hetzner Cloud, cheapest x86 (CX), Ubuntu 24.04, created with the SSH key `~/.ssh/tradebot_vps`.
After this one-time setup, every push to `main` that passes CI is deployed automatically:

```
push to main → lint & tests → build + push image to ghcr.io → SSH to server → deploy.sh
                                                               (docker compose pull && up -d)
```

| On the server | |
|---|---|
| `/opt/tradebot/` | `docker-compose.yml`, `deploy.sh`, `.env`, `config.yaml`, `data/` |
| user `tradebot` (uid 1000) | owns the app, runs docker; the container user is uid 1000 too |
| firewall | only SSH (22) open; dashboard only via SSH tunnel |
| SSH | keys only; the GitHub deploy key may run nothing but `deploy.sh` |

Commands below are for **PowerShell** on the Windows PC, from the repo folder, unless marked *server*.
Replace `SERVER` with the server's IPv4.

## 1. Deploy key for GitHub Actions (on the PC)

A separate key, so the personal key never goes to GitHub:

```powershell
ssh-keygen -t ed25519 -f $HOME\.ssh\tradebot_deploy -N '""' -C github-deploy
```

## 2. Server setup (once)

```powershell
mkdir $env:TEMP\tb-setup -Force
copy deploy\setup.sh, deploy\deploy.sh, docker-compose.yml, $HOME\.ssh\tradebot_deploy.pub $env:TEMP\tb-setup\
ren $env:TEMP\tb-setup\tradebot_deploy.pub deploy_key.pub
scp -i $HOME\.ssh\tradebot_vps -r $env:TEMP\tb-setup root@SERVER:/root/tradebot-setup
ssh -i $HOME\.ssh\tradebot_vps root@SERVER bash /root/tradebot-setup/setup.sh
```

`setup.sh` installs updates + automatic security updates, Docker (official repo, rotated logs),
2 GB swap, user `tradebot`, the keys, `/opt/tradebot`, key-only SSH and the firewall.
It can be run again safely.

## 3. Bot state: `.env`, `config.yaml`, `data/` (once)

**Stop the local bot first** (close `start_bot.bat`): two bots with the same Telegram token conflict
and would both run the scans and the Alpaca jobs. From now on only the server bot runs.

```powershell
tar -czf $env:TEMP\tb-state.tgz --exclude=data/research .env config.yaml data
scp -i $HOME\.ssh\tradebot_vps $env:TEMP\tb-state.tgz tradebot@SERVER:/opt/tradebot/
ssh -i $HOME\.ssh\tradebot_vps tradebot@SERVER "cd /opt/tradebot && tar -xzf tb-state.tgz && rm tb-state.tgz && chmod 600 .env"
del $env:TEMP\tb-state.tgz
```

(`data/research` is ~1 GB of research caches and is not needed by the bot.)

## 4. Let the server pull the private image (once)

GitHub → Settings → Developer settings → Personal access tokens → **Tokens (classic)** →
new token with only `read:packages`, no expiry or a long one. Then:

```powershell
ssh -i $HOME\.ssh\tradebot_vps tradebot@SERVER
```
```bash
# server
docker login ghcr.io -u Andreas-Yianni      # paste the token as password
/opt/tradebot/deploy.sh                     # first deploy by hand
docker compose -f /opt/tradebot/docker-compose.yml logs -f bot
```

## 5. Turn on automatic deploys (GitHub → repo → Settings → Secrets and variables → Actions)

| Kind | Name | Value |
|---|---|---|
| Variable | `DEPLOY_HOST` | the server IPv4 |
| Secret | `DEPLOY_SSH_KEY` | contents of `~/.ssh/tradebot_deploy` (the **private** file) |
| Secret | `DEPLOY_KNOWN_HOSTS` | output of `ssh-keyscan -t ed25519 SERVER` (check its fingerprint matches `ssh-keygen -lf` on the server's `/etc/ssh/ssh_host_ed25519_key.pub`) |

Until `DEPLOY_HOST` exists the `deploy` job is skipped, so CI stays green.
Test: Actions → CI → *Run workflow* on `main` (or merge a PR).

## Everyday use

```bash
# dashboard: tunnel from the PC, then open http://localhost:8501
ssh -i ~/.ssh/tradebot_vps -L 8501:127.0.0.1:8501 tradebot@SERVER

# server, in /opt/tradebot
docker compose ps                  # status
docker compose logs -f bot         # live log (also data/bot.log)
docker compose restart bot
docker compose exec bot python cli.py AAPL 1d --no-news
```

- A change to `docker-compose.yml` is not deployed automatically: copy it with
  `scp -i ~/.ssh/tradebot_vps docker-compose.yml tradebot@SERVER:/opt/tradebot/`, then run `deploy.sh`.
- `config.yaml` on the server is the live one (the bot rewrites it); the repo copy is only the default.
- Rollback: on the server, `docker pull ghcr.io/andreas-yianni/stock_trading_bot:<older sha>`,
  `docker tag` it as `:latest`, `docker compose up -d` — or revert the commit on `main`.
