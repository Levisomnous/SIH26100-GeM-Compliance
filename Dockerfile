FROM python:3.12-slim

# tesseract + poppler are needed for the OCR fallback path
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY index.html ./index.html
COPY samples ./samples

# DATABASE_URL is required at runtime and is NOT baked into the image —
# pass it via `docker run -e DATABASE_URL=...` or your host's secrets manager.

# run as an unprivileged user
RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
