# AutoApplier — Design Document

**Status (2026-09-10):** Phase 2 in progress: an API prototype wraps the local pipeline;
compiler integration awaits verification. Google sign-in, credits, billing, and the extension
are planned. Public launch requires the account and payment gates in §10.

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

## 3. Architecture Overview

This is the public-launch target. The current route still uses a shared key and has no
account/credit/payment integration; the diagram is not a claim that those parts are implemented.

```
[ User's Browser ]
  └── Activate extension → introductory page explaining the service
      └── Sign in with Google → backend session and account balance
      └── Settings: save criteria to chrome.storage.sync
      └── Upload CV/resume: save its content to chrome.storage.local
  └── Content script: JSON-LD JobPosting → else pre-filled form → user confirms
      └── Reads master CV from chrome.storage.local
      └── Reads criteria from chrome.storage.sync
            │
            ▼  chrome.runtime.sendMessage
  └── Background service worker (adds user session and Idempotency-Key)
            │
            ▼  HTTPS POST /api/tailor  { job, resume_text, criteria }
[ FastAPI Proxy Backend ]
  0. Verify session; enforce durable account/IP attempt and concurrency limits
  1. Criteria gate (deterministic title/location/pay check) — stops here if it fails
  2. Database: deduplicate request and atomically reserve 1 available credit
     └── No credit → payment required; do not call the LLM (A DB is required, idk where to host yet)
  3. LLM call → { is_qualified, gap_analysis, latex_code }
  4. If qualified: validate latex_code, then compile → PDF (restricted pdflatex, temp dir)
  5. Database: consume reservation on successful PDF; release on rejection/failure
            │
            ▼  { stage, is_qualified, reason, gap_analysis,
                 latex_source, resume_pdf_base64, compile_log,
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
```

## 4. Target Repository Structure

New account/billing modules below are planned responsibilities, not existing files. Keep the
existing pipeline functions and add API/account integration around them.

```text
AutoApplier/
├── DESIGN.md
├── README.md
├── .github/
│   └── workflows/
│       └── main.yml            # test backend, package + release extension
├── src/
│   └── compiler.py              # existing filepath-based compiler, shared with the API
├── backend/                     # FastAPI proxy (deployed, e.g. Render)
│   ├── main.py                  # /api/tailor route
│   ├── schemas.py               # pydantic request/response models
│   ├── security.py              # target: session authorization + durable request limits
│   ├── auth.py                  # planned: Google sign-in, backend sessions, logout
│   ├── credits.py               # planned: free grants, reservations, settlement, ledger
│   ├── billing.py               # planned: checkout, signed webhooks, payment reconciliation
│   ├── db.py                    # planned: transactional account/usage/payment persistence
│   ├── migrations/              # planned: schema and uniqueness constraints
│   ├── criteria_matcher.py      # ported from src/, now accepts criteria as a dict
│   ├── llm_engine.py            # ported from src/, called in-process instead of via CLI
│   ├── latex_guard.py           # validates model LaTeX before it reaches pdflatex
│   ├── compiler.py              # API adapter around src/compiler.py; temporary request files
│   ├── prompts/
│   │   └── resume_builder_prompt.txt
│   ├── criteria.json            # default criteria; users override from the extension
│   ├── requirements.txt
│   └── tests/
└── extension/                    # Chromium extension (Chrome/Edge/Brave)
    ├── manifest.json             # MV3; pinned "key" for a stable extension ID
    ├── jsonld.js                 # schema.org JobPosting extractor
    ├── content_script.js         # runs jsonld.js, falls back to page heuristics
    ├── background.js             # service worker — the only code that calls the backend
    ├── welcome.html              # instructions, Google sign-in, link to settings
    ├── options.html              # criteria fields and CV/resume upload
    ├── options.js                # criteria → storage.sync; CV content → storage.local
    ├── popup.html                # posting confirmation, credits, purchase, PDF download
    └── popup.js                  # storage, messaging, account state, PDF download
```

The current `src/`, `prompts/`, `criteria.json`, and `tests/` at the repo root map directly into
the API integration (see the reuse table below). In particular, `src/compiler.py` remains the
shared file-based compiler; `backend/compiler.py` adapts request text and PDF bytes around it.

## 5. Pipeline Walkthrough

### 5.0 User Setup and End-to-End Flow

1. The user activates the extension and opens an introductory page explaining how to use the
   service, the 3 free creations, paid credits, and where to find settings. Google sign-in is
   required before the first generation. The backend grants the account's free credits once.
2. In settings, the user edits the criteria fields (target/banned title tokens, allowed
   locations, and minimum pay). The extension saves these small preferences to
   `chrome.storage.sync` and loads them on later visits, so they do not need to be re-entered.
3. The user uploads their master CV/resume. Its content is saved in `chrome.storage.local`,
   which provides more room than sync storage, and is reused for later tailoring requests.
4. The user visits a job posting, invokes tailoring, and confirms the extracted posting fields.
   The extension sends the job, saved CV text, and saved criteria with its session and a unique
   request key. It displays the server-reported remaining free/paid credits.
5. The backend authenticates the user, checks attempt limits, applies the criteria gate, and
   reserves a credit before calling the LLM. A zero available balance prompts purchase without
   a model call. For a qualified result, it parses the returned JSON's `latex_code`, writes
   that source to a temporary `.tex` file, and passes the filepath to the existing compiler through the API adapter.
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
`src/criteria_matcher.py` provides the original criteria gate — checking title tokens against
`target_tokens` / `banned_tokens` / `allowed_locations` and parsing pay against the minimum
thresholds. The **evaluation** runs in the backend before credit reservation and the LLM call.
The current backend port and original CLI matcher coexist; their behavior differences need
review rather than being assumed identical. The **configuration** belongs to the user: the
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

### 5.3 LLM Qualification + Resume Drafting
`src/llm_engine.py` and `prompts/resume_builder_prompt.txt` already implement the richer contract
the pipeline needs — an OpenAI call returning JSON with `is_qualified`, `gap_analysis`, and (when
qualified) a complete one-page `latex_code` string with double-escaped backslashes. The backend
reuses this prompt and JSON contract as-is, but tightens the call from a loose `json_object`
response format to a strict `json_schema` parsed into a pydantic model, so a missing key is a
validation error rather than an unguarded `KeyError` mid-pipeline (§8.4). If `is_qualified` is
false, the backend returns the gap analysis and stops; the credit-enabled route releases the
reservation without consuming a credit.

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

- **Master CV:** uploaded by the user and saved as serializable content in
  `chrome.storage.local`, then reused across requests. CV text is sent to the backend per-request
  and never persisted server-side. Local storage provides more capacity than sync storage and
  keeps the CV on that browser profile rather than syncing it across machines. Supported upload
  formats and text extraction are still to be defined; the current API accepts `resume_text`,
  so a stored upload must supply text before a tailoring request.
- **Criteria:** stored in `chrome.storage.sync`, so a user's thresholds follow them across
  machines when Chrome Sync is enabled (§8.1). Service sign-in is separate from browser sync;
  the backend does not need to store criteria to maintain account/credit records.
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

## 7. temp


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

The existing `X-AutoApplier-Key` check and in-process IP limiter support private development.
An extension-distributed key is extractable and cannot identify who owns credits. Before public
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
   model, so `is_qualified` / `gap_analysis` / `latex_code` are guaranteed present and typed.
2. **A LaTeX sanity and denylist check** (`latex_guard.py`): require `\documentclass` and a
   matched `\begin{document}` / `\end{document}`, bound the length to catch truncated generations,
   and reject shell and file primitives (`\write18`, `\input{|`, `\openout`, `\openin`, `\read`,
   `\special`).
3. **Restricted compilation**: `-no-shell-escape`, restricted TeX file access, a hard subprocess
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

**Current state:** `.github/workflows/main.yml` exists but is empty so currently there is no working CI/deployment pipeline established by
these files.

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

Statuses below reflect repository inspection on 2026-09-10. **Current work is Phase 2.**
Authentication and credits are now Phase 2 backend requirements; payment and abuse controls are
public-launch requirements, not features deferred until after launch. Phases 2–3 may overlap
against agreed API contracts, but public availability waits for Phase 4.

### Phase 1 — Local pipeline foundation: implemented, limited verification

- [x] Existing `src/` CLI, criteria matcher, LLM engine, file-based compiler, prompt, and fixtures.
- [x] Original matcher runner reported 12 passing cases in the initial review.
- [ ] Verify a full CLI → model → PDF smoke run; no live model run was performed in that review.

This establishes the starting code to reuse, not a claim that every edge case is correct.

### Phase 2 — API integration and account/credit foundation: in progress

Present in the repository:

- [] `/health`, `/api/tailor`, request/response models, criteria gate, model integration,
  LaTeX validation, and compile-failure responses.
- [x] Compiler adapter delegates to `src/compiler.py`, retaining the file interface and adding
  per-request temporary directories and explicit API restrictions/timeouts.
- [] Private-development shared-key check and in-process rate limiter.
- [] Backend pipeline/compiler tests and new file-path/failure regression cases.

Remaining work:

- [ ] Run the updated backend tests. The **pre-adjustment** run had 46 passes and 4 compiler
  failures; the subsequent compiler edits/new regression cases have only had syntax/diff checks
  in this session. Do not claim the modified suite is green until rerun.
- [ ] Fix and cover the inherited free-text salary parsing issue (`$20000 / year` can be
  interpreted as `$200/hour`); review criteria/LLM changes against the original implementation.
- [ ] Add declared backend/test dependencies and reproducible API startup instructions.
- [ ] Add model error-path tests and API handling for refusal, malformed/empty output, and
  upstream failures. Verify a controlled live-model-to-PDF run separately.
- [ ] Implement Google sign-in, verified account creation, expiring/revocable sessions, and logout.
- [ ] Implement database migrations, once-only 3-credit grants, atomic reservations/settlement,
  request-key deduplication, stale-work recovery, and account/request status endpoints.
- [ ] Verify simultaneous use of the last credit, repeated sign-in, worker interruption,
  database failure, and account isolation. Replace shared-key authorization for public routes.

Exit: reproducible API with passing tests, verified PDF generation, and durable authenticated
free-credit enforcement. This is still a private/staging service until the public-launch gate.

### Phase 3 — Extension and complete user journey: not started

`extension/` is currently empty.

- [ ] Create the MV3 manifest, introductory page, Google sign-in/session flow, and settings UI.
- [ ] Save/reload criteria in `chrome.storage.sync`; implement CV upload, text extraction, size
  handling, and persistence in `chrome.storage.local`.
- [ ] Implement JSON-LD extraction, editable fallback, and user confirmation.
- [ ] Make authenticated service-worker requests with stable keys across retries; show credit
  balance, pending/failed states, payment-required state, and session-expiry recovery.
- [ ] Implement PDF download and browser review; add the purchase entry point for Phase 4 checkout.
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
