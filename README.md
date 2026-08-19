# AutoApplier

AutoApplier tailors your master CV into a one-page, job-specific LaTeX resume for a given job
posting. A deterministic criteria gate (title / location / pay) filters out obviously irrelevant
postings before anything reaches the LLM; a compatible posting gets compared against your CV by
an LLM, which either flags a gap analysis or generates a tailored LaTeX resume, which is then
compiled to a PDF for you to review. **It never auto-applies** — you review and submit manually.

The project is mid-migration from a local CLI script to a browser extension + hosted proxy
backend, so that job scraping happens inside your own authenticated browser session and no user
needs a personal API key or local LaTeX install. See [DESIGN.md](./DESIGN.md) for the full
architecture and roadmap. Everything below describes the **current, runnable state**: the local
CLI pipeline in `src/`.

## How it works (current CLI pipeline)

```
Job Listing → Criteria Matcher → LLM Engine → LaTeX Compiler → Human Review
```

1. **`src/criteria_matcher.py`** — checks a job's title, location, and pay against `criteria.json`.
   Jobs that fail (banned title tokens, no target tokens, disallowed location, pay below your
   minimum) are skipped before spending any LLM calls on them.
2. **`src/llm_engine.py`** — sends your master CV and the job description to OpenAI using the
   prompt in `prompts/resume_builder_prompt.txt`. The model returns JSON with `is_qualified`, a
   `gap_analysis` explaining missing requirements, and (if qualified) a complete one-page
   `latex_code` resume tailored to the posting.
3. **`src/compiler.py`** — compiles the generated LaTeX to a PDF via `pdflatex`, writing logs to
   `output/logs/LaTeX_logs/` and the final PDF to `output/pdfs/`.
4. **You review the PDF** and apply manually — nothing here submits an application for you.

`src/main.py` wires these three stages together and currently runs against a mock job and the
sample files in `tests/test_text_files/`.

## Requirements

- Python 3.12+
- A LaTeX distribution providing `pdflatex` (e.g. `texlive-latex-base`,
  `texlive-latex-recommended`, `texlive-latex-extra` — see `Dockerfile` for the exact package set)
- An OpenAI API key

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` (or otherwise export) your API key:

```
OPENAI_API_KEY=sk-...
```

## Running

```bash
python src/main.py
```

This checks the mock job in `main.py` against `criteria.json`, and if it passes, sends
`tests/test_text_files/master_cv.txt` and `tests/test_text_files/job_description.txt` to the LLM,
writes the result to `tailored_resume.tex`, and compiles it to `output/pdfs/tailored_resume.pdf`.

To use your own data, swap in your own master CV / job description text files and edit the mock
job dict in `src/main.py` (title, location, pay), or call `run_llm_generation()` /
`eval_job_criteria()` directly with your own paths.

## Configuration

`criteria.json` controls the deterministic pre-filter:

- `target_tokens` — job title must contain at least one (e.g. `"software"`, `"backend"`)
- `banned_tokens` — job title must not contain any (e.g. seniority levels like `"senior"`, `"lead"`)
- `allowed_locations` — job location must match at least one (case-insensitive substring match)
- `minimum_hourly_rate` / `minimum_annual_salary` — pay floor, hourly vs. annual is inferred from
  the pay text

## Testing

```bash
python tests/test_matcher.py
```

Runs the criteria matcher against the cases in `tests/test_cases.json`.

## Docker

```bash
docker build -t autoapplier .
docker run --env-file .env autoapplier
```

## Roadmap

The next phase moves job scraping into a browser extension and moves the LLM/compilation steps
behind a hosted FastAPI proxy, so the API key stays server-side and users don't need a local
Python/LaTeX setup. Full details in [DESIGN.md](./DESIGN.md).
