# AutoApplier API: FastAPI proxy + restricted pdflatex.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY prompts/ prompts/

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
# Request state is kept in memory (src/backend/jobs.py), so run exactly one worker.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
