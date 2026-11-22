# AutoApplier

AutoApplier is a Chrome extension plus a small FastAPI backend. It tailors your master CV into a one-page LaTeX resume for the job posting you're viewing. It compiles that resume to a PDF for you to review.

0. **Read your CV (once).** When you save your master CV, the backend splits it into sections that keep your own headings (for example "Engineering Projects" and "Professional Experience"), and tags each with a type. You check the types in Settings.
1. **Extract.** Click the toolbar icon on a job posting. The extension reads the page's schema.org `JobPosting` data. If the page has none, it makes a best guess, and you confirm or edit the fields.
2. **Criteria gate.** The backend checks the title, location and pay against your criteria. A rejected job costs no LLM call.
3. **Select.** An OpenAI model compares the posting with your CV. It returns a gap analysis and, if you're qualified, picks the sections, entries and bullets to use by ID, optionally rewording a bullet. It never writes the resume itself.
4. **Check and render.** Code checks the picks against your CV. Each entry stays under the section it came from, so a job can't end up under Projects. Unknown entries are dropped. A reworded bullet that adds a number not in the original goes back to your wording. Every correction is shown to you. Code then typesets the resume from a fixed LaTeX template.
5. **Compile.** The LaTeX is compiled with restricted `pdflatex` in a throwaway directory. If it runs past one page, the backend recompiles with tighter margins, spacing and font size, without changing any wording. If it still doesn't fit, you get the PDF with a warning. The PDF downloads to `Downloads/AutoApplier/`.
6. **You review and apply.** AutoApplier **never** submits applications.

The OpenAI key lives only on the backend. See [DESIGN.md](./DESIGN.md) for the architecture, security decisions and roadmap.

> **Status: private MVP.** Access uses one shared API key. Google sign-in, per-account credits and paid credit packs are designed but not built yet (DESIGN.md §10). Don't expose this backend publicly with your OpenAI key behind it.

## Quick start

You need Docker, Chrome (or Edge or Brave), and an OpenAI API key.

### 1. Start the backend

```bash
cp .env.example .env          # then set OPENAI_API_KEY and pick an AUTOAPPLIER_API_KEY
docker build -t autoapplier .
docker run --rm -p 127.0.0.1:2005:2005 --env-file .env autoapplier
```

Check it with `curl localhost:2005/health`. All four values should be `true`. Binding to `127.0.0.1` keeps the API off your network.

### 2. Load the extension

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and select the `extension/` folder. A welcome page opens.
3. In the extension's **Settings**:
   - Paste your `AUTOAPPLIER_API_KEY` and click **Test connection**.
   - Paste your master CV, or load a `.txt`, `.tex` or `.md` file, and click **Save CV**. Check the sections it finds and fix any wrong type, for example a job marked as a project.
   - Adjust your criteria.

### 3. Use it

Open a job posting, click the AutoApplier icon, and check the extracted fields. Then click **Tailor resume**. Generation usually takes under a minute. You can close the popup in the meantime, and the PDF downloads when it's ready.

| Outcome | What you see |
|---|---|
| Fails your criteria | The reason. No LLM call was made. |
| Model says you're not a match | The gap analysis. |
| PDF compiled | The PDF downloads. You can also download the `.tex`. Any corrections made to the model's choices are listed. |
| LaTeX didn't compile | The compiler log and a `.tex` download, so you can fix it by hand. The model is never asked to regenerate (DESIGN.md §8.4). |

## Configuration

Backend environment variables (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | (required) | Model access. Kept server-side only. |
| `AUTOAPPLIER_API_KEY` | (required) | Shared key the extension sends as `X-AutoApplier-Key`. If unset, every request is refused. |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Model used for qualification and drafting. |
| `AUTOAPPLIER_RATE_LIMIT` | `10` | Generations per minute per client IP. Criteria rejections don't count. |
| `AUTOAPPLIER_JOB_TTL` | `900` | Seconds a finished result stays available for polling and re-download. |
| `AUTOAPPLIER_WORKERS` | `2` | Concurrent generations. |
| `AUTOAPPLIER_COMPILE_TIMEOUT` | `30` | Hard `pdflatex` time limit in seconds. |

Criteria fields (set in the extension; defaults in `src/backend/criteria.json`):

- `target_tokens`: the title must contain at least one, as a whole word or phrase.
- `banned_tokens`: the title must contain none (e.g. `senior`, `ii`).
- `allowed_locations`: the location must contain one (case-insensitive).
- `minimum_hourly_rate` / `minimum_annual_salary`: pay floors. Hourly vs. annual is inferred from the pay text. An empty list or a `0` disables that check.

## API

All `/api` routes require the `X-AutoApplier-Key` header.

- `GET /health` reports `{ok, pdflatex_available, api_key_configured, openai_key_configured}`.
- `POST /api/cv/parse` takes `{resume_text}` and returns `202 {request_id}`. The finished result is `{master_cv, unmatched_bullets}`. `master_cv` is `{contact, sections: [{id, heading, kind, entries: [{id, title, organization, dates, location, tech, bullets: [{id, text}]}]}]}`. `unmatched_bullets` lists parsed lines that don't appear word for word in the text.
- `POST /api/tailor` takes `{job: {title, location, pay, description, url?}, master_cv, criteria?}`, plus an optional `Idempotency-Key` header. IDs in `master_cv` are reassigned from position on the server.
  - A criteria rejection returns `200` with the finished result immediately.
  - Otherwise it returns `202 {request_id, status: "running"}`.
  - Reusing an `Idempotency-Key` returns the same request. Reusing it with different input returns `409`.
- `GET /api/requests/{request_id}` returns `{status: running|done|error, result?, error?}`. `result` has `stage` (`criteria`, `llm`, `compile` or `complete`), `is_qualified`, `reason`, `gap_analysis`, `latex_source`, `resume_pdf_base64`, `compile_log` and `selection_notes`.

Generation is queued and polled because Chrome stops an extension service worker that waits more than 30 seconds on one `fetch()`. Request state is held in memory, so run the API as a single process.

Example using the sample files (parse first, then tailor with the parse):

```bash
H="X-AutoApplier-Key: $AUTOAPPLIER_API_KEY"
python3 -c 'import json; print(json.dumps({"resume_text": open("tests/test_text_files/master_cv.txt").read()}))' > /tmp/cv.json
curl -s -X POST localhost:2005/api/cv/parse -H "$H" -H 'Content-Type: application/json' -d @/tmp/cv.json
curl -s localhost:2005/api/requests/<request_id> -H "$H" > /tmp/parse.json   # poll until "done"

python3 - <<'EOF' > /tmp/req.json
import json
print(json.dumps({
  "job": {"title": "Software Engineering Intern", "location": "Remote", "pay": "",
          "description": open("tests/test_text_files/job_description.txt").read()},
  "master_cv": json.load(open("/tmp/parse.json"))["result"]["master_cv"]}))
EOF
curl -s -X POST localhost:2005/api/tailor -H "X-AutoApplier-Key: $AUTOAPPLIER_API_KEY" \
     -H 'Content-Type: application/json' -d @/tmp/req.json
# then poll:
curl -s localhost:2005/api/requests/<request_id> -H "X-AutoApplier-Key: $AUTOAPPLIER_API_KEY"
```

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
PYTHONPATH=src python -m pytest tests          # compile tests skip without pdflatex
node --test tests/*.test.js                    # JSON-LD parser tests
PYTHONPATH=src uvicorn backend.main:app --reload --port 2005
```

To run the complete suite, including the real `pdflatex` tests, inside the image:

```bash
docker run --rm --user root -v "$PWD":/work:ro autoapplier sh -c \
  "pip install -q -r /work/requirements-dev.txt && cd /work && su app -s /bin/sh -c 'PYTHONPATH=/work/src python -m pytest -p no:cacheprovider tests'"
```

CI (`.github/workflows/main.yml`) runs the backend tests with TeX Live installed and fails if any are skipped. It also syntax-checks and tests the extension, uploads a zipped extension artifact, and smoke-tests the Docker image.

## Layout

```
src/compiler.py            file-based pdflatex wrapper (shared by the API adapter)
src/backend/               FastAPI app: main, schemas, criteria_matcher, llm_engine,
                           resume_model (ID selection checks), render (LaTeX template),
                           latex_guard, page_fit, compile_adapter, jobs, security, config
src/backend/{auth,credits,billing,db}.py   placeholders for planned phases
prompts/                   system prompts: CV parsing and resume selection
extension/                 MV3 extension: manifest, background, popup, options, welcome,
                           jsonld.js + content_script.js (injected on click)
tests/                     pytest suite + node tests
```
