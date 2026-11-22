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

> **Status: private, v2 in progress.** Each user has an account with an operator-issued token, and profiles are stored encrypted in Postgres. Automatic job discovery, email approval and assisted applying are being built in phases (DESIGN.md §10). Google sign-in and paid credits come later. Don't expose this backend publicly with your OpenAI key behind it.

## Quick start

You need Docker, Chrome (or Edge or Brave), and an OpenAI API key.

### 1. Start the backend

```bash
cp .env.example .env          # set OPENAI_API_KEY and POSTGRES_PASSWORD
docker compose run --rm api python -m backend.admin gen-key   # paste into AUTOAPPLIER_DATA_KEY
docker compose up -d --build  # starts Postgres, runs migrations, starts the API
docker compose run --rm api python -m backend.admin create-account --label me
```

The last command prints your account token (`aa_…`). It's shown once; `rotate-token <id>` issues a new one. Check the backend with `curl localhost:2005/health`. Every value should be `true`. The API binds to `127.0.0.1`, so it stays off your network.

Keep `AUTOAPPLIER_DATA_KEY` safe. Stored CVs and profiles can't be decrypted without it.

### 2. Load the extension

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and select the `extension/` folder. A welcome page opens.
3. In the extension's **Settings**:
   - Paste your account token and click **Test connection**.
   - Paste your master CV, or load a `.txt`, `.tex` or `.md` file, and click **Save CV**. Check the sections it finds and fix any wrong type, for example a job marked as a project.
   - Add the email address for job notifications, and adjust your criteria. Your CV sections, criteria and email are also saved to your backend profile, where automatic discovery will use them.
   - Under **Job watchlist**, add company boards (Greenhouse, Lever or Ashby URLs) or a SimplifyJobs list. The worker checks them about every 15 minutes. Jobs already open when you add a board are skipped, and new ones appear under **Recently found**.
   - Turn on **Automatically tailor new jobs that pass my criteria**. Each new job is checked against your criteria, which is free. Jobs that pass get a tailored resume, up to your daily limit (default 20; the operator sets it with `python -m backend.admin set-cap <id> <n>`). Tailored jobs appear in the popup's **Inbox**, where you can download the PDF, open the posting, and approve or skip it. Emailing them comes in the next phase.

### 3. Use it

Open a job posting, click the AutoApplier icon, and check the extracted fields. Then click **Tailor resume**. Generation usually takes under a minute. You can close the popup in the meantime, and the PDF downloads when it's ready.

| Outcome | What you see |
|---|---|
| Fails your criteria | The reason. No LLM call was made. |
| Model says you're not a match | The gap analysis. |
| PDF compiled | The PDF downloads. You can also download the `.tex`. Any corrections made to the model's choices are listed. |
| LaTeX didn't compile | The compiler log and a `.tex` download, so you can fix it by hand. The model is never asked to regenerate (DESIGN.md §8.4). |

### Hosting with a managed database (Supabase)

Postgres can be managed (for example Supabase). The API and worker can't run as serverless functions: they run `pdflatex` and a polling loop. Run the image on any always-on host, such as your own machine, a homelab, a small VPS, or a container platform. Point both at the managed database and leave the Compose `db` service out:

```bash
# .env on the host running the image
AUTOAPPLIER_DATABASE_URL=postgresql+psycopg://<user>:<password>@<pooler-host>:6543/postgres?sslmode=require
docker compose run --rm --no-deps api alembic upgrade head
docker compose up -d api worker --no-deps
```

Use Supabase's connection-pooler URL; direct connections may be IPv6-only. Transaction mode (port 6543) works because the backend disables server-side prepared statements (`src/backend/db.py`).

**How discovery load scales:**
- Each board is fetched once per interval no matter how many accounts watch it. The postings are stored once and every account reads them from the database, so request volume grows with **distinct boards**, not users.
- Unchanged boards answer `304 Not Modified`.
- Boards with no changes in 2 days are checked half as often, and after 7 days a quarter as often.
- Requests to one host are capped by `AUTOAPPLIER_HOST_RATE`.
- A `429` pauses every board on that host until its `Retry-After` time.

## Configuration

Backend environment variables (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | (required) | Model access. Kept server-side only. |
| `POSTGRES_PASSWORD` | (required with Compose) | Password for the Compose Postgres service. |
| `AUTOAPPLIER_DATABASE_URL` | set by Compose | SQLAlchemy URL. If unset, every `/api` request is refused. |
| `AUTOAPPLIER_DATA_KEY` | (required) | 32-byte base64url key that encrypts stored profiles (AES-GCM). Generate with `python -m backend.admin gen-key`. |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Model used for qualification and drafting. |
| `AUTOAPPLIER_RATE_LIMIT` | `10` | Generations per minute per account. Criteria rejections don't count. |
| `AUTOAPPLIER_JOB_TTL` | `900` | Seconds a finished result stays available for polling and re-download. |
| `AUTOAPPLIER_WORKERS` | `2` | Concurrent generations. |
| `AUTOAPPLIER_COMPILE_TIMEOUT` | `30` | Hard `pdflatex` time limit in seconds. |
| `AUTOAPPLIER_POLL_INTERVAL` | `900` | Seconds between checks of each watched board (±10% jitter; failures back off up to 6 h). |
| `AUTOAPPLIER_WORKER_TICK` | `30` | Seconds between discovery-worker wake-ups. |
| `AUTOAPPLIER_TASK_THREADS` | `2` | Worker threads that match and tailor new postings. Polling runs on its own thread. |
| `AUTOAPPLIER_HOST_RATE` | `30` | Most requests per minute to one host. Every board on an ATS shares its host, so this caps total load on, say, Greenhouse. |

Criteria fields (set in the extension; defaults in `src/backend/criteria.json`):

- `target_tokens`: the title must contain at least one, as a whole word or phrase.
- `banned_tokens`: the title must contain none (e.g. `senior`, `ii`).
- `allowed_locations`: the location must contain one (case-insensitive).
- `minimum_hourly_rate` / `minimum_annual_salary`: pay floors. Hourly vs. annual is inferred from the pay text. An empty list or a `0` disables that check.

## API

All `/api` routes require an account token in the `X-AutoApplier-Key` header. Requests, results and idempotency keys are private to the account.

- `GET /health` reports `{ok, pdflatex_available, database_ok, data_key_configured, openai_key_configured}`.
- `GET /api/me` returns `{id, label, daily_llm_cap, automation_enabled}`.
- `GET /api/profile` returns `{notify_email, master_cv, criteria, application_profile, updated_at}`. `PATCH /api/profile` updates the fields present in the body; `null` clears one. The email, CV and application profile are encrypted at rest.
- `GET /api/sources` lists the account's watchlist with status (`last_polled_at`, `last_error`, `open_postings`, `new_postings_7d`). `POST /api/sources {url}` adds a Greenhouse, Lever or Ashby board, or a SimplifyJobs list, detected from any board, job, embed or API URL. It returns `422` for unsupported URLs or unknown boards. `DELETE /api/sources/{id}` removes one.
- `GET /api/postings?limit=` lists new postings from the watchlist, one per job even when two sources report it.
- `PATCH /api/account {automation_enabled}` turns automatic tailoring on or off. The daily cap is operator-only.
- `GET /api/matches?state=&limit=` lists the account's matches with posting details. By default it lists everything except criteria rejections; `state` takes a comma-separated list. `GET /api/matches/{id}/resume` returns `{latex_source, resume_pdf_base64}`. `POST /api/matches/{id}/decision {decision: approve|skip}` records a decision, and `POST /api/matches/{id}/retry` re-queues a failed one. An invalid state change returns `409`.
- `POST /api/cv/parse` takes `{resume_text}` and returns `202 {request_id}`. The finished result is `{master_cv, unmatched_bullets}`. `master_cv` is `{contact, sections: [{id, heading, kind, entries: [{id, title, organization, dates, location, tech, bullets: [{id, text}]}]}]}`. `unmatched_bullets` lists parsed lines that don't appear word for word in the text.
- `POST /api/tailor` takes `{job: {title, location, pay, description, url?}, master_cv, criteria?}`, plus an optional `Idempotency-Key` header. IDs in `master_cv` are reassigned from position on the server.
  - A criteria rejection returns `200` with the finished result immediately.
  - Otherwise it returns `202 {request_id, status: "running"}`.
  - Reusing an `Idempotency-Key` returns the same request. Reusing it with different input returns `409`.
- `GET /api/requests/{request_id}` returns `{status: running|done|error, result?, error?}`. `result` has `stage` (`criteria`, `llm`, `compile` or `complete`), `is_qualified`, `reason`, `gap_analysis`, `latex_source`, `resume_pdf_base64`, `compile_log` and `selection_notes`.

Generation is queued and polled because Chrome stops an extension service worker that waits more than 30 seconds on one `fetch()`. Request state is held in memory, so run the API as a single process.

Example using the sample files (parse first, then tailor with the parse):

```bash
H="X-AutoApplier-Key: $AUTOAPPLIER_TOKEN"   # your aa_… account token
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
curl -s -X POST localhost:2005/api/tailor -H "$H" -H 'Content-Type: application/json' -d @/tmp/req.json
curl -s localhost:2005/api/requests/<request_id> -H "$H"                      # poll
```

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
PYTHONPATH=src python -m pytest tests          # in-memory SQLite; compile tests skip without pdflatex
# against Postgres: AUTOAPPLIER_TEST_DATABASE_URL=postgresql+psycopg://user:pw@host/db python -m pytest tests
node --test tests/*.test.js                    # JSON-LD parser tests
alembic upgrade head                           # needs AUTOAPPLIER_DATABASE_URL
PYTHONPATH=src uvicorn backend.main:app --reload --port 2005
```

To run the complete suite, including the real `pdflatex` tests, inside the image:

```bash
docker run --rm --user root -v "$PWD":/work:ro autoapplier sh -c \
  "pip install -q -r /work/requirements-dev.txt && cd /work && su app -s /bin/sh -c 'PYTHONPATH=/work/src python -m pytest -p no:cacheprovider tests'"
```

CI (`.github/workflows/main.yml`) runs the backend tests against Postgres with TeX Live installed, and fails if any are skipped. It also syntax-checks and tests the extension, uploads a zipped extension artifact, and smoke-tests the Compose stack: migrations, account creation and token auth.

Schema changes need an Alembic revision (`alembic revision --autogenerate -m "..."` with `AUTOAPPLIER_DATABASE_URL` set). `tests/test_db.py` fails if the models and migrations differ.

## Layout

```
src/compiler.py            file-based pdflatex wrapper (shared by the API adapter)
src/backend/               FastAPI app: main, pipeline (tailor/parse), schemas, criteria_matcher,
                           llm_engine, resume_model (ID selection checks), render (LaTeX template),
                           latex_guard, page_fit, compile_adapter, jobs, security, config
src/backend/               accounts (tokens, profile), models + db (SQLAlchemy), crypto (AES-GCM),
                           admin (operator CLI), migrations/ (Alembic)
src/backend/               discovery (watchlist, polling), worker (poll + task threads),
                           matching (match states, criteria → tailoring, cleanup), tasks (queue),
                           sources/ (greenhouse, lever, ashby, simplify adapters, JSON-LD, enrich)
src/backend/{auth,credits,billing}.py   placeholders for planned phases
docker-compose.yml         API + discovery worker + Postgres
prompts/                   system prompts: CV parsing and resume selection
extension/                 MV3 extension: manifest, background, popup, options, welcome,
                           jsonld.js + content_script.js (injected on click)
tests/                     pytest suite + node tests
```
