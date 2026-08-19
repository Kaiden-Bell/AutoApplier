# AutoApplier — Design Document

**Status:** Migrating from a local CLI pipeline to a browser extension + hosted proxy service.

## 1. Why This Exists

AutoApplier tailors a candidate's master CV into a one-page, job-specific LaTeX resume by
comparing it against a scraped job posting with an LLM, then compiles that resume to a PDF for
the candidate to review before applying. A deterministic pre-filter (title/location/pay) keeps
obviously-irrelevant postings from ever reaching the LLM.

## 2. The Pivot

The original design (see §7, History) drove job discovery with a `Playwright` bot polling
LinkedIn/Handshake and an IMAP email scraper. Both approaches fight the site's bot detection and
don't scale past "one person's cron job."

The project is moving to a **browser extension + proxy backend** model instead:

- **Extension (frontend):** runs inside the user's real, already-authenticated browser session,
  so job pages render exactly as they would for a human — no bot-detection fight. It scrapes the
  visible job posting text and holds the user's master CV in local extension storage.
- **Proxy backend (FastAPI):** the only thing that holds the OpenAI API key. It receives scraped
  text + CV text, runs the criteria gate, calls the LLM, compiles the LaTeX result to a PDF, and
  returns it. No user ever needs their own API key, a local Python environment, or a local LaTeX
  install.

This also reframes the project from "a script one person runs locally" into a hostable service
other users could point their own extension installs at.

## 3. Architecture Overview

```
[ User's Browser ]
  └── Extension: content script scrapes job posting DOM
      └── Extension: reads master CV from chrome.storage.local
            │
            ▼  HTTPS POST /api/tailor  { job_text, resume_text }
[ FastAPI Proxy Backend ]
  1. Criteria gate (deterministic title/location/pay check)
  2. LLM call (OpenAI, key held server-side only) → { is_qualified, gap_analysis, latex_code }
  3. If qualified: compile latex_code → PDF (pdflatex, per-request temp dir)
            │
            ▼  { is_qualified, gap_analysis, resume_pdf_base64 }
[ Extension ]
  └── Shows gap analysis (if rejected) or offers the PDF for download/preview
  └── Human reviews and applies manually — pipeline never auto-submits
```

## 4. Target Repository Structure

```text
AutoApplier/
├── DESIGN.md
├── README.md
├── .github/
│   └── workflows/
│       └── main.yml            # test backend, package + release extension
├── backend/                     # FastAPI proxy (deployed, e.g. Render)
│   ├── main.py                  # /api/tailor route
│   ├── criteria_matcher.py      # ported from src/, unchanged logic
│   ├── llm_engine.py            # ported from src/, called in-process instead of via CLI
│   ├── compiler.py              # ported from src/, per-request temp dirs
│   ├── prompts/
│   │   └── resume_builder_prompt.txt
│   ├── criteria.json
│   ├── requirements.txt
│   └── test_main.py
└── extension/                    # Chromium extension (Chrome/Edge/Brave)
    ├── manifest.json
    ├── content_script.js         # scrapes job posting DOM
    ├── popup.html                # CV upload + run/review UI
    └── popup.js                  # storage, fetch to backend, PDF display
```

The current `src/`, `prompts/`, `criteria.json`, and `tests/` at the repo root map directly into
`backend/` (see the reuse table below) rather than being rewritten from scratch.

## 5. Pipeline Walkthrough

### 5.1 Fetching Postings
Replaces the email scraper and Playwright poller. The extension's content script runs on
supported job-board pages (LinkedIn, Handshake, Indeed, …) and extracts the posting's title,
location, pay, and full description text from the DOM when the user clicks "Tailor Resume."

### 5.2 Criteria Matching
Unchanged logic, relocated. `src/criteria_matcher.py` already does exactly what's needed —
regex-parses location/pay, checks title tokens against `criteria.json`'s `target_tokens` /
`banned_tokens` / `allowed_locations` / minimum pay thresholds — and needs no rewrite, only a new
home. It runs **in the backend**, before the LLM call, so the pay/location thresholds stay
server-side config rather than being duplicated in extension JS (see Open Decisions, §8).

### 5.3 LLM Qualification + Resume Drafting
`src/llm_engine.py` and `prompts/resume_builder_prompt.txt` already implement the richer contract
the pipeline needs — an OpenAI call returning JSON with `is_qualified`, `gap_analysis`, and (when
qualified) a complete one-page `latex_code` string with double-escaped backslashes. The backend
reuses this prompt and JSON contract as-is; `updated_design.md`'s original plain-text example is
superseded by it. If `is_qualified` is false, the backend returns the gap analysis and stops.

### 5.4 LaTeX Compilation
`src/compiler.py`'s `pdflatex` subprocess wrapper is reused, but the backend must generate PDFs
per-request rather than in one shared `output/` directory — see §8. The backend returns the
compiled PDF (base64-encoded) alongside the raw LaTeX source, so the extension can offer both a
preview and a "view LaTeX" option.

### 5.5 Human Review
Unchanged principle from the original design: **the pipeline never auto-applies.** The extension
surfaces the generated PDF for the user to review and manually submit through the job board's own
application flow.

## 6. Code Reuse Map

| Current file | New location | Change needed |
|---|---|---|
| `src/criteria_matcher.py` | `backend/criteria_matcher.py` | None — logic ports as-is |
| `src/llm_engine.py` | `backend/llm_engine.py` | Called as a function from the FastAPI route instead of via CLI `main.py`; drop file I/O for prompt/CV/JD in favor of in-memory strings from the request |
| `src/compiler.py` | `backend/compiler.py` | Switch shared `output/logs`, `output/pdfs` dirs to a per-request temp directory (`tempfile.mkdtemp()`), return PDF bytes instead of writing to a fixed path |
| `prompts/resume_builder_prompt.txt` | `backend/prompts/resume_builder_prompt.txt` | None |
| `criteria.json` | `backend/criteria.json` | None |
| `tests/test_matcher.py`, `tests/test_cases.json` | `backend/test_main.py` (+ ported criteria tests) | Adapt import paths |
| `src/main.py` | *(retired)* | Its job (wiring the three stages together) becomes the `/api/tailor` route handler |
| `cv.tex`, `tailored_resume.tex` | *(personal files, not shipped)* | These are one person's actual resume content committed at the repo root — worth moving out of version control (or into a gitignored example dir) now that the project is heading toward being usable by more than one person |

## 7. Data Storage & Privacy

- **Master CV:** stored only in the user's `chrome.storage.local`, sent to the backend per-request,
  never persisted server-side. No user database in the MVP.
- **Job postings / results:** not persisted server-side either — the backend is stateless per
  request, matching `updated_design.md`'s original intent.
- **Secrets:** the OpenAI API key lives only in the backend's environment (Render/host config),
  never shipped in extension code.

## 8. Open Decisions / Risks

These are gaps between `updated_design.md` and the current codebase that need a deliberate call,
not just an assumption — flagging them here so they're visible rather than silently baked in:

1. **Criteria gate placement.** This doc puts it in the backend (server-side config, single
   source of truth, reuses `criteria_matcher.py` untouched). The alternative — running it in the
   extension — would save a round trip for obviously-disqualified jobs but means duplicating
   `criteria.json` logic in JS and shipping the thresholds to the client. Revisit if request volume
   becomes a cost concern.
2. **Compiler concurrency.** `compiler.py` currently writes to a single shared
   `output/logs/LaTeX_logs` / `output/pdfs` directory keyed only by filename — fine for one local
   user, not safe for concurrent requests from multiple SaaS users. Needs per-request temp dirs
   (§5.4/§6) before this goes multi-tenant.
3. **CORS.** The `updated_design.md` backend example sets `allow_origins=["*"]`. Needs to be
   locked down to the packaged extension's origin before any public deployment.
4. **LLM output trust.** The prompt already requires the model to avoid inventing facts and to
   double-escape LaTeX, but there's still no validation step before compilation — a malformed or
   hallucinated `latex_code` value will surface as a `pdflatex` error today. Worth a sanity check
   (e.g. JSON schema validation, escaping check) before compiling untrusted model output.
5. **Job-board DOM scraping is fragile and may violate site Terms of Service.** LinkedIn, Indeed,
   and Handshake can change markup or rate-limit/ban accounts that scrape via extension content
   scripts, same underlying risk as the Playwright approach, just moved to the user's own account
   instead of a bot's. Worth deciding per-site whether to scrape or rely on manual paste of the
   job description.

## 9. CI/CD

A single GitHub Actions workflow on push to `main`:
1. Sets up Python, installs `backend/requirements.txt`, runs `pytest` against the backend.
2. Zips `extension/` and attaches it to a GitHub Release for manual "Load unpacked" installs
   (or a Chrome Web Store submission once the extension is stable).
3. On success, hits a deploy webhook (e.g. Render) to redeploy the backend.

## 10. Roadmap

- **Phase 1 (current):** Local CLI pipeline (`src/`) proves out the criteria gate → LLM →
  compile flow against a mock job and static test files.
- **Phase 2:** Port `src/` into `backend/`, stand up the FastAPI route, deploy it, resolve the
  concurrency/CORS items in §8.
- **Phase 3:** Build the extension (content script + popup UI), wire it to the deployed backend.
- **Phase 4:** Multi-user hardening — auth, rate limiting, and a decision on whether any job/result
  history gets persisted at all.

## 11. History

The original single-machine design (email scraper + Playwright poller, straight-through local
pipeline with no proxy) is preserved below for context on how the current `src/` modules came to
exist; it's superseded by the architecture above.

<details>
<summary>Original design (pre-pivot)</summary>

### Pipeline Architecture

| Job Listing | → | Parser | → | LLM Engine | → | LaTeX Compiler | → | Human Review |

### Tech Stack

- Orchestrator: Python
- APIs: Playwright (checking website for LinkedIn/Handshake)
- Engine Matching: OpenAI API
- DB/Storage: SQLite3
- LaTeX Comp: Python-LaTeX wrapper

### 1. Fetching Postings

**Email scraper:** a Python script using `imaplib` to find emails from Handshake and LinkedIn,
extracting the link for later use.

**Browser automation:** using Playwright to periodically search for target keywords ("Software
Engineering", "Fullstack", …) and pull raw HTML of job posts.

### 2. Criteria Matching

Runs a deterministic check vs. a text document — parses location, pay, and job title using
regex and compares against `criteria.json`. If the job requires specific relocation or falls
below certain thresholds, it doesn't get passed to the LLM.

### 3. Comparison Check

If the criteria check passes, the master CV and job description are sent to an LLM with a prompt
like: *"Analyze this [Master CV] against this [Job Description]. Output valid JSON with two
fields: is_qualified (boolean) and gap_analysis (string analyzing missing requirements)."* If
`is_qualified` is false, log to the DB, mark "Skipped," and halt.

### 4. Crafting a LaTeX Resume

If the job matches, generate a tailored resume: inject a base template, then enforce constraints
with a prompt like *"You must fit this content into exactly one page. Use concise bullet points
focusing heavily on [X], [Y], and [Z]. Output raw LaTeX code inside a JSON string block."* A
strict visual framework keeps the LLM from flooding the page.

### 5. Compilation and Human Review

Never auto-apply — always allow a human review pass over the generated resume/cover letter.
Compile locally via `subprocess.run(["pdflatex", "-output-directory=output/", "tailored_resume.txt"])`,
open the generated PDF automatically, and review it for accuracy before applying.

</details>
