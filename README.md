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
| Compliance scoring engine | **Real** | Deterministic, configurable weights (`app/scoring.py`) |
| Hash-chained audit log | **Real** | Each log row embeds the hash of the previous row; `/api/audit/verify` re-walks the chain |
| GSTN / Income Tax / Udyam / MCA21 / DigiLocker **registry lookups** | **Simulated** | These require a registered GSP / API-Setu credential and a government approval cycle that doesn't fit a hackathon timeline (we checked — see below). Responses are deterministic per document (same input always gives the same simulated result) so demos are repeatable, and every payload carries `"source": "SIMULATED"`. |

**On "public sandbox APIs":** we looked. GSTN, MCA21, Udyam and DigiLocker
don't expose a free, keyless, instantly-approved sandbox — commercial KYC
resellers (Gridlines, AuthBridge, Decentro, Cashfree, SignalX...) sit in front
of the same registries and require signup + business KYC of their own. The
architecture is adapter-based specifically so this is a one-file swap: replace
the body of `simulate_gstn_lookup()` etc. in `app/verification.py` with a real
HTTP call once GSP credentials are issued — nothing else in the app changes.

## Architecture

```
app/
  main.py          FastAPI app — orchestrates the pipeline per upload
  extraction.py    pdfplumber + Tesseract OCR fallback, regex field extraction
  verification.py  Real format/checksum validation + simulated registry adapters
  forensics.py     PDF metadata, incremental-update, and duplicate-hash detection
  scoring.py       Weighted 0–100 compliance score + risk band
  database.py      SQLite: bids table + hash-chained audit_log table
  tender_criteria.json   Per-tender eligibility rules (edit to add tenders)
static/            Officer dashboard (vanilla HTML/CSS/JS, no build step)
samples/           Two demo bid PDFs (one clean, one engineered to trip every flag)
```

Pipeline per upload: **extract → forensic scan → identity validation →
(simulated) registry lookups → eligibility check against the chosen tender →
score → log every step to the audit trail → return the report to the
dashboard.**

## Run it locally

Requires Python 3.10+, and for OCR: `tesseract-ocr` + `poppler-utils` (both
already on most Linux dev boxes; on macOS `brew install tesseract poppler`; on
Windows install Tesseract and add it to PATH — OCR is only used as a fallback,
the app still runs without it).

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

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

To regenerate the sample PDFs: `python3 samples/generate_samples.py`.

To wipe all data and start over: `curl -X DELETE http://localhost:8000/api/reset`.

## Deploying it

**Docker (any host — Render, Railway, Fly.io, a VM):**

```bash
docker build -t gem-compliance .
docker run -p 8000:8000 gem-compliance
```

**Render / Railway without Docker:** point the build at this repo, set the
start command to `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, and add
a build step `apt-get install -y tesseract-ocr poppler-utils` (or use their
Docker-native deploy path with the Dockerfile above, which is simpler).

SQLite is file-based, so on most PaaS free tiers the database resets on
redeploy unless you attach a persistent volume — fine for a hackathon demo,
worth flagging to judges if asked about production durability.

## Extending toward production

1. **Swap in real registry APIs** once GSP/API-Setu access is granted — same
   function signatures in `verification.py`, everything downstream (scoring,
   audit log, dashboard) is unchanged.
2. **Swap SQLite for Postgres** — `database.py` is the only file that touches
   storage.
3. **Add authentication** for the officer portal (currently open, as a demo).
4. **Add more forensic signals** — e.g. font-substitution detection, EXIF
   checks on embedded photos, and a real digital-signature (PAdES) validator
   for documents that carry one.
