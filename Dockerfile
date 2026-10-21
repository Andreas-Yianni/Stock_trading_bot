# One image for both services (see docker-compose.yml): the Telegram bot and the Streamlit dashboard.
# Secrets (.env) and state (data/, config.yaml) are NOT baked in - they come from env_file and volumes.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so this layer is cached until requirements.lock changes.
COPY requirements.lock .
RUN pip install -r requirements.lock

# Non-root user that owns the writable state (data/ is a volume in compose).
RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /app/data \
    && chown bot:bot /app/data

COPY --chown=bot:bot tradebot/ tradebot/
COPY --chown=bot:bot telegram_bot.py dashboard.py cli.py config.yaml ./

USER bot

# Default command = the bot; the dashboard service overrides it.
CMD ["python", "telegram_bot.py"]
