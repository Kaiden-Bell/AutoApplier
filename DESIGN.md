# AutoApplier — Design Document

**Status (2026-11-17):** Private MVP complete. The FastAPI backend (criteria gate → LLM →
LaTeX guard → restricted compile) and the MV3 extension work end to end behind a shared dev key.
The tests run against real `pdflatex`. Google sign-in, credits, and billing are still planned.
Public launch requires the account and payment gates in §10.

**Public service model:** Google sign-in before generation; **3 free successful resume creations
per Google account, granted once**, then prepaid credits. The backend owns all balances and
payment decisions. Settings and the master CV remain in the user's extension storage.

## 1. Why This Exists

AutoApplier tailors a candidate's master CV into a one-page, job-specific LaTeX resume by
comparing it against a scraped job posting with an LLM, then compiles that resume to a PDF for
the candidate to review before applying. A deterministic pre-filter (title/location/pay) keeps
obviously-irrelevant postings from ever reaching the LLM.

## 2. The Pivot

The original design (see §11, History) drove job discovery with a `Playwright` bot polling
LinkedIn/Handshake and an IMAP email scraper. Both approaches fight the site's bot detection and
don't scale past "one person's cron job."

The project is moving to a **browser extension + proxy backend** model instead:

- **Extension (frontend):** runs inside the user's real, already-authenticated browser session,
  so job pages render exactly as they would for a human — no bot-detection fight. It scrapes the
  visible job posting text and holds the user's master CV in local extension storage.
- **Proxy backend (FastAPI):** the only thing that holds the OpenAI API key. It receives scraped
  text + CV text, runs the criteria gate, calls the LLM, compiles the LaTeX result to a PDF, and
  returns it. For public use it also verifies user sessions, reserves and settles credits in a
  database, and confirms purchases through a payment provider. No user needs their own API key,
  a local Python environment, or a local LaTeX install.

This also reframes the project from "a script one person runs locally" into a hostable service
other users could point their own extension installs at.

### 2.1 v2: automatic discovery, email approval, assisted apply

The extension alone only tailors jobs the user finds. v2 adds always-on automation without
giving up the reason the extension exists (the expensive, bot-sensitive browsing happens in the
user's own browser):

```
            ┌──────────────── server (one Docker image, two commands) ────────────────┐
 Greenhouse │  worker: poll due sources → upsert postings → match per account          │
 Lever  ───►│          → criteria gate → LLM selection → render/compile → email         │
 Ashby      │  api:    profile sync, matches inbox, /decide confirm page, apply status  │──► email (Y/N)
 Simplify   │  Postgres: accounts, profiles (encrypted), sources, postings, matches,    │
            │            tasks (queue), decision tokens, application runs               │
            └──────────────────────────────────────────────────────────────────────────┘
                     ▲ approved matches (poll)            │ status + screenshots
                     │                                    ▼
            extension (user's browser): open apply page → fill → screenshot → user clicks Submit
```

- **Sources:**
  - Public ATS job boards that publish postings the moment the company does: Greenhouse
    (`boards-api.greenhouse.io/v1/boards/{slug}/jobs`), Lever (`api.lever.co/v0/postings/{slug}`)
    and Ashby (`api.ashbyhq.com/posting-api/job-board/{slug}`), for companies on the user's
    watchlist.
  - SimplifyJobs internship list repos.
  - Social media and paid search APIs were rejected: no usable official APIs, terms-of-service and
    bot-detection risk, cost, and lag behind the source boards.
- **Scaling:**
  - Each source is polled once for all users, so discovery cost grows with companies, not users.
  - The free criteria gate runs per account. The LLM runs only on matches, under a daily cap per
    account.
  - The first poll of a new source is a baseline: existing postings are recorded, not matched.
  - The server never runs browsers. Form filling happens in the extension.
- **Human gate:**
  - Each match is emailed with the tailored PDF. The Approve/Skip links open a confirmation page,
    and the decision is a POST, because email security scanners follow GET links.
  - On approval the extension fills the form from the application profile, attaches the resume,
    and takes screenshots. **The user clicks Submit.** The extension never submits applications.
- **Accounts:** real accounts and per-user rows from the start. v1 tokens are issued by the operator
  (`python -m backend.admin create-account`). Google sign-in (§8.6) replaces token issuance later
  without changing the tables.

Delivery is phased (§10, "v2 automation track"): A persistence and accounts, B discovery, C
automatic match and tailor, D email approval, E assisted apply.

## 3. Architecture Overview

This is the public-launch target. The private MVP implements everything except sign-in,
sessions, credits, and purchases. It uses a shared key, and request state is in memory (§7).
The diagram is not a claim that those parts are implemented.

```
[ User's Browser ]
  └── Activate extension → introductory page explaining the service
      └── Sign in with Google → backend session and account balance
      └── Settings: save criteria to chrome.storage.sync
      └── Upload CV/resume: save its content to chrome.storage.local
      └── POST /api/cv/parse once per CV text → sections the user reviews (§5.3)
  └── Content script: JSON-LD JobPosting → else pre-filled form → user confirms
      └── Reads master CV from chrome.storage.local
      └── Reads criteria from chrome.storage.sync
            │
            ▼  chrome.runtime.sendMessage
  └── Background service worker (adds user session and Idempotency-Key)
            │
            ▼  HTTPS POST /api/tailor  { job, master_cv, criteria }
[ FastAPI Proxy Backend ]
  0. Verify session; enforce durable account/IP attempt and concurrency limits
  1. Criteria gate (deterministic title/location/pay check) — stops here if it fails
  2. Database: deduplicate request and atomically reserve 1 available credit
     └── No credit → payment required; do not call the LLM (use sqlite)
  3. LLM call → { is_qualified, gap_analysis, sections (IDs from master_cv) }
  4. If qualified: check/repair the selection, render LaTeX from the template, then
     compile → PDF (restricted pdflatex, temp dir)
  5. Database: consume reservation on successful PDF; release on rejection/failure; require timeout
            │
            ▼  { stage, is_qualified, reason, gap_analysis,
                 latex_source, resume_pdf_base64, compile_log, selection_notes,
                 request_id, credit_outcome }
[ Extension ]
  └── Offers the completed PDF for download, shows a rejection/gap analysis, or —
      if compilation failed — the LaTeX source and compiler log to fix by hand
  └── User downloads the PDF and opens it in their browser to review
  └── Human applies manually — pipeline never auto-submits

[ Purchase flow ]
  Extension → authenticated checkout request → hosted payment page
  Payment provider → signed webhook → backend verifies payment and grants credits once
  Extension → refresh account balance → tailoring can resume
  Consider Subscription → Per month quota of credits per plan.
```

## 4. Repository Structure

Files marked *planned* are placeholders for Phase 2b/4.

```text
AutoApplier/
├── DESIGN.md, README.md
├── Dockerfile                    # python:3.12-slim + TeX Live; uvicorn, single worker
├── docker-compose.yml            # api + Postgres; runs migrations on start
├── alembic.ini                   # migrations live in src/backend/migrations
├── requirements.txt, requirements-dev.txt, .env.example
├── .github/workflows/main.yml    # backend tests w/ pdflatex, extension checks + zip, image smoke test
├── prompts/
│   ├── cv_parse_prompt.txt       # CV text → typed sections (transcription only)
│   └── resume_builder_prompt.txt # qualification + selection of CV IDs
├── src/
│   ├── compiler.py               # filepath-based pdflatex wrapper, shared with the API
│   └── backend/                  # FastAPI proxy (run: PYTHONPATH=src uvicorn backend.main:app)
│       ├── main.py               # /health, POST /api/cv/parse, POST /api/tailor, GET /api/requests/{id}
│       ├── schemas.py            # pydantic request/response, MasterCV, structured outputs
│       ├── config.py             # env settings, repo paths
│       ├── criteria_matcher.py   # criteria gate; takes criteria as a dict
│       ├── criteria.json         # default criteria (mirrors extension/criteria.json)
│       ├── llm_engine.py         # strict structured-output OpenAI calls (parse, select)
│       ├── resume_model.py       # IDs, parse fidelity check, selection check/repair
│       ├── render.py             # LaTeX template + escaping
│       ├── latex_guard.py        # backstop check on rendered LaTeX before pdflatex
│       ├── page_fit.py           # recompiles with tighter layout until one page
│       ├── compile_adapter.py    # temp-dir adapter around src/compiler.py
│       ├── jobs.py               # in-memory request store + worker pool + idempotency
│       ├── security.py           # account-token auth + in-process per-account limiter
│       ├── accounts.py           # tokens, encrypted profile read/update
│       ├── models.py, db.py      # SQLAlchemy models + engine/sessions
│       ├── migrations/           # Alembic (alembic.ini at repo root)
│       ├── crypto.py             # AES-GCM encryption at rest
│       ├── admin.py              # operator CLI: gen-key, create-account, rotate-token
│       ├── pipeline.py           # tailor/parse, shared by API and worker
│       ├── auth.py               # planned: Google sign-in, sessions, logout
│       ├── credits.py            # planned: free grants, reservations, settlement, ledger
│       └── billing.py            # planned: checkout, signed webhooks, reconciliation
├── tests/                        # pytest (API, matcher, guard, LLM errors, real compile) + node
└── extension/                    # Chromium MV3 extension (Chrome/Edge/Brave)
    ├── manifest.json             # activeTab + scripting, downloads, storage; localhost backend
    ├── jsonld.js                 # schema.org JobPosting extractor (injected on click)
    ├── content_script.js         # runs jsonld.js, falls back to page heuristics
    ├── background.js             # service worker — the only code that calls the backend
    ├── popup.html / popup.js     # posting confirmation, run status, PDF/.tex download
    ├── options.html / options.js # backend URL/key + CV + reviewed sections → storage.local; criteria → storage.sync
    ├── welcome.html              # opened on install: setup instructions
    ├── ui.css
    └── criteria.json             # bundled default criteria
```

## 5. Pipeline Walkthrough

### 5.0 User Setup and End-to-End Flow

1. The user activates the extension and opens an introductory page explaining how to use the
   service, the 3 free creations, paid credits, and where to find settings. Google sign-in is
   required before the first generation. The backend grants the account's free credits once. Consider using lower level models for free creations. 
2. In settings, the user edits the criteria fields (target/banned title tokens, allowed
   locations, and minimum pay). The extension saves these small preferences to
   `chrome.storage.sync` and loads them on later visits, so they do not need to be re-entered.
3. The user uploads their master CV/resume. Its content is saved in `chrome.storage.local`,
   which provides more room than sync storage, and is reused for later tailoring requests.
4. The user visits a job posting, invokes tailoring, and confirms the extracted posting fields.
   The extension sends the job, the saved CV sections (§5.3), and saved criteria with its session and a unique
   request key. It displays the server-reported remaining free/paid credits.
5. The backend authenticates the user, checks attempt limits, applies the criteria gate, and
   reserves a credit before calling the LLM. A zero available balance prompts purchase without
   a model call. For a qualified result, it checks the model's ID selection against the CV, renders
   the LaTeX from a fixed template, and compiles it through the API adapter.
6. A successfully produced PDF consumes one credit; rejection or generation/compilation failure
   releases the reservation (§8.7). The extension offers the resulting PDF for download. The user
   downloads it, opens it in their own browser's PDF viewer, and reviews it before applying
   manually. This flow does not require a PDF viewer embedded inside the extension.
7. Once all 3 free credits are consumed, the user purchases a credit pack through hosted
   checkout. Payment confirmation on the backend replenishes the balance; the user can then
   create additional resumes. Sign-out, reinstall, or clearing browser storage does not reset
   the free grant. A different Google account is a separate account, not proof of a new person.

### 5.1 Fetching Postings
Replaces the email scraper and Playwright poller. When the user clicks "Tailor Resume," the
content script extracts the posting in two tiers (§8.5):

1. **schema.org JSON-LD.** Most boards embed a `JobPosting` object in a
   `<script type="application/ld+json">` tag so Google Jobs can index them. Reading it takes
   plain DOM JavaScript with no dependencies, gives structured `title` / `jobLocation` /
   `baseSalary` / `description`, and is immune to markup redesigns because it is published
   deliberately for machine consumption.
2. **Editable form fallback.** When a page has no JSON-LD — auth-gated SPAs such as Handshake
   are the common case — the extension pre-fills a small form from page heuristics (`<h1>`, the
   current text selection, visible body text) and the user confirms or corrects the fields before
   anything is sent.

There are no per-site CSS selectors, no crawling, and no background activity: one page at a time,
always user-initiated.

### 5.2 Criteria Matching
`src/backend/criteria_matcher.py` provides the criteria gate — checking title tokens against
`target_tokens` / `banned_tokens` / `allowed_locations` and parsing pay against the minimum
thresholds. The **evaluation** runs in the backend before credit reservation and the LLM call.
It replaced the original CLI matcher. The **configuration** belongs to the user: the
extension's options GUI writes it to `chrome.storage.sync` and sends it in the request body, and the backend
falls back to the bundled `criteria.json` when a request omits it (§8.1).

The existing backend port takes a criteria dict rather than resolving a path, and introduces
word-boundary matching and structured pay. These are explicit behavior changes beyond the API
interface adaptation. Implementation works as following:
- User selects a job, they don't read through the requirements, or perhaps even the job title.
- They hit "Tailor Resume" to prompt the LLM. Before the LLM is prompted, it passes through the users criteria, touching on their
set title(e.g. `i`)/pay(e.g. `$30 / hr`)/location(e.g. `Las Vegas, Nevada`).
- The job (e.g. `Based in Seattle, WA`) gets rejected without spending any LLM credit, allowing the user to skip past the job, and move forward with the next one they are qualified for.

Visit (§10 for more logic)

### 5.3 CV Parsing, Qualification, and Resume Selection
The model never writes the resume. Before this change it returned a whole LaTeX document, so
nothing checked which section an entry was printed under. In practice, a freelance job ended up
under Projects, which matters for postings that weigh work experience separately. A fixed
pydantic layout (for example a required `projects` list) can't fix this, because CVs are
organized differently: some have no projects, some have detailed experience, coursework, or
publications. So the pipeline enforces **section membership** instead:

1. **Parse once** (`POST /api/cv/parse`, `prompts/cv_parse_prompt.txt`). The model transcribes the
   CV into `ParsedCV`: contact details plus sections that keep the user's own headings. Each
   section has a `kind` (`summary | experience | projects | education | skills | coursework |
   certifications | publications | leadership | languages | other`) and holds entries with
   bullets. Code assigns position-derived IDs (`s3`, `s3.e2`, `s3.e2.b1`,
   `resume_model.assign_ids`) and flags bullets that don't appear word for word in the CV text
   (`unmatched_bullets`). The extension stores the result, shows it in Settings so the user can
   correct a section's heading or kind, and re-parses only when the CV text changes.
2. **Select** (`POST /api/tailor`, `prompts/resume_builder_prompt.txt`). The request carries the
   stored `master_cv`, and the server reassigns IDs on receipt, so client IDs are never trusted.
   Through strict structured output (`TailoredResume`), the model returns `is_qualified`,
   `gap_analysis`, and the section, entry and bullet IDs to print, in order. Each bullet can
   carry an optional `rewrite`. Contact details are not sent in this prompt; code copies them
   onto the resume.
3. **Check and repair** (`resume_model.check_selection`). This step is deterministic and uses CV
   content only:
   - an entry listed under the wrong section is moved back to the section it came from;
   - unknown or duplicate IDs, and bullets that belong to another entry, are dropped;
   - a rewrite that adds a number the source bullet (or its entry's title or tech) doesn't
     contain, or that grows to more than about twice the source length, falls back to the CV's
     wording;
   - Education is added back if the model left it out.

   Each repair becomes a `selection_notes` entry that the popup shows. A selection with nothing
   but Education fails the request.
4. **Render** (`render.render_resume`). A fixed template lays out each section by kind: entries
   with bullets, or compact `Label: a, b, c` lines for skills, coursework and languages. All text
   passes through `latex_escape`. The preamble maps ligatures to Unicode so the PDF's text layer
   reads "profile" rather than "prole" for applicant tracking systems.

If `is_qualified` is false, the backend returns the gap analysis and stops. The credit-enabled
route releases the reservation without consuming a credit.

Known limit: the rewrite check catches invented numbers but not an invented tool or skill named in
a rewrite. The prompt forbids that, and the user reviews every PDF.

### 5.4 LaTeX Compilation
`src/compiler.py`'s `pdflatex` subprocess wrapper is reused, but the backend must generate PDFs
per-request rather than in one shared `output/` directory (§8.2), and must treat the model's
output as untrusted input rather than as a file it wrote itself (§8.4). Concretely: validate the
LaTeX before invoking anything, compile inside a `tempfile.TemporaryDirectory()` with shell escape
disabled and file access restricted, and impose a hard subprocess timeout so a runaway document cannot pin a
worker indefinitely.

The API adapter writes the parsed LaTeX source to a temporary file and calls
`src/compiler.py` with that filepath and temporary log/PDF directories. The shared compiler
produces the PDF; the adapter reads its bytes for the response and cleans up the temporary files.

The backend returns the compiled PDF (base64-encoded) alongside the raw LaTeX source. The
extension turns the PDF response into a downloadable file for browser review. **When compilation
fails, it returns the LaTeX source and the compiler log for the user to correct by hand** — it does
not ask the model to try again (§8.4).

### 5.5 Human Review
**The pipeline never auto-applies.** The extension
offers a PDF download. The user opens the downloaded file in their own browser, reviews its
content, and manually submits it through the job board's own application flow.

## 6. Data Storage & Privacy

- **Master CV:** uploaded by the user and saved in `chrome.storage.local`. **Since v2 the parsed
  CV (`master_cv`) is also stored server-side**, because the discovery worker has to tailor
  while the user's browser is closed. It is encrypted at rest with AES-256-GCM
  (`src/backend/crypto.py`). The key, `AUTOAPPLIER_DATA_KEY`, lives only in the server
  environment, and each ciphertext is bound to its account and field. The notification email
  and application profile are stored the same way. The raw CV text is not stored server-side.
  Before v2, the CV was sent per request and never persisted. Local storage provides more capacity than sync storage and
  keeps the CV on that browser profile rather than syncing it across machines. Supported upload
  formats and text extraction are still to be defined; the parse route accepts `resume_text`,
  so a stored upload must supply text. The parsed sections (`masterCv`) are stored next to
  the text and are what each tailoring request sends.
- **Criteria:** stored in `chrome.storage.sync`, so a user's thresholds follow them across
  machines when Chrome Sync is enabled (§8.1). Since v2 the extension also saves them to the
  backend profile, as plain JSON (they are thresholds, not personal data), so the worker can
  match postings.
- **Job postings / document results:** processed in memory and temporary compilation files;
  not retained as permanent server-side history. A downloaded PDF remains on the user's device.
  Delivery recovery after a lost response is an explicit pre-launch decision (§8.7).
- **Account and billing metadata:** persisted server-side. Store the verified Google account
  identity, sessions, once-only free grant, balances, request states, credit ledger, and payment
  references. This makes the public backend stateful for usage and billing, while CV content
  stays outside that database. Logs must not contain CV/job bodies, generated LaTeX, or tokens.
- **Retention/deletion:** define account deletion, billing-record retention, and handling of a
  previously used free grant before launch. An extension reinstall alone never creates a new
  entitlement. Document any temporary result retention introduced for delivery recovery.
- **Secrets:** the OpenAI API key lives only in the backend's environment (Render/host config),
  never shipped in extension code. OAuth client secrets, payment secrets, and webhook signing
  secrets also stay server-side. Public API access uses per-user sessions; the current shared
  key is a private-development mechanism only (§8.3).

The Chrome API names are `chrome.storage.sync` and `chrome.storage.local`; see the
[official storage reference](https://developer.chrome.com/docs/extensions/reference/api/storage).

## 7. Private MVP Implementation Notes

The MVP departs from, or fills gaps in, the target design in these ways:

- **Asynchronous requests.** An MV3 service worker is terminated when a single `fetch()` waits
  more than 30 seconds, and LLM drafting can exceed that.
  - `POST /api/tailor` runs the criteria gate inline. A rejection returns `200` with a
    finished result (`stage: "criteria"`).
  - Otherwise it queues the generation and returns `202 {request_id, status: "running"}`.
    The extension polls `GET /api/requests/{request_id}` every 3s until `status` is `done`
    (with a `result`) or `error`.
  - An optional `Idempotency-Key` returns the same request on retry, and `409` if reused with
    different input.
  - Results stay in memory for `AUTOAPPLIER_JOB_TTL` (15 min), which also allows re-download
    after a lost response. The API must run as a single process until request state moves to
    the database (§8.7).
- **Shared key, fail closed.** Every `/api` route requires `X-AutoApplier-Key`. If the server
  has no key configured it answers `503`. The per-IP limiter counts only requests that reach
  the model. Criteria rejections are free.
- **Criteria matching.** Title tokens match whole words, case-insensitively, so `ii` no longer
  matches "Hawaii". The pay parser handles `k` suffixes, ranges, and monthly figures, and no
  longer reads `$20000 / year` as `$200/hour`. An empty list or a `0` minimum disables a check.
- **Compilation.** `src/compiler.py` runs with `-no-shell-escape`, `openin_any=p` /
  `openout_any=p`, `-halt-on-error`, and a 30s timeout, in a per-request temporary directory.
  The image runs as a non-root user. A test confirms that `\input{/etc/hostname}` is blocked.
- **CV input.** Paste or load a .txt/.tex/.md file (≤ 50k characters). PDF text extraction is
  not supported yet.
- **Extraction.** The content scripts are injected only on click (`activeTab` + `scripting`),
  so the extension needs no broad host permissions.


## 8. Resolved Decisions

The original pipeline decisions are supplemented by the public account, credit, and billing
requirements below. Implementation choices still open are listed explicitly in §8.9.

### 8.1 Criteria gate — backend evaluates, extension owns the config

Criteria evaluation stays in Python on the backend, before the LLM call. The *configuration*
travels with the user: the extension's options GUI writes it to
`chrome.storage.sync`

`chrome.storage.sync` provides saved settings and optional cross-machine sync. Google sign-in
to AutoApplier serves a separate purpose: identifying the account that owns free/paid credits.
Authentication and durable usage tracking are required before public access, even though the
criteria themselves remain in extension storage.

### 8.2 Compiler concurrency — per-request temp directories

The CLI defaults in `src/compiler.py` use shared `output/logs/LaTeX_logs` and `output/pdfs`
directories, keyed by input filename. The API adapter instead creates a
`tempfile.TemporaryDirectory()` for each request and passes its input filepath and log/PDF
directories to that same compiler. It reads the returned PDF path into bytes before cleanup.
Concurrent requests therefore use separate intermediate files while reusing the existing
file-based compilation flow.

### 8.3 Backend protection — authenticated requests and bounded usage

CORS turns out to be the wrong frame for the real problem. Under Manifest V3, fetches issued from
the extension's **background service worker** to hosts listed in `host_permissions` are not
subject to CORS at all — only content-script fetches are. So all network calls go through
`background.js`, and the content script reaches it via `chrome.runtime.sendMessage`. The CORS
allowlist is still set to the extension's origin (with a pinned `"key"` in the manifest so that
origin is stable across unpacked loads) as defense in depth, but it is not what protects anything.

`X-AutoApplier-Key` now carries a per-account token issued by the operator. Only its SHA-256
is stored, and it resolves to an account on every request (`security.make_account_dependency`).
Requests, results and idempotency keys are scoped to that account, and the in-process limiter
counts per account. This replaced the original shared key. Before public
launch, require a valid backend user session on every generation and checkout request, and derive
the account from that session rather than a caller-supplied account ID or balance.

Add shared, durable account/IP attempt limits, concurrency limits, and input/output/time bounds.
Trust forwarded IP headers only from the configured deployment proxy. Failed or unqualified
model calls still cost money, so a released credit does not reset attempt limits. Apply an
application-enforced global generation/spend ceiling with an operator stop switch, counting
in-flight work and allowing for provider retries. Provider dashboards/alerts supplement this;
do not assume a budget setting automatically stops billable requests.

Three free credits limit successful outputs per account, not per human. Multiple Google accounts
remain a possible abuse route. Monitor signup and generation spikes and pause free grants when
necessary; tune limits before public launch instead of relying on the free-credit count alone.

### 8.4 LLM output trust — validate and sandbox, never regenerate

Validation and compilation controls:

1. **Strict `json_schema`** on the OpenAI call instead of `json_object`, parsed into a pydantic
   model, so `is_qualified` / `gap_analysis` / `sections` are guaranteed present and typed.
2. **The model selects, code writes** (§5.3). The model returns IDs from the user's own parsed CV
   and never authors LaTeX. Each selection is checked against the CV and repaired with CV content
   only, and every string is escaped by the renderer.
3. **A LaTeX sanity and denylist check** (`latex_guard.py`): require `\documentclass` and a
   matched `\begin{document}` / `\end{document}`, bound the length to catch truncated generations,
   and reject shell and file primitives (`\write18`, `\input{|`, `\openout`, `\openin`, `\read`,
   `\special`).
4. **Restricted compilation**: `-no-shell-escape`, restricted TeX file access, a hard subprocess
   timeout, and a per-request temp directory. A non-root deployment, resource limits, and
   verification of the untrusted-document isolation boundary remain public-launch work (§10).

**A repair retry was explicitly rejected.** Feeding a failed `pdflatex` log back to the model for
a second attempt would raise the success rate, but a second generation pass has no mechanism
tying its output back to the master CV — it can return a resume containing content that was never
in the source document. Since the user submits this document to real employers under their own
name, invented content is a misrepresentation they would be accountable for, and no compile-success
rate justifies it. On failure the backend returns the LaTeX source and the compiler log, and the
human fixes it. The same reasoning applies to any future feature that would let the model author
or re-author resume content.

### 8.5 Scraping — structured data first, human confirmation second

Per-site CSS selector adapters were rejected on both counts that made this a risk in the first
place: they break silently whenever a board ships a redesign, and maintaining a
LinkedIn/Indeed/Handshake selector set is precisely the pattern those sites' terms target.

The primary extractor instead reads the schema.org `JobPosting` JSON-LD that boards publish in a
`<script type="application/ld+json">` tag so search engines can index their listings — public
structured metadata, intended for machine consumption, stable across redesigns, and site-agnostic
in about thirty lines of dependency-free JavaScript. Coverage is good but not universal (public
postings on Greenhouse, Lever, Indeed, and LinkedIn generally carry it; auth-gated SPAs such as
Handshake generally do not), which is why it is paired with the editable pre-filled form rather
than shipped alone.

What keeps this defensible is the interaction model, not the extraction technique: the user opens
one page themselves, clicks once, and confirms the extracted fields before anything leaves the
browser. No crawling, no pagination, no background polling, no automated submission.

### 8.6 Google sign-in and backend accounts

Sign-in is required before the first free generation. Use Google's OpenID Connect sign-in flow,
validate the identity on the backend, and identify the account using the verified issuer and
`sub`, not an unverified email or ID sent by the extension. Verify signature, audience, issuer,
expiry, and the flow's state/nonce protections using maintained authentication libraries.
Request only the identity scopes needed for sign-in. The service does not need access to the
user's Drive or Gmail. See [Google's OpenID Connect guide](https://developers.google.com/identity/openid-connect/openid-connect).

Use an extension-compatible browser authentication flow, with configured callback URLs and a
stable extension ID; [Chrome's identity API](https://developer.chrome.com/docs/extensions/reference/api/identity)
provides browser authentication integration. The exact library and callback/session-exchange
implementation are Phase 2 decisions. Backend secrets must never be included in the extension.
Issue an expiring, revocable service session after verification; handle expiry, sign-out, and
account switching explicitly. Keep session credentials out of synced preferences and page scripts.

On first verified account creation, grant **3 free credits once** in a database transaction with
a uniqueness constraint on the trial grant. Concurrent sign-ins must not duplicate the grant.
Subsequent logins load the existing account. Clearing extension storage, changing devices,
changing the account's email, or reinstalling the extension does not replenish credits.

The extension may display balances, but every decision is made from backend records. Cached UI
values, request JSON, and Chrome storage cannot authorize a generation or change a balance.

### 8.7 Credits, request ownership, and failure accounting

**Product rule:** one successfully generated PDF costs one credit. The initial grant is 3 free
credits per Google account for the lifetime of that grant, with no periodic reset. Use free
credits before purchased credits. Pricing and purchased pack sizes remain open (§8.9).

| Outcome | Credit treatment | Model cost exposure |
|---|---|---|
| Invalid request, invalid session, or request limit reached | No credit consumed | No model call |
| Criteria gate rejects the job | No credit reserved or consumed | No model call |
| No available credit | Return payment-required response before generation | No model call |
| Model rejects qualification or generation/compilation fails | Release the reserved credit once | May still incur model cost; attempt limits remain in force |
| PDF successfully produced | Consume the reserved credit once | Record provider usage where available |
| Duplicate submission with the same request key | Return existing request state; no second reservation or generation | No new model call |

Require an `Idempotency-Key` for each user-initiated creation. Store it with the authenticated
account and an input fingerprint; enforce uniqueness on `(account_id, request_key)`. Reusing a
key for different input returns a conflict. Bind status/result access to that same account.

After the criteria gate passes, reserve one available credit atomically and create the request
record. Reservations reduce the available balance immediately. With one credit left, two
concurrent requests cannot both proceed. Commit the reservation before invoking the model;
if account/credit storage is unavailable, fail closed without a model call. Do not hold a
database transaction open during generation or compilation.

Track request states such as `reserved`, `running`, `completed`, and `failed`. Finalize credit
consumption or release in a transaction with a unique settlement record. Retries and worker
restarts cannot repeat settlement. Recover stale reservations with worker ownership/expiry
checks; do not release a credit while its worker can still finish and consume it. An ambiguous
provider timeout is not permission to blindly repeat the model call.

**Create a state machine to define this asap ^^**

Credit consumption is tied to PDF creation, not browser download acknowledgement. A lost HTTP
response must not trigger another model call or another charge under the same request key.
Persist request status even after temporary files are removed. **Delivery recovery is a
pre-launch decision:** either provide a bounded, documented temporary result cache for repeat
downloads, or a defined support/refund path when the successful PDF is no longer available.
The current immediate-cleanup adapter cannot replay a completed PDF; do not present this as
implemented recovery. Neither choice requires permanent resume history.

Minimum persistent records (database technology is not selected yet):

| Record | Purpose and constraints |
|---|---|
| Accounts and sessions | Unique verified Google identity, service account ID, active/revoked sessions |
| Trial grants | Once-only 3-credit grant per verified Google identity; deletion/retention policy before launch |
| Credit ledger and balances | Free/purchased grants, reservations, consumption, release, payment adjustments; atomic updates |
| Generation requests | Account, unique request key, fingerprint, state, reservation, timestamps, usage/error metadata; no raw CV/job content |
| Purchases and webhook receipts | Account-owned checkout/payment references, product/amount/currency, payment state, unique grant and event processing |

### 8.8 Paid credits and API additions

Use prepaid credit packs for the initial public release. **Stripe Checkout is the proposed
payment provider**, with hosted checkout so the extension/backend do not collect card details.
Google sign-in supplies identity; purchasing credits is a separate operation on that account.

The backend creates checkout sessions for authenticated users from a server-controlled product
catalog. It associates the purchase with the account before redirecting to checkout. The client
cannot choose an arbitrary price, number of credits, or recipient account. After payment, the
extension refreshes the balance; a checkout success page alone never grants credits.

Grant credits only after server-side payment verification. Verify webhook signatures against the
raw request body, confirm paid status and the expected purchase/product/amount/currency, and
process delivery idempotently. Deduplicate event IDs **and** the underlying purchase grant so
different events for the same payment cannot add credits twice. Handle delayed payment success,
failure, duplicate/out-of-order events, refunds, and disputes with recorded state transitions
and reconciliation. Define adjustment of unused credits and account handling when already-spent
credits are refunded/disputed before launch. See [Stripe's fulfillment guide](https://docs.stripe.com/checkout/fulfillment)
and [webhook documentation](https://docs.stripe.com/webhooks).

Proposed API responsibilities, to implement alongside the existing route:

| Interface | Contract |
|---|---|
| Authentication start/callback/session/logout routes | Verified Google sign-in, backend session issuance and revocation; exact route names depend on the chosen integration |
| `GET /api/me` | Authenticated account and server-calculated available/reserved free and purchased credits |
| `POST /api/tailor` | Existing payload and pipeline response, plus authenticated session, required request key, returned request ID and credit outcome |
| `GET /api/requests/{request_id}` | Authenticated owner-only request state and settlement outcome; result recovery follows the selected delivery policy |
| `POST /api/billing/checkout` | Authenticated purchase of a server-defined credit pack; return hosted checkout URL |
| `POST /api/billing/webhook` | Provider-authenticated webhook endpoint; verify signatures instead of requiring a browser session |

The asynchronous `POST /api/tailor` / `GET /api/requests/{id}` contract is implemented (§7).

For generation, use `401` for missing/expired authentication, `402` with a stable
`insufficient_credits` code for exhausted balance, `409` for conflicting request-key reuse,
`422` for invalid input, and `429` for attempt limits. Document how duplicate in-progress and
completed requests are represented. Preserve the existing pipeline's criteria/LLM/compile/
complete outcomes within this account-aware contract. These additions are not in today's
`schemas.py` or routes yet.

### 8.9 Decisions to close before public launch

- Select the database, migration tooling, Google authentication library, and session lifecycle.
- Confirm the payment provider and set pack sizes/prices using measured model, hosting,
  compilation, failure, payment-processing, and free-trial costs. No prices are specified yet.
- Choose request recovery/result-retention behavior and refund/dispute handling (§8.7–8.8).
- Define supported CV upload formats, text extraction, and size limits.
- Set account/IP/signup/concurrency limits and an application-enforced global cost ceiling.
- Define account deletion and metadata retention, including how prior free grants are treated.

## 9. CI/CD

**Current state:** `.github/workflows/main.yml` runs on every push and PR:
- Backend pytest with TeX Live installed; the job fails if any test is skipped.
- Extension syntax checks, JSON-LD parser tests, and a zipped extension artifact.
- A Docker build with a `/health` smoke test.

Deployment is not automated yet.

Target workflow:

1. On pull requests and pushes, install declared backend/test dependencies and a TeX
   distribution, run the backend suite, and ensure real compiler tests execute rather than skip.
   Add account/credit transaction, session, and payment replay tests as those features land.
2. Exercise database migrations and recovery in staging. Keep production credentials and live
   payments out of automated tests; use provider mocks/test mode.
3. Once extension files exist, validate/package them and produce a release artifact. Do not
   publish an empty extension directory as a completed release.
4. Deploy the API as an ASGI service, retaining `src/compiler.py` in the deployment artifact.
   The existing Dockerfile still starts the CLI and must be adapted. Verify health, restricted
   compilation, session/credit enforcement, and purchase reconciliation in staging.
5. Production deployment and Chrome Web Store release require the Phase 4 public-launch gate
   below. Passing pipeline unit tests alone is not that gate.

## 10. Roadmap

### v2 automation track (§2.1)

- [x] **A. Persistence and accounts** (2026-11-22).
  - Postgres via `docker-compose.yml`, SQLAlchemy models and Alembic migrations (CI checks that
    the models match the migrations).
  - Operator-issued account tokens, `GET /api/me`, and `GET/PATCH /api/profile` with AES-GCM
    encryption for CV, email and application profile.
  - Extension settings sync to the profile, and a notification email setting.
  - `pipeline.tailor` is shared by the API and the coming worker.
- [x] **B. Discovery** (2026-11-22).
  - `src/backend/sources/`: Greenhouse, Lever, Ashby and Simplify adapters, built against
    responses recorded on 2026-11-22. Greenhouse pay comes from `pay_transparency`.
  - `discovery.py` + `worker.py`: due sources are claimed with `FOR UPDATE SKIP LOCKED` and a
    lease. Polls are conditional (ETag), spaced by the interval with jitter, back off on
    failure, and keep at least 1 s between requests to one host.
  - The first poll is a baseline. A source rewatched after being idle re-baselines.
  - Postings that leave the feed are closed, and reopen if they return.
  - `dedupe_key` identifies one job across sources. It is applied per account, because accounts
    watch different sources.
  - `sources/enrich.py` fills Simplify descriptions from the ATS API or the page's JSON-LD
    (a Python port of `jsonld.js`). Phase C calls it only for postings that pass criteria.
  - Watchlist UI in Settings, and `GET/POST/DELETE /api/sources`, `GET /api/postings`.
  - **Load at scale:** request volume grows with distinct boards, not users, because sources and
    postings are shared and accounts read them from the database. On top of that:
    - a per-host request budget (`AUTOAPPLIER_HOST_RATE`);
    - a `429`/`503` with `Retry-After` pauses the whole host, and boards on it are deferred
      without counting as failures;
    - boards unchanged for 2 or 7 days are checked 2× or 4× less often.
  - **Managed Postgres:** server-side prepared statements are off, so transaction poolers
    (Supabase, PgBouncer) work. The API and worker still need an always-on host for `pdflatex`
    and the polling loop. Per-user decisions (shown, approved, skipped, applied) belong in Phase C's
    `matches` and never trigger board fetches.
- [x] **C. Automatic match and tailor** (2026-11-22).
  - **Task queue** (`tasks.py`): tasks are enqueued in the caller's transaction, so polling saves
    a posting and its `match_posting` task together. They are claimed with `FOR UPDATE SKIP
    LOCKED` and a lease, and keys make enqueueing idempotent. Failures back off exponentially
    until `max_attempts`, then the task is marked dead. `Retry` reschedules without counting an
    attempt.
  - **Matching** (`matching.py`): a match is made for accounts with automation on, a CV and
    criteria. It is skipped if the account already has the job from another source
    (`uq_matches_account_dedupe`). Criteria failures are recorded as `criteria_rejected` without
    an LLM call.
  - **Tailoring:**
    - The daily cap is checked under a row lock on the account. Over the cap, the match is
      `deferred` until 00:00 UTC.
    - Missing descriptions are fetched once onto the shared posting.
    - `pipeline.tailor` produces `ready` (PDF stored), `llm_rejected` or `failed`.
    - LLM errors are not retried (§8.4), and a match interrupted mid-tailoring resumes.
  - **State machine:** the match state machine covers Phases D and E as well; `transition()`
    rejects invalid moves.
  - **Worker:** one polling thread, plus `AUTOAPPLIER_TASK_THREADS` task threads and an hourly
    cleanup. The cleanup deletes rejected or skipped matches after 30 days, dead tasks after 7, and
    closed postings after 30 days unless a match still uses them.
  - **API and extension:** `PATCH /api/account` and `/api/matches` (list, resume, decision,
    retry). A popup Inbox, and an automation switch in Settings.
  - **Not yet moved:** the manual `/api/tailor` and `/api/cv/parse` requests still use the
    in-memory request store. Moving them onto the task queue would let the API run as several
    instances; that is still to do.
- [ ] **D. Email approval:** SMTP mailer, one email per match with the PDF, single-use expiring
  tokens, and a GET confirm page with the decision as a POST.
- [ ] **E. Assisted apply:** ATS form fillers in the extension, application profile UI, resume
  upload, screenshots, and submission detection. The user clicks Submit.

Statuses below reflect the repository on 2026-11-17. **The private MVP (the API half of Phase 2
and Phase 3 without accounts) is complete. Next are the Phase 2 account/credit items.**
Authentication and credits are now Phase 2 backend requirements; payment and abuse controls are
public-launch requirements, not features deferred until after launch. Phases 2–3 may overlap
against agreed API contracts, but public availability waits for Phase 4.

### Phase 1 — Local pipeline foundation: implemented, limited verification

- [x] Existing `src/` CLI, criteria matcher, LLM engine, file-based compiler, prompt, and fixtures.
- [x] Original matcher runner reported 12 passing cases in the initial review.
- [x] The CLI entry point was retired; the API (Phase 2) replaces it. A live model → PDF run is
  tracked in Phase 2.

This establishes the starting code to reuse, not a claim that every edge case is correct.

### Phase 2 — API integration and account/credit foundation: API done, accounts/credits pending

Present in the repository:

- [x] `/health`, `/api/tailor`, `/api/requests/{id}`, request/response models, criteria gate,
  model integration, LaTeX validation, and compile-failure responses.
- [x] Compiler adapter delegates to `src/compiler.py`, retaining the file interface and adding
  per-request temporary directories and explicit API restrictions/timeouts.
- [x] Private-development shared-key check and in-process rate limiter.
- [x] Backend pipeline/compiler tests and new file-path/failure regression cases.

Done in the private MVP:

- [x] Backend suite green, including real `pdflatex` tests in the Docker image and CI.
- [x] Fix and cover the free-text salary parsing issue (`$20000 / year` was read as `$200/hour`).
  Title tokens now match whole words, case-insensitively.
- [x] Declared backend/test dependencies, Dockerfile, and reproducible startup instructions.
- [x] Model error-path tests and API handling for refusal, malformed/empty output, and
  upstream failures.
- [ ] Verify a controlled live-model-to-PDF run with a real `OPENAI_API_KEY`.

Remaining account/credit work:

- [ ] Implement Google sign-in, verified account creation, expiring/revocable sessions, and logout.
- [ ] Implement database migrations, once-only 3-credit grants, atomic reservations/settlement,
  request-key deduplication, stale-work recovery, and account/request status endpoints.
- [ ] Verify simultaneous use of the last credit, repeated sign-in, worker interruption,
  database failure, and account isolation. Replace shared-key authorization for public routes.

Exit: reproducible API with passing tests, verified PDF generation, and durable authenticated
free-credit enforcement. This is still a private/staging service until the public-launch gate.

### Phase 3 — Extension and complete user journey: private MVP done, accounts pending

- [x] MV3 manifest, introductory page, and settings UI. (Google sign-in/session flow: pending.)
- [x] Save/reload criteria in `chrome.storage.sync`. CV paste or plain-text file upload (.txt/.tex/
  .md, 50k chars) persisted in `chrome.storage.local`. PDF text extraction is not supported.
- [x] JSON-LD extraction, editable heuristic fallback, and user confirmation.
- [x] Service-worker requests with stable idempotency keys across retries, pending/failed states,
  and recovery after the popup closes. (Credit balance, payment-required, session expiry: pending.)
- [x] PDF download and browser review. (Purchase entry point: pending Phase 4.)
- [x] Headless Chromium run of the unpacked extension against the Dockerized backend, with only
  the LLM call stubbed: JSON-LD and heuristic extraction, closing the popup mid-run, PDF download,
  result restored on reopen, and criteria rejection.
- [ ] Verify the full browser flow, including extension reopen, account switching, retries,
  cancellation, missing CV, and exhausted free credits. Verify targeted Chromium browsers.

Exit: a private end-to-end extension/API trial using real account-bound credits.

### Phase 4 — Paid credits and public launch: required before public access

- [ ] Resolve the pricing, provider, recovery, upload, retention, and limit decisions in §8.9.
- [ ] Implement account-bound hosted checkout, verified payment webhooks, idempotent credit
  grants, delayed-payment handling, refund/dispute adjustments, and reconciliation.
- [ ] Test duplicate/out-of-order payment events, altered client prices, unpaid checkout
  returns, purchase ownership, and attempted duplicate free grants.
- [ ] Implement shared durable attempt/concurrency controls, trusted proxy handling, signup
  abuse monitoring, global cost limits, and an operator stop switch.
- [ ] Verify production TeX isolation and resource bounds; a regex denylist and temporary
  directory alone do not establish a complete untrusted-document isolation boundary.
- [ ] Finish CI, backend deployment/migrations, backups/recovery for credit/payment records,
  and operational monitoring without logging document content or credentials.
- [ ] Publish accurate pricing, free-credit rules, failure/refund behavior, support contact,
  and privacy/retention information; complete required Google/store production configuration.
- [ ] Verify staging sign-in → 3 free successful PDFs → no fourth billable model call without
  credits → confirmed purchase → additional PDF. Rejections/failures preserve credits while
  attempt limits still apply. Test delivery loss and the chosen recovery policy.

Exit: all public-launch checks above verified. Open-source publication may happen earlier;
access to an owner-funded public API must wait for these controls.

### Phase 5 — Post-launch improvement: optional future work

- Scale workers and improve reliability using observed demand and costs.
- Refine abuse controls and support tooling from actual usage.
- Consider subscriptions or additional purchase options only after the initial credit model works.
- Consider opt-in document/history features separately; permanent resume storage is not required
  for accounts, credits, or billing.

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
