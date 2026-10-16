FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends openssh-client iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY jarvis ./jarvis
COPY web ./web

ENV JARVIS_HOST=0.0.0.0 \
    JARVIS_PUERTO=8765 \
    JARVIS_DATA=/app/data \
    PYTHONUNBUFFERED=1

EXPOSE 8765
VOLUME ["/app/data"]
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/api/ping')"
CMD ["python", "-m", "jarvis"]
