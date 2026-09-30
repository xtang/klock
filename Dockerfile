FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=5188 KLOCK_DB=/data/klock.db
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && groupadd --gid 10001 klock && useradd --uid 10001 --gid klock --no-create-home klock
COPY server.py google_login.py ./
COPY public ./public
USER 10001:10001
EXPOSE 5188
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5188/api/state', timeout=3)"
CMD ["python", "server.py"]
