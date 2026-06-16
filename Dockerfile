FROM python:3.12-slim

# Configurable port and host
ARG APP_PORT=8000
ARG APP_HOST=0.0.0.0
ENV APP_PORT=$APP_PORT
ENV APP_HOST=$APP_HOST

# Patch system packages (fixes base image CVEs)
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY concilia_engine/ concilia_engine/
COPY main.py .
COPY .env .

# Set ownership and switch user
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE $APP_PORT

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://localhost:$APP_PORT/docs')" || exit 1

CMD uvicorn main:app --host "$APP_HOST" --port "$APP_PORT"
