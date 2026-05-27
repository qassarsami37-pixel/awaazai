FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY voice-app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY voice-app/ ./voice-app/

# Railway injects $PORT at runtime; default to 8080 for local docker run
ENV PORT=8080
EXPOSE 8080

CMD gunicorn --chdir voice-app -w 2 -b 0.0.0.0:${PORT} app:app
