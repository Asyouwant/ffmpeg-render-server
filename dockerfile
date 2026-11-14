FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.115.* "uvicorn[standard]==0.32.*"
COPY server.py .

ENV PORT=8000
EXPOSE 8000
CMD uvicorn server:app --host 0.0.0.0 --port ${PORT} --timeout-keep-alive 620
