FROM python:3.12-slim

# tesseract + poppler are needed for the OCR fallback path
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY samples ./samples

RUN mkdir -p uploads

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
