# Hugging Face Spaces, Docker SDK. Spaces route traffic to port 7860.
FROM python:3.11-slim

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=7860
EXPOSE 7860

# No state is written to this filesystem on purpose - it is wiped on restart.
# Everything that must survive lives in Postgres, reached via DATABASE_URL.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}
