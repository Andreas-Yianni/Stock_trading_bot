# CI/CD plan for the trading bot (portfolio project)

Goal: show DevOps skills on this real codebase. Every push to GitHub runs lint + tests
automatically; images are built and pushed to a registry; deploys to the cloud server happen
automatically from `main`. The bot's behaviour must not change because of this work.

Later direction (do NOT start until this plan is done): split into services
(market data, signals/engine, broker/execution, notifications) with Docker Compose.

## What the app actually is (read CLAUDE.md for details)
- `telegram_bot.py` – long-running worker (polling + scheduled jobs: morning scan,
  check_trades every 10 min, weekly/monthly jobs). Not a web API.
- `dashboard.py` – Streamlit dashboard (port 8501). It can write `config.yaml`, so it must
  NOT be exposed publicly without protection.
- `tradebot/` – engine, indicators, levels, backtest, journal (SQLite), portfolio, broker
  (Alpaca PAPER via `requests`), autoscan, selfcheck, etc.
- `cli.py`, `research/` (research tools, not part of the running bot).
- State: `data/` (~1 GB, git-ignored) and `config.yaml` (rewritten at runtime).
- Secrets in `.env` only (Telegram token/chat id, Alpaca paper keys).
- Cloud target: Hetzner VPS (Ubuntu 24.04) – see ΠΟΥ_ΕΜΕΙΝΑΜΕ.md; server not created yet.

## About me
- New to Docker / GitHub Actions / deployment: explain each new concept briefly once.
- Windows + PowerShell for local commands. Talk to me in Greek; code/commits/docs in English.

## Roadmap (one phase at a time – stop and let me test before the next)
1. **Explore & deps** – summarize structure first. Add a pinned `requirements.lock`
   (or pins) from the current working venv, plus `requirements-dev.txt`
   (pytest, pytest-cov, ruff). Add `pyproject.toml` with a lenient ruff config
   (don't reformat the whole codebase in one go).
2. **Tests** – `tests/` with pytest for pure logic first: indicators, levels, engine scoring,
   `portfolio.ticket` / sizing, `broker` sizing + `health()` circuit breaker, journal replay.
   ALL network calls (Telegram, Alpaca, Yahoo/yfinance, ccxt, news feeds) mocked; tests use
   small fixture DataFrames and a temp SQLite DB, never the real `data/` or `config.yaml`.
3. **CI** – `.github/workflows/ci.yml`: on push/PR → Python 3.x, pip cache, ruff, pytest with
   coverage. Status badge in README.
4. **Docker** – `Dockerfile` (slim Python, non-root user) and `docker-compose.yml` with two
   services from the same image: `bot` (python telegram_bot.py, restart: unless-stopped) and
   `dashboard` (streamlit, bound to 127.0.0.1 only). Volumes for `data/` and `config.yaml`;
   `.env` passed as env_file, never baked into the image. `.dockerignore`. Test locally
   with Docker Desktop.
5. **Build & push** – on push to `main`, only if CI passes, build the image and push to
   GitHub Container Registry (ghcr.io), tagged with commit SHA and `latest`.
6. **CD to Hetzner** (when the server exists) – deploy job: SSH into the server with a
   dedicated deploy key stored as a GitHub secret, `docker compose pull && docker compose up -d`.
   Server setup documented as a script (`deploy/setup.sh`): Docker, firewall (only SSH open),
   non-root user. Dashboard reached via SSH tunnel, not a public port.
7. **Hardening** – branch protection on `main` (PR + green CI), Dependabot (pip + actions),
   healthcheck for the bot container (e.g. heartbeat file), alert on failed deploy.
8. **Showcase** – README in English: what it does, architecture + pipeline diagram (Mermaid),
   how to run locally / with Docker, tech stack, badges. Screenshots of the dashboard.

## Rules
- Never commit `.env`, keys, tokens or anything in `data/`.
- Alpaca stays PAPER only (`paper-api.alpaca.markets`) – never touch that.
- Don't change trading logic, thresholds or `config.yaml` values as part of CI/CD work.
- `start_bot.bat` / `start_dashboard.bat` must keep working (keep them ASCII after chcp).
- Don't move/rename modules without asking me first.
- Work on branch `ci-cd`; small commits; merge to `main` via Pull Request.
- If something fails, explain the cause before changing things. Free tiers only
  (except the Hetzner server I already chose).

## Current status
Phase 0 – git repo initialized and pushed (2026-10-21) to the private repo
github.com/Andreas-Yianni/Stock_trading_bot (default branch `main`; work on `ci-cd`).
Phase 1 – done on branch `ci-cd` (2026-10-21): `requirements.lock` (pip freeze of the
Python 3.14 venv; `winloop` Windows-only, `uvloop` elsewhere, verified all pins have Linux
cp314 wheels), `requirements-dev.txt` (pinned pytest/pytest-cov/ruff + deps),
`pyproject.toml` (ruff `E9`+`F` only – passes; a stricter E/W/I/B/UP set would report
82 style issues, `ruff format` would touch 26 files – both left for later).
Phase 2 – done on branch `ci-cd` (2026-10-21): `tests/` with 94 pytest tests (~3 s) for
indicators, levels, engine (scoring, filters, `_build_plan`), portfolio (`round_qty`, `ticket`,
open/close + balance), broker (Alpaca sizing, paper-only guards, `health()`, `pause`) and journal
(`simulate`, `replay`, `record`). `tests/conftest.py` isolates every test: temp `config.yaml`
(`config.ROOT`), temp journal DB and broker state files, fake `PK…` keys, sockets blocked.
Coverage 27% overall (indicators/levels 100%, engine/journal 68%, portfolio 52%, broker 49%).
Fixed afterwards (user-approved): `_conn()` in journal/portfolio now closes the SQLite connection
(context manager: commit/rollback + close) → no more `ResourceWarning`s.
Also (2026-10-21): `telegram_bot.py` logs to `data/bot.log` too (rotating 5 × 2 MB), because the bot
had stopped unnoticed and the console log was lost.
Phase 3 – done on branch `ci-cd` (2026-10-21): `.github/workflows/ci.yml` – on every push, PRs to
`main` and manual dispatch: ubuntu-latest, Python 3.14 with pip cache, `requirements.lock` +
`requirements-dev.txt`, `ruff check`, `pytest --cov` (coverage table in the run summary,
`coverage.xml` artifact). No secrets needed. First run green. CI badge in README (visible only to
the owner while the repo is private).
Merged to `main` via PR #1.
Phase 4 – done on branch `ci-cd` (2026-10-21): `Dockerfile` (python:3.14-slim, deps layer from
`requirements.lock`, non-root user `bot` uid 1000, only `tradebot/`, the 3 entry points and a default
`config.yaml` copied; image ≈ 274 MB compressed / 1.2 GB on disk), `.dockerignore` (no .env, data,
tests, research, docs), `docker-compose.yml` (`bot` + `dashboard` from the same image
`ghcr.io/andreas-yianni/stock_trading_bot:latest`, restart unless-stopped, `env_file: .env`, volumes
`./data` and `./config.yaml`, dashboard on 127.0.0.1:8501 only, no depends_on so the dashboard can run
alone). Verified on Docker Desktop with scratch copies of data/config (the real bot was running): runs as
uid 1000, no .env in the image, bot exits cleanly without token, `cli.py AAPL 1d` works on Linux,
dashboard health OK and AppTest renders with no exceptions.
Notes for phase 6: never run the Docker bot and `start_bot.bat` at the same time (same Telegram token →
polling conflict, duplicate scans); on the server `config.yaml` and `data/` must exist before
`docker compose up` (a missing bind-mounted file becomes a directory) and be writable by uid 1000.
Phase 5 – on branch `ci-cd` (2026-10-21): job `docker` in `ci.yml`, `needs: test` (runs only if lint +
tests pass). On pull requests to `main` it only builds the image (Dockerfile check); on push to `main` it
also logs in with the built-in `GITHUB_TOKEN` (`packages: write`, no extra secret) and pushes
`ghcr.io/andreas-yianni/stock_trading_bot:<full commit sha>` and `:latest`. Buildx with GitHub Actions
layer cache. The package is private (like the repo): the server will need a read-only token to pull.
Merged to `main` via PR #2; the `main` run pushed the image (CI + docker job green).
Phase 6 – prepared (2026-10-22, merged to `main` via PR #3; deploy job correctly skipped), server not created yet: `deploy/setup.sh` (one-time,
as root: updates + unattended-upgrades, Docker from the official repo with rotated logs, 2 GB swap,
user `tradebot` uid 1000 in the docker group, `/opt/tradebot`, key-only SSH, ufw with only SSH open),
`deploy/deploy.sh` (flock, checks `.env`/`config.yaml` exist, `docker compose pull && up -d`, fails if the
bot isn't running after 20 s, prunes old images), `deploy/README.md` (step-by-step incl. state copy,
ghcr login with a `read:packages` token, GitHub settings). Job `deploy` in `ci.yml` (`needs: docker`, push
to `main` only, skipped until repo variable `DEPLOY_HOST` exists; secrets `DEPLOY_SSH_KEY`,
`DEPLOY_KNOWN_HOSTS` = pinned host key). The deploy key is a forced command in `authorized_keys`
(`command="/opt/tradebot/deploy.sh",restrict`): it can only deploy. Workflow no longer cancels
in-progress runs on `main` (a deploy must not be cut off). `*.sh` forced to LF in `.gitattributes`.
Checked with shellcheck; not yet run on a real server. The x86 CX23 was sold out everywhere, so the
server is an Arm64 Ampere (CAX): the docker job builds `linux/amd64,linux/arm64` (QEMU); all pinned deps have
cp314 aarch64 wheels (checked on PyPI).
Phase 6 – server live (2026-10-22): Hetzner CAX11 (Arm64, Falkenstein, `2.31.77.113`); the x86 CX23 was sold
out everywhere. setup.sh run (fix: `/run/sshd` for `sshd -t` under socket activation), server rebooted after updates.
Verified on the server: native arm64 build, 94 tests pass, `cli.py AAPL 1d` (Yahoo works from Hetzner), bot
starts, Alpaca paper account reachable, dashboard health 200, password SSH refused, deploy key runs only
`deploy.sh`. State copied (`.env`, `config.yaml`, `data/` without research; CRLF stripped). Local bot off.
GitHub: variable `DEPLOY_HOST`, secrets `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS` set; deploy also on "Run workflow" on main.
