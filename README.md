# GeM Bid Compliance Verification Platform — Working Prototype

Team Index Labs · Tekathon 5.0 · Problem Statement SIH26100

This is a runnable prototype of the platform described in the pitch deck: upload
a bidder's PDF submission, and it extracts identity documents, checks them for
forgery signals, cross-checks them against registries, and produces a
0–100 Compliance Score with a Low/Medium/High risk band and a flag list for a
procurement officer to review — plus a tamper-evident audit trail of every
step.

## What's real vs. simulated (read this first)

Everything is genuinely implemented and runs — nothing is a mockup screen with
static numbers. But one layer is intentionally simulated, and the app tells you
which is which at every point it appears (a `real` or `simulated` badge next
to each section in the dashboard):

| Layer | Status | Why |
|---|---|---|
| PDF text extraction (pdfplumber) + OCR fallback (Tesseract) for scanned pages | **Real** | Runs on the actual uploaded file |
| GSTIN / PAN / CIN / Udyam field extraction (regex) | **Real** | Runs on the actual extracted text |
| GSTIN check-digit validation | **Real** | This is GSTN's own published mod-36 checksum algorithm — it catches typos/fabricated GSTINs with no API needed |
| PDF forensics: metadata, incremental-update ("re-opened after signing") detection, recycled-document detection via SHA-256 hash matching across submissions | **Real** | Inspects actual file bytes/structure; the demo includes a sample bid engineered to trip each of these |
| Make in India classification (Class-I / Class-II / Non-Local) | **Real** | Deterministic classification based on declared local content percentage per DPIIT Order P-45021/2/2017-PP |
| Compliance scoring engine | **Real** | Deterministic, configurable weights (`backend/scoring.py`) |
| Hash-chained audit log | **Real** | Each log row embeds the hash of the previous row; `/api/audit/verify` re-walks the chain |
| GSTN / Income Tax / MCA21 / DigiLocker / EPFO-ESIC / CPPP Debarment / NSIC / BIS-DPIIT / Startup India **registry lookups** | **Simulated** | These require registered GSP / API-Setu / EPFO Unified Portal credentials and a government approval cycle that doesn't fit a hackathon timeline. Responses are deterministic per document so demos are repeatable, and every payload carries `"source": "SIMULATED"`. |
| CPCL-specific tender eligibility rules (Manali Refinery, Cauvery Basin, Chennai Complex, Nagapattinam) | **Real** | Configurable in `tender_criteria.json`; includes turnover thresholds, local content requirements, and EPFO/BIS mandates |

**On "public sandbox APIs":** we looked. GSTN, MCA21, Udyam, DigiLocker,
EPFO and the CPPP debarment registry don't expose a free, keyless,
instantly-approved sandbox — commercial KYC resellers (Gridlines, AuthBridge,
Decentro, Cashfree, SignalX...) sit in front of the same registries and
require signup + business KYC of their own. The architecture is adapter-based
specifically so this is a one-file swap: replace the body of
`simulate_gstn_lookup()` etc. in `backend/verification.py` with a real HTTP
call once GSP credentials are issued — nothing else in the app changes.

## Architecture

```
backend/
  main.py          FastAPI app — orchestrates the pipeline per upload, serves index.html at "/"
  extraction.py    pdfplumber + Tesseract OCR fallback, regex field extraction
  verification.py  Real GSTIN checksum validation + simulated registry adapters
  forensics.py     PDF metadata, incremental-update, and duplicate-hash inputs
  eligibility.py   Checks extracted fields against tender_criteria.json
  scoring.py       Weighted 0–100 compliance score + risk band
  recommendations.py  Deterministic APPROVE/CLARIFY/REJECT suggestion for officers
  database.py      Postgres (psycopg2): pooled connections, bids table + hash-chained audit_log table
  tender_criteria.json   Per-tender eligibility rules (edit to add tenders)
  tests/           pytest suite for scoring, GSTIN checksum, eligibility, and the API
index.html         Officer dashboard (vanilla HTML/CSS/JS, no build step) — served at "/"
samples/           Two demo bid PDFs (one clean, one engineered to trip every flag)
```

Pipeline per upload: **extract → forensic scan (incl. recycled-document
check via SHA-256) → GSTIN checksum validation → (simulated) registry
lookups → eligibility check against the chosen tender → score → log every
step to the audit trail → return the report to the dashboard.**

## Run it locally

Requires Python 3.10+, a Postgres database (Supabase, RDS, local — anything
reachable via a standard connection string), and for OCR: `tesseract-ocr` +
`poppler-utils` (both already on most Linux dev boxes; on macOS
`brew install tesseract poppler`; on Windows install Tesseract and add it to
PATH — OCR is only used as a fallback, the app still runs without it).

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL, then:
uvicorn backend.main:app --reload --port 8000
```

Tables are created automatically on first startup — no separate migration
step. **Never commit `.env` or paste a real `DATABASE_URL` anywhere** (chat,
issues, PRs) — if a credential ever does leak, rotate it immediately (in
Supabase: Project Settings → Database → Reset database password) rather than
assuming no one saw it.

If your password contains `@`, `#`, `:`, `/` or `?`, percent-encode those
characters (`@` → `%40`, `#` → `%23`) in the connection string — an
un-encoded `#` in particular gets treated as a URL fragment delimiter and
silently truncates everything after it.

The two demo PDFs below are already generated and committed under `samples/`.
To regenerate them (or to run the test suite), install the dev extras first:
`pip install -r requirements-dev.txt`.

Open **http://localhost:8000**. Use "Submit a Bid" and upload one of the files
in `samples/`:

- `bid_vantara_systems.pdf` — clean bid, scores 100/Low.
- `bid_northstar_traders.pdf` — engineered to fail: invalid GSTIN check
  digit, a post-signing incremental edit, and a Startup-India claim with no
  Udyam number to back it. Scores Medium and lists all three flags.

Upload `bid_vantara_systems.pdf` a second time under a different bidder name
to see the recycled-document detector fire (identical file hash reused by a
different bidder).

Check **Audit Trail** afterwards to see every extraction/scoring step logged
and the chain-integrity check pass.

To regenerate the sample PDFs: `python3 samples/generate_samples.py` (needs
`requirements-dev.txt`).

To wipe all data and start over: `curl -X DELETE http://localhost:8000/api/reset`
(drops and recreates both tables).

## Running the tests

```bash
docker compose run --rm tests        # easiest: scratch Postgres + full suite, nothing to install
```

or against your own scratch Postgres:

```bash
pip install -r requirements-dev.txt
export TEST_DATABASE_URL=postgresql://gem:gem@localhost:5433/gem_test   # `docker compose up -d db`
pytest backend/tests/ -v
```

Covers the scoring engine, the GSTIN checksum validator, the eligibility
engine, and the `/api/verify` → `/api/bids` → `/api/audit` flow end-to-end.
The database tests **drop and recreate all tables**, so they only ever use
`TEST_DATABASE_URL` and refuse to run against `DATABASE_URL` (the pure-logic
tests — scoring, eligibility, GSTIN, security helpers — need no database).
Hardening tests cover: officer auth, reset gating, upload limits, rate
limiting, the append-only audit log, transaction rollback, duplicate-upload
locking, and concurrent audit appends.

## Deploying it

**Docker (any host — Render, Railway, Fly.io, a VM):**

```bash
docker build -t gem-compliance .
docker run -p 8000:8000 -e DATABASE_URL="postgresql://..." gem-compliance
```

`DATABASE_URL` is read at runtime, not baked into the image — pass it via
`-e`/your host's env-var settings or secrets manager.

**Render / Railway without Docker:** point the build at this repo, set the
start command to `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`, set
`DATABASE_URL` in the platform's environment-variable settings, and add a
build step `apt-get install -y tesseract-ocr poppler-utils` (or use their
Docker-native deploy path with the Dockerfile above, which is simpler).

Postgres being a real managed database (rather than SQLite's single local
file) means the data now survives redeploys without needing a persistent
volume — that was the main reason for this migration.

## Database notes

- **Connection pool:** `psycopg2.pool.ThreadedConnectionPool` (default 1–10
  connections, tunable via `DB_POOL_MIN` / `DB_POOL_MAX` / `DB_POOL_WAIT` in
  `.env`). Handlers borrow a connection with `database.get_conn()`; requests
  wait for a free connection rather than failing when the pool is busy.
- **Transactions:** write handlers (`/api/verify`, `/api/bids/{id}/decision`)
  run in a single transaction — the bid row and all its audit rows commit
  together or not at all. Read handlers use a read-only Postgres session.
- **Startup:** the app retries the initial connection with backoff, creates
  the tables if missing, and closes the pool on shutdown.
- **Schema:** NOT NULL on required columns, `risk_level` restricted to
  Low/Medium/High/Critical, unique `row_hash`, and indexes on the columns
  actually queried. `CREATE TABLE IF NOT EXISTS` does not alter tables that
  already exist — to pick up the new constraints on an existing database, add
  them with `ALTER TABLE` or (only if the data is disposable) temporarily set
  `ENABLE_RESET=true` and call `DELETE /api/reset`.
- **Append-only audit log:** a database trigger rejects `UPDATE`, `DELETE`
  and `TRUNCATE` on `audit_log`. Together with the hash chain, editing history
  needs a schema change, not just an `UPDATE`. For stronger guarantees, run the
  app as a DB role that has no `ALTER`/`DROP` rights on that table.
- **Duplicate uploads:** concurrent submissions of the same file are serialized
  with a per-hash advisory lock, so "recycled document" detection can't race.

## Security & operations

All settings are environment variables (see `.env.example`).

| Concern | Behaviour |
|---|---|
| Officer auth | Set `OFFICER_TOKENS=name:token,...` (tokens ≥16 chars). Decisions and recommendations then require `Authorization: Bearer <token>` (or `X-API-Key`), and the audit `actor` is the authenticated officer's name — a client-supplied name is ignored. The web UI prompts for the token on the first officer action and keeps it in `sessionStorage`. Unset = **open dev mode** (warned at startup). |
| Reset | `DELETE /api/reset` is **off** unless `ENABLE_RESET=true`; if `ADMIN_TOKEN` is set it must be presented; with officer auth on and no `ADMIN_TOKEN` it stays disabled. |
| Production guard | `APP_ENV=production` refuses to start without `OFFICER_TOKENS` or with `ENABLE_RESET` on. |
| Uploads | Max `MAX_UPLOAD_MB` (default 10), must start with a `%PDF-` header, corrupt files return 422, oversized `Content-Length` is rejected before the body is spooled. |
| Rate limit | `RATE_LIMIT_PER_MIN` (default 30) per client IP on `/api/verify` and decisions → 429 + `Retry-After`. In-memory, per worker process. Behind a proxy set `TRUST_PROXY=true`. |
| Load | PDF analysis runs in worker threads (the event loop stays responsive) and at most `MAX_CONCURRENT_ANALYSIS` (default 4) run at once; extra requests wait, then get 503. |
| Pagination | `/api/bids` and `/api/audit` accept `?limit=&offset=` and return `X-Total-Count`. Without `limit` they return everything (the UI's client-side chain check needs the full log). |
| Health | `/api/health` reports DB connectivity and pool usage. |

**Known limitations:** the read endpoints (`GET /api/bids`, `/api/audit`) are
still unauthenticated because the demo UI has no bidder login — anyone who can
reach the server can read bids. Put the app behind a VPN/SSO or add bidder auth
before exposing it. The GST / income-tax registry lookups are simulated.

## Extending toward production

1. **Swap in real registry APIs** once GSP/API-Setu access is granted — same
   function signatures in `verification.py`, everything downstream (scoring,
   audit log, dashboard) is unchanged.
2. **Add bidder authentication and per-bidder read access** (officer actions are token-protected, reads are not yet).
3. **Add more forensic signals** — e.g. font-substitution detection, EXIF
   checks on embedded photos, and a real digital-signature (PAdES) validator
   for documents that carry one.
