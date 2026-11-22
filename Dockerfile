FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin app

COPY --chown=app:app LICENSE .
COPY --chown=app:app spot_history ./spot_history

RUN mkdir -p /app/data && chown app:app /app/data

USER app

VOLUME ["/app/data"]
EXPOSE 8345

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8345/health', timeout=5)"

CMD ["python", "-m", "spot_history", "web", "--host", "0.0.0.0", "--port", "8345"]
