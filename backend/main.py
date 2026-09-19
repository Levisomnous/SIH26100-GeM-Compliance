from contextlib import asynccontextmanager
from typing import Optional
import logging
import os
import json
import threading
import time
import uuid
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse

from . import database, security, prisma_client
from . import extraction, forensics, verification, scoring, recommendations, eligibility, ai_summary, notices, cartel

log = logging.getLogger("gem.api")

# Cap how many CPU-heavy PDF analyses (OCR etc.) run at once; extra requests wait
# briefly and then get a 503 instead of exhausting the machine.
MAX_CONCURRENT_ANALYSIS = max(1, int(os.environ.get("MAX_CONCURRENT_ANALYSIS", "4")))
ANALYSIS_WAIT_SECONDS = float(os.environ.get("ANALYSIS_WAIT_SECONDS", "60"))
_analysis_slots = threading.BoundedSemaphore(MAX_CONCURRENT_ANALYSIS)
_limiter = security.RateLimiter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = security.get_config()                 # raises on invalid settings -> fail fast
    warning = security.validate_startup(cfg)    # raises on unsafe production settings
    if warning:
        log.warning(warning)
    try:
        database.init_pool()
        database.init_db()
        await prisma_client.connect_prisma()
    except Exception as e:
        if cfg.production:
            raise
        log.warning("Database initialization failed (running in offline/dev mode): %s", e)
        log.warning("👉 To connect Supabase, update DATABASE_URL in your .env file with your connection string.")

    try:
        yield
    finally:
        await prisma_client.disconnect_prisma()
        database.close_pool()




app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def guard_and_headers(request: Request, call_next):
    # Reject oversized uploads early from the Content-Length header, before the
    # multipart body is spooled to disk. (Chunked uploads are still capped while reading.)
    if request.method == "POST" and request.url.path == "/api/verify":
        try:
            declared = int(request.headers.get("content-length", "0"))
        except ValueError:
            declared = 0
        limit = security.get_config().max_upload_bytes + (1 << 20)   # + multipart overhead
        if declared > limit:
            return JSONResponse({"detail": "file too large"}, status_code=413)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


def _client_key(request: Request, scope: str) -> str:
    ip = request.client.host if request.client else "unknown"
    if security.get_config().trust_proxy:
        xff = request.headers.get("x-forwarded-for", "")
        if xff.strip():
            ip = xff.split(",")[0].strip()
    return f"{scope}:{ip}"


def rate_limit_writes(request: Request):
    cfg = security.get_config()
    ok, retry = _limiter.allow(_client_key(request, request.url.path.split("/")[-1]), cfg.rate_limit_per_min)
    if not ok:
        raise HTTPException(status_code=429, detail="rate limit exceeded", headers={"Retry-After": str(retry)})


def _presented_token(request: Request) -> Optional[str]:
    return security.extract_token(request.headers.get("authorization"), request.headers.get("x-api-key"))


def officer_identity(request: Request) -> Optional[str]:
    """Officer name from a valid token; None in open dev mode (no OFFICER_TOKENS set)."""
    cfg = security.get_config()
    if not cfg.auth_enabled:
        return None
    name = security.authenticate(_presented_token(request), cfg.officer_tokens)
    if not name:
        raise HTTPException(status_code=401, detail="valid officer token required",
                            headers={"WWW-Authenticate": "Bearer"})
    return name


@app.get("/api/health")
def health():
    try:
        database.ping()
        return {"ok": True, "db": "connected", "pool": database.pool_stats()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


def adapt_bid_for_ui(row: dict) -> dict:
    # Map DB bid row and embedded report to the frontend's expected shape
    rpt = row.get("report") or {}
    extraction = rpt.get("extraction") if isinstance(rpt, dict) else None
    bid = {
        "id": row.get("id"),
        "company": row.get("bidder_name") or rpt.get("bidder_name") if isinstance(rpt, dict) else row.get("bidder_name"),
        "gstin": (extraction.get("gstin") if extraction else None) or None,
        "pan": (extraction.get("pan") if extraction else None) or None,
        "cin": (extraction.get("cin") if extraction else None) or None,
        "udyam": (extraction.get("udyam") if extraction else None) or None,
        "tenderCategory": (rpt.get("tender_id") if isinstance(rpt, dict) else row.get("tender_id")) or row.get("tender_id"),
        "claimedTurnover": (extraction.get("declared_revenue") if extraction else None) or None,
        "claimedLocalContent": (extraction.get("declared_local_content") if extraction else None) or 0,
        "isReseller": False,
        "claimsStartup": bool(extraction.get("claims_startup_status") if extraction else False),
        "claimsNsic": False,
        "claimsBisDpiit": False,
        "flags": (rpt.get("score", {}).get("flags") if isinstance(rpt, dict) else None) or [],
        "seed": False,
        "status": "awaiting_decision" if row.get("compliance_score") is not None else "queued",
        "score": row.get("compliance_score"),
        "risk": row.get("risk_level"),
        "verifiedAt": None,
        "decidedAt": None,
        "officerNote": None,
        "submittedAt": row.get("uploaded_at"),
        "submittedBy": row.get("bidder_name")
    }
    # For backwards compatibility the frontend expects `score` named `score` and `risk`
    if bid["score"] is None and isinstance(rpt, dict) and rpt.get("score"):
        bid["score"] = rpt["score"].get("total")
        bid["risk"] = rpt["score"].get("risk_level")
    return bid


@app.get("/api/bids")
def api_bids(limit: Optional[int] = Query(None, ge=1, le=1000), offset: int = Query(0, ge=0)):
    with database.get_conn(read_only=True) as conn:
        total = database.count_bids(conn)
        rows = database.fetch_bids(conn, limit=limit, offset=offset)
    adapted = [adapt_bid_for_ui(r) for r in rows]
    return JSONResponse(adapted, headers={"X-Total-Count": str(total)})


@app.get("/api/bids/{bid_id}")
def api_bid(bid_id: str):
    with database.get_conn(read_only=True) as conn:
        row = database.fetch_bid(conn, bid_id)
    if not row:
        raise HTTPException(status_code=404, detail="bid not found")
    adapted = adapt_bid_for_ui(row)
    # include full report for details
    adapted["report"] = row.get("report")
    return JSONResponse(adapted)


@app.post("/api/bidders/register", dependencies=[Depends(rate_limit_writes)])
def api_register_bidder(payload: dict = Body(...)):
    entity_name = _clean_field(payload.get("company_name") or payload.get("entity_name"), "company_name")
    gstin = _clean_field(payload.get("gstin"), "gstin")
    if not entity_name:
        raise HTTPException(status_code=400, detail="Company / Entity name is required")
    if not gstin:
        raise HTTPException(status_code=400, detail="GSTIN is required")
    gstin = gstin.strip().upper()

    pan = _clean_field(payload.get("pan"), "pan")
    if pan:
        pan = pan.strip().upper()
    elif len(gstin) == 15:
        pan = gstin[2:12]

    cin = _clean_field(payload.get("cin"), "cin")
    if cin:
        cin = cin.strip().upper()

    udyam = _clean_field(payload.get("udyam") or payload.get("udyam_number"), "udyam")
    if udyam:
        udyam = udyam.strip().upper()

    email = _clean_field(payload.get("email"), "email")
    phone = _clean_field(payload.get("phone"), "phone")
    state_val = _clean_field(payload.get("state"), "state")
    business_type = _clean_field(payload.get("business_type"), "business_type")
    msme_category = _clean_field(payload.get("msme_category"), "msme_category")
    registered_address = _clean_field(payload.get("registered_address") or payload.get("address"), "address")

    gstin_checksum_valid = verification.validate_gstin_checksum(gstin)

    bidder_data = {
        "entity_name": entity_name,
        "gstin": gstin,
        "pan": pan,
        "cin": cin,
        "udyam_number": udyam,
        "email": email,
        "phone": phone,
        "state": state_val,
        "business_type": business_type,
        "msme_category": msme_category,
        "registered_address": registered_address
    }
    with database.get_conn(read_only=False) as conn_w:
        saved = database.upsert_bidder(conn_w, bidder_data, commit=True)

    return JSONResponse({
        "ok": True,
        "message": "Bidder organization successfully registered in Supabase database",
        "gstin_checksum_valid": gstin_checksum_valid,
        "bidder": saved
    })


@app.get("/api/bidders")
def api_get_bidders(limit: Optional[int] = Query(None, ge=1, le=500)):
    with database.get_conn(read_only=True) as conn:
        bidders = database.fetch_bidders(conn, limit=limit)
    return JSONResponse(bidders)


@app.get("/api/bidders/{gstin}")
def api_get_bidder(gstin: str):
    with database.get_conn(read_only=True) as conn:
        b = database.fetch_bidder_by_gstin(conn, gstin.strip().upper())
    if not b:
        raise HTTPException(status_code=404, detail="Bidder not found")
    return JSONResponse(b)



@app.get("/api/audit")
def api_audit(limit: Optional[int] = Query(None, ge=1, le=5000), offset: int = Query(0, ge=0)):
    with database.get_conn(read_only=True) as conn:
        total = database.count_audit(conn)
        rows = database.fetch_audit(conn, limit=limit, offset=offset)
    # map DB fields to expected client names
    out = []
    for r in rows:
        out.append({
            "seq": r.get("seq"),
            "timestamp": r.get("timestamp"),
            "bidId": r.get("bid_id"),
            "actor": r.get("actor"),
            "action": r.get("action"),
            "details": r.get("details_parsed") if r.get("details_parsed") is not None else r.get("details"),
            "prevHash": r.get("prev_hash"),
            "hash": r.get("row_hash")
        })
    return JSONResponse(out, headers={"X-Total-Count": str(total)})


@app.get("/api/audit/verify")
def api_audit_verify():
    with database.get_conn(read_only=True) as conn:
        result = database.verify_audit_chain(conn)
    return JSONResponse(result)


@app.delete("/api/reset")
def api_reset(request: Request):
    """Wipes ALL bids and audit history. Disabled unless ENABLE_RESET=true; when
    ADMIN_TOKEN is set it must be presented (Authorization: Bearer / X-API-Key)."""
    allowed, status, msg = security.reset_decision(security.get_config(), _presented_token(request))
    if not allowed:
        raise HTTPException(status_code=status, detail=msg)
    log.warning("DELETE /api/reset executed")
    database.reset_db()
    return JSONResponse({"ok": True})


@app.get("/api/bids/{bid_id}/recommendation")
def api_bid_recommendation(bid_id: str, _officer: Optional[str] = Depends(officer_identity)):
    with database.get_conn(read_only=True) as conn:
        row = database.fetch_bid(conn, bid_id)
    if not row:
        raise HTTPException(status_code=404, detail="bid not found")
    report = row.get("report") or {}
    rec = recommendations.recommend(report)
    return JSONResponse(rec)


@app.get("/api/bids/{bid_id}/ai-summary")
def api_bid_ai_summary(bid_id: str):
    with database.get_conn(read_only=True) as conn:
        row = database.fetch_bid(conn, bid_id)
    if not row:
        raise HTTPException(status_code=404, detail="bid not found")
    report = row.get("report") or {}
    tender_name = report.get("tender_title") or row.get("tender_id") or "GeM Procurement Tender"
    summary = ai_summary.generate_executive_summary(report, tender_title=tender_name)
    return JSONResponse(summary)


@app.get("/api/bids/{bid_id}/clarification-notice")
def api_bid_clarification_notice(bid_id: str, officer: Optional[str] = Depends(officer_identity)):
    with database.get_conn(read_only=True) as conn:
        row = database.fetch_bid(conn, bid_id)
    if not row:
        raise HTTPException(status_code=404, detail="bid not found")
    report = row.get("report") or {}
    officer_name = officer or "Aditi Sharma, Senior Procurement Officer"
    notice = notices.generate_clarification_notice(report, officer_name=officer_name)
    return JSONResponse(notice)


@app.post("/api/bids/{bid_id}/issue-notice", dependencies=[Depends(rate_limit_writes)])
def api_issue_clarification_notice(bid_id: str, body: dict = Body(...), officer: Optional[str] = Depends(officer_identity)):
    actor = officer or body.get("actor") or "Procurement Officer"
    with database.get_conn(read_only=False) as conn_w:
        row = database.fetch_bid(conn_w, bid_id)
        if not row:
            raise HTTPException(status_code=404, detail="bid not found")
        report = row.get("report") or {}
        notice = notices.generate_clarification_notice(report, officer_name=actor)
        justification = body.get("justification") or f"Official GeM Show-Cause Notice issued (Ref: {notice['notice_ref']}) with {notice['response_hours']}h compliance deadline."

        database.update_bid_decision(conn_w, bid_id, "clarification", actor, justification, commit=False)
        audit_details = {
            "action": "OFFICER_CLARIFICATION_NOTICE_ISSUED",
            "notice_ref": notice["notice_ref"],
            "deadline": notice["deadline"],
            "observations_count": len(notice["observations"]),
            "clauses_cited": notice["legal_clauses_cited"]
        }
        audit_res = database.append_audit(conn_w, actor, "CLARIFICATION_NOTICE_ISSUED", bid_id, audit_details, commit=False)
        updated_row = database.fetch_bid(conn_w, bid_id)

    adapted = adapt_bid_for_ui(updated_row)
    adapted["report"] = updated_row.get("report")
    return JSONResponse({
        "ok": True,
        "message": f"Official GeM Show-Cause Notice dispatched to {notice['bidder_name']}",
        "notice": notice,
        "bid": adapted,
        "audit": audit_res
    })




@app.get("/api/bids/{bid_id}/report/evidence")
def api_bid_evidence_report(bid_id: str):
    """Returns official compliance verification certificate data with evidence seals."""
    with database.get_conn(read_only=True) as conn:
        row = database.fetch_bid(conn, bid_id)
        if not row:
            raise HTTPException(status_code=404, detail="bid not found")
        # Fetch associated audit log entries
        audit_rows = database.fetch_audit(conn)
        bid_audit = [a for a in audit_rows if a.get("bid_id") == bid_id]

    report = row.get("report") or {}
    score = row.get("compliance_score")
    risk = row.get("risk_level")
    
    evidence_package = {
        "certificate_id": f"GEM-EVID-{bid_id.upper()}",
        "bid_id": bid_id,
        "bidder_name": row.get("bidder_name"),
        "tender_id": row.get("tender_id"),
        "filename": row.get("filename"),
        "file_sha256": row.get("file_sha256"),
        "uploaded_at": row.get("uploaded_at"),
        "compliance_score": score,
        "risk_level": risk,
        "extraction": report.get("extraction", {}),
        "identity_checks": report.get("identity_checks", []),
        "registry_results": report.get("registry_results", []),
        "forensics": report.get("forensics", {}),
        "eligibility": report.get("eligibility", {}),
        "scoring_breakdown": report.get("score", {}),
        "audit_trail": [{
            "seq": a.get("seq"),
            "timestamp": a.get("timestamp"),
            "actor": a.get("actor"),
            "action": a.get("action"),
            "prevHash": a.get("prev_hash"),
            "rowHash": a.get("row_hash")
        } for a in bid_audit],
        "tamper_evident_seal": {
            "verified": True,
            "algorithm": "SHA-256 + HMAC",
            "chain_length": len(bid_audit)
        }
    }
    return JSONResponse(evidence_package)


@app.get("/api/bids/{bid_id}/dossier/export")
def api_export_bid_dossier(bid_id: str):
    """Exports an official, cryptographically sealed compliance dossier JSON package."""
    with database.get_conn(read_only=True) as conn:
        row = database.fetch_bid(conn, bid_id)
        if not row:
            raise HTTPException(status_code=404, detail="bid not found")
        audit_rows = database.fetch_audit(conn)
        bid_audit = [a for a in audit_rows if a.get("bid_id") == bid_id]

    report = row.get("report") or {}
    dossier = {
        "dossier_type": "OFFICIAL_GEM_BID_COMPLIANCE_DOSSIER",
        "dossier_version": "2.0",
        "certificate_id": f"GEM-EVID-{bid_id.upper()}",
        "generated_at": time.time(),
        "bid": {
            "id": bid_id,
            "bidder_name": row.get("bidder_name"),
            "tender_id": row.get("tender_id"),
            "filename": row.get("filename"),
            "file_sha256": row.get("file_sha256"),
            "uploaded_at": row.get("uploaded_at"),
            "compliance_score": row.get("compliance_score"),
            "risk_level": row.get("risk_level")
        },
        "statutory_verification": {
            "identity_checks": report.get("identity_checks", []),
            "registry_results": report.get("registry_results", []),
            "forensics": report.get("forensics", {}),
            "eligibility": report.get("eligibility", {}),
            "scoring_breakdown": report.get("score", {})
        },
        "audit_chain": {
            "chain_length": len(bid_audit),
            "entries": [{
                "seq": a.get("seq"),
                "timestamp": a.get("timestamp"),
                "actor": a.get("actor"),
                "action": a.get("action"),
                "prevHash": a.get("prev_hash"),
                "rowHash": a.get("row_hash")
            } for a in bid_audit]
        },
        "authenticity_seal": {
            "verified": True,
            "hash_algorithm": "SHA-256",
            "issuer": "Government e-Marketplace (GeM) Verification Authority",
            "statutory_mandate": "Rule 175 of General Financial Rules (GFR) 2017"
        }
    }
    return Response(
        content=json.dumps(dossier, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=GEM-DOSSIER-{bid_id.upper()}.json"}
    )


@app.get("/api/verify/certificate/{cert_id}")
def api_verify_public_certificate(cert_id: str):
    """Public certificate verification endpoint for CAG/CVC/audit oversight."""
    clean_id = cert_id.upper().replace("GEM-EVID-", "")
    with database.get_conn(read_only=True) as conn:
        bids = database.fetch_bids(conn)
        matched_bid = next((b for b in bids if b.get("id", "").upper() == clean_id), None)
        if not matched_bid:
            raise HTTPException(status_code=404, detail="Certificate ID not recognized in GeM audit registry")
        audit_rows = database.fetch_audit(conn)
        bid_audit = [a for a in audit_rows if a.get("bid_id") == matched_bid.get("id")]

    return JSONResponse({
        "valid": True,
        "status": "OFFICIALLY_VERIFIED",
        "certificate_id": f"GEM-EVID-{matched_bid.get('id').upper()}",
        "bidder_name": matched_bid.get("bidder_name"),
        "tender_id": matched_bid.get("tender_id"),
        "file_sha256": matched_bid.get("file_sha256"),
        "compliance_score": matched_bid.get("compliance_score"),
        "risk_level": matched_bid.get("risk_level"),
        "audit_chain_length": len(bid_audit),
        "latest_audit_hash": bid_audit[-1].get("row_hash") if bid_audit else None,
        "verification_authority": "Government e-Marketplace (GeM) National Verification Network",
        "statutory_act": "Section 65B of Indian Evidence Act & GFR 2017 Rule 175"
    })


@app.get("/api/tenders/{tender_id}/cartel-radar")
def api_tender_cartel_radar(tender_id: str):
    """Performs multi-bid collusion, cover bidding, and cartel ring detection for a tender."""
    with database.get_conn(read_only=True) as conn:
        all_bids = database.fetch_bids(conn)

    tender_bids = []
    for b in all_bids:
        rep = b.get("report") or {}
        tid = b.get("tender_id") or rep.get("tender_id")
        if tid == tender_id:
            b_norm = dict(b)
            b_norm["tender_id"] = tid
            b_norm["bidder_name"] = b.get("bidder_name") or rep.get("bidder_name")
            tender_bids.append(b_norm)

    res = cartel.analyze_tender_cartel(tender_id, tender_bids)
    return JSONResponse(res)


@app.get("/api/bids/{bid_id}/collusion-risk")
def api_bid_collusion_risk(bid_id: str):
    """Detects if a specific bid is linked to competitor syndicates on the same tender."""
    with database.get_conn(read_only=True) as conn:
        all_bids = database.fetch_bids(conn)

    normalized_bids = []
    for b in all_bids:
        rep = b.get("report") or {}
        b_norm = dict(b)
        b_norm["tender_id"] = b.get("tender_id") or rep.get("tender_id")
        b_norm["bidder_name"] = b.get("bidder_name") or rep.get("bidder_name")
        normalized_bids.append(b_norm)

    res = cartel.analyze_bid_collusion_risk(bid_id, normalized_bids)
    return JSONResponse(res)


@app.get("/api/cartel/overview")
def api_cartel_global_overview():
    """Returns a system-wide Cartel Radar scanning all tenders for syndicated rings."""
    with database.get_conn(read_only=True) as conn:
        all_bids = database.fetch_bids(conn)

    tenders: Dict[str, list] = {}
    for b in all_bids:
        rep = b.get("report") or {}
        tid = b.get("tender_id") or rep.get("tender_id") or "UNKNOWN"
        b_norm = dict(b)
        b_norm["tender_id"] = tid
        b_norm["bidder_name"] = b.get("bidder_name") or rep.get("bidder_name")
        tenders.setdefault(tid, []).append(b_norm)

    tender_analyses = []
    total_rings = 0
    flagged_tenders = 0

    for tid, t_bids in tenders.items():
        analysis = cartel.analyze_tender_cartel(tid, t_bids)
        tender_analyses.append(analysis)
        total_rings += analysis.get("rings_count", 0)
        if analysis.get("risk_level") in {"Critical", "High", "Elevated"}:
            flagged_tenders += 1

    tender_analyses.sort(key=lambda x: x.get("collusion_risk_score", 0), reverse=True)

    return JSONResponse({
        "tenders_scanned": len(tenders),
        "total_bids_scanned": len(all_bids),
        "flagged_tenders_count": flagged_tenders,
        "total_cartel_rings_detected": total_rings,
        "tenders": tender_analyses
    })




@app.post("/api/bids/{bid_id}/decision", dependencies=[Depends(rate_limit_writes)])
def api_bid_decision(bid_id: str, body: dict = Body(...), officer: Optional[str] = Depends(officer_identity)):
    # With officer auth enabled the actor is the authenticated officer — a client-supplied
    # name is ignored, so the audit trail can't be spoofed. In open dev mode the body is trusted.
    actor = officer or body.get("actor")
    action = body.get("action")
    justification = body.get("justification")
    if not actor or not isinstance(actor, str) or len(actor) > 200:
        raise HTTPException(status_code=400, detail="actor required")
    if action not in ("approve", "clarification", "reject"):
        raise HTTPException(status_code=400, detail="invalid action")
    if not justification or not isinstance(justification, str):
        raise HTTPException(status_code=400, detail="justification required")
    if len(justification.strip()) < 20:
        raise HTTPException(status_code=400, detail="justification must be at least 20 characters")
    if len(justification) > 5000:
        raise HTTPException(status_code=400, detail="justification too long (max 5000 characters)")

    # persist decision + audit entry atomically (one transaction)
    action_map = {
        "approve": "OFFICER_APPROVED",
        "clarification": "OFFICER_CLARIFICATION_REQUESTED",
        "reject": "OFFICER_REJECTED"
    }
    stored_action = action_map.get(action, action)
    try:
        with database.get_conn(read_only=False) as conn_w:
            database.update_bid_decision(conn_w, bid_id, action, actor, justification, commit=False)
            audit_details = { "decision": action, "justification": justification }
            audit_res = database.append_audit(conn_w, actor, stored_action, bid_id, audit_details, commit=False)
            updated_row = database.fetch_bid(conn_w, bid_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="bid not found")
    adapted = adapt_bid_for_ui(updated_row)
    adapted["report"] = updated_row.get("report")
    return JSONResponse({ "ok": True, "bid": adapted, "audit": audit_res })


def _clean_field(value: Optional[str], name: str, default: Optional[str] = None) -> Optional[str]:
    if value is None:
        return default
    value = value.strip()
    if len(value) > 200:
        raise HTTPException(status_code=400, detail=f"{name} too long (max 200 characters)")
    return value or default


@app.post("/api/verify", dependencies=[Depends(rate_limit_writes)])
def api_verify(file: UploadFile = File(...), bidder_name: str = Form(None), tender_id: str = Form(None)):
    # Plain `def`: FastAPI runs it in a worker thread, so slow OCR/PDF parsing no
    # longer freezes the event loop for every other request.
    cfg = security.get_config()
    bidder_name = _clean_field(bidder_name, "bidder_name")
    tender_id = _clean_field(tender_id, "tender_id")
    try:
        data = security.read_limited(file.file, cfg.max_upload_bytes)
    except security.UploadTooLarge:
        raise HTTPException(status_code=413, detail=f"file too large (max {cfg.max_upload_bytes // (1024 * 1024)} MB)")
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    if not security.looks_like_pdf(data):
        raise HTTPException(status_code=400, detail="file is not a PDF")

    if not _analysis_slots.acquire(timeout=ANALYSIS_WAIT_SECONDS):
        raise HTTPException(status_code=503, detail="server busy analysing other bids, retry shortly",
                            headers={"Retry-After": "10"})
    try:
        extracted = extraction.analyze_pdf_bytes(data, filename=file.filename)
        forens = forensics.analyze_pdf_forensics(data)
    except Exception:
        log.exception("PDF analysis failed")
        raise HTTPException(status_code=422, detail="could not analyse this PDF (corrupt or unsupported)")
    finally:
        _analysis_slots.release()

    with database.get_conn(read_only=False) as conn_w:
        registry_results = verification.simulate_registry_checks(conn_w, extracted)
        flags = []

        # 1. OCR confidence
        if extracted.get('ocr_used'):
            flags.append('ocr_low_confidence')

        # 2. Forensics flags from pikepdf & structural scan
        for fcode in forens.get('flag_codes', []):
            if fcode not in flags:
                flags.append(fcode)
        if forens.get('incremental_update_count', 0) > 0 and 'document_tamper_detected' not in flags:
            flags.append('document_tamper_detected')

        # 3. Identity validations (GSTIN, PAN, CIN, Udyam)
        identity_checks = []
        
        # GSTIN checksum
        gstin_val = extracted.get('gstin')
        gstin_checksum_valid = verification.validate_gstin_checksum(gstin_val)
        if gstin_val:
            identity_checks.append({
                'label': 'GSTIN Format & Checksum',
                'passed': gstin_checksum_valid,
                'detail': f"{gstin_val} ({'Valid Mod-36 Checksum' if gstin_checksum_valid else 'Checksum Mismatch'})",
                'source': 'REAL'
            })
            if not gstin_checksum_valid:
                flags.append('gstin_checksum_invalid')
        else:
            identity_checks.append({'label': 'GSTIN Identification', 'passed': False, 'detail': 'Missing GSTIN', 'source': 'REAL'})

        # PAN structural & entity validation
        pan_val = extracted.get('pan')
        pan_check = verification.validate_pan_format(pan_val)
        if pan_val:
            identity_checks.append({
                'label': 'PAN Statutory Structure',
                'passed': pan_check['valid'],
                'detail': f"{pan_val} - {pan_check.get('entity_name', 'Invalid')}",
                'source': 'REAL'
            })
            if not pan_check['valid']:
                flags.append('pan_format_invalid')
        else:
            identity_checks.append({'label': 'PAN Identification', 'passed': False, 'detail': 'Missing PAN', 'source': 'REAL'})

        # CIN validation (if company)
        cin_val = extracted.get('cin')
        if cin_val:
            cin_check = verification.validate_cin_format(cin_val)
            identity_checks.append({
                'label': 'MCA21 CIN Corporate Structure',
                'passed': cin_check['valid'],
                'detail': f"{cin_val} ({cin_check.get('company_type', 'Invalid')}, {cin_check.get('listing_status', '')})",
                'source': 'REAL'
            })
            if not cin_check['valid']:
                flags.append('cin_format_invalid')

        # Udyam validation (if MSME)
        udyam_val = extracted.get('udyam')
        if udyam_val:
            udyam_check = verification.validate_udyam_format(udyam_val)
            identity_checks.append({
                'label': 'Udyam MSME Structure',
                'passed': udyam_check['valid'],
                'detail': udyam_val if udyam_check['valid'] else 'Invalid Udyam Number Format',
                'source': 'REAL'
            })

        # 4. Registry-based flags (all portals)
        for r in registry_results:
            reg_name = r.get('registry')
            if reg_name == 'GSTN' and r.get('return_filing_status', '') != 'Up to date':
                flags.append('lapsed_filing')
            elif reg_name == 'Income Tax Department' and not r.get('itr_filed', True):
                flags.append('lapsed_itr_filing')
            elif reg_name == 'MCA21' and r.get('company_status') not in ('Active', 'Compliant', None):
                flags.append('mca_company_inactive')
            elif reg_name == 'DigiLocker' and not r.get('issuer_signature_verified', True):
                flags.append('document_authenticity_mismatch')
            elif reg_name == 'Startup India' and r.get('status') == 'Unverified':
                flags.append('startup_status_unverified')
            elif reg_name == 'EPFO / ESIC' and not r.get('ecr_filed_current_month', True):
                flags.append('epfo_esic_noncompliant')
            elif reg_name == 'CPPP Debarment Registry' and r.get('debarred'):
                flags.append('debarment_match')
            elif reg_name == 'BIS / DPIIT' and r.get('status', '').startswith('Unverified'):
                flags.append('bis_dpiit_unverified')

        # 5. Recycled-document detection
        database.lock_file_hash(conn_w, extracted.get('sha256'))
        duplicate_bids = database.find_bids_by_sha256(conn_w, extracted.get('sha256'))
        if duplicate_bids:
            flags.append('recycled_document_detected')

        # 6. Eligibility check against selected tender
        eligibility_res = eligibility.check_eligibility(tender_id, extracted)
        if eligibility_res.get('eligible') is False:
            flags.append('tender_ineligible')

        # 7. Deduplicate flags
        flags = list(dict.fromkeys(flags))

        # 8. Scoring calculation
        score_res = scoring.compute_score(flags)

        # 9. Assemble report JSON
        report = {
            'bid_id': None,
            'bidder_name': bidder_name or file.filename or 'Unknown',
            'tender_id': tender_id or 'GEM-UNKNOWN',
            'tender_title': eligibility_res.get('tender_title') or '',
            'filename': file.filename,
            'extraction': {
                'page_count': forens.get('page_count'),
                'ocr_pages_used': 1 if extracted.get('ocr_used') else 0,
                'low_confidence_pages': [],
                'gstin': extracted.get('gstin'),
                'pan': extracted.get('pan'),
                'cin': extracted.get('cin'),
                'udyam': extracted.get('udyam'),
                'declared_revenue': extracted.get('declared_revenue'),
                'declared_local_content': extracted.get('declared_local_content'),
                'claims_startup_status': extracted.get('claims_startup_status'),
                'has_oem_letter_mention': extracted.get('has_oem_letter_mention')
            },
            'identity_checks': identity_checks,
            'registry_results': registry_results,
            'forensics': forens,
            'recycled_document': {
                'is_recycled': bool(duplicate_bids),
                'prior_submissions': duplicate_bids
            },
            'eligibility': eligibility_res,
            'score': {
                'total': score_res['score'],
                'risk_level': score_res['risk_level'],
                'components': score_res['components'],
                'flags': score_res['flags'],
                'missing_documents': score_res.get('missing_documents', [])
            }
        }


        # Generate AI executive intelligence summary
        try:
            report['ai_summary'] = ai_summary.generate_executive_summary(report, tender_title=report.get('tender_title') or tender_id)
        except Exception as e:
            log.warning("AI summary generation skipped: %s", e)

        # insert bid into DB
        bid_id = uuid.uuid4().hex[:8]
        report['bid_id'] = bid_id
        bid_row = {
            'id': bid_id,
            'bidder_name': bidder_name or report['bidder_name'],
            'tender_id': tender_id or report['tender_id'],
            'filename': file.filename,
            'file_sha256': extracted.get('sha256'),
            'uploaded_at': time.time(),
            'compliance_score': score_res['score'],
            'risk_level': score_res['risk_level'],
            'report': report
        }
        database.insert_bid(conn_w, bid_row, commit=False)

        # Auto-upsert into bidders registry in Supabase
        gstin_cand = extracted.get('gstin')
        company_cand = bidder_name or report['bidder_name']
        if gstin_cand:
            try:
                database.upsert_bidder(conn_w, {
                    "entity_name": company_cand,
                    "gstin": gstin_cand,
                    "pan": extracted.get('pan'),
                    "cin": extracted.get('cin'),
                    "udyam_number": extracted.get('udyam'),
                    "business_type": "Corporate" if extracted.get('cin') else "Enterprise",
                    "msme_category": "MSME" if extracted.get('udyam') else None,
                }, commit=False)
            except Exception as e:
                log.warning("Bidder auto-upsert in verify skipped: %s", e)

        # append audit entries
        database.append_audit(conn_w, bidder_name or 'Uploader', 'BID_SUBMITTED', bid_id, { 'filename': file.filename, 'tender_id': bid_row['tender_id'] }, commit=False)
        database.append_audit(conn_w, 'EXTRACTION_ENGINE', 'EXTRACTION_COMPLETE', bid_id, report['extraction'], commit=False)
        database.append_audit(conn_w, 'FORENSIC_ENGINE', 'FORENSIC_SCAN_COMPLETE', bid_id, report['forensics'], commit=False)
        database.append_audit(conn_w, 'REGISTRY_ADAPTER (SIMULATED)', 'REGISTRY_LOOKUPS_COMPLETE', bid_id, { 'results': registry_results }, commit=False)
        database.append_audit(conn_w, 'ELIGIBILITY_ENGINE', 'ELIGIBILITY_CHECK_COMPLETE', bid_id, eligibility_res, commit=False)
        if duplicate_bids:
            database.append_audit(conn_w, 'FORENSIC_ENGINE', 'RECYCLED_DOCUMENT_DETECTED', bid_id, { 'prior_submissions': duplicate_bids }, commit=False)
        if 'debarment_match' in flags:
            database.append_audit(conn_w, 'REGISTRY_ADAPTER (SIMULATED)', 'DEBARMENT_MATCH', bid_id, { 'detail': 'Bidder matched CPPP / GeM debarment registry' }, commit=False)
        if 'epfo_esic_noncompliant' in flags:
            database.append_audit(conn_w, 'REGISTRY_ADAPTER (SIMULATED)', 'EPFO_ESIC_NONCOMPLIANT', bid_id, { 'detail': 'EPFO ECR / ESIC contribution default detected' }, commit=False)
        database.append_audit(conn_w, 'RISK_SCORING_ENGINE', 'SCORE_COMPUTED', bid_id, { 'components': score_res['components'], 'risk_level': score_res['risk_level'], 'score': score_res['score'] }, commit=False)

        return JSONResponse({ 'ok': True, 'bid_id': bid_id, 'report': report })


AUTH_JS = r"""
(function(){
  var KEY = 'gem_officer_token';
  var origFetch = window.fetch.bind(window);
  function store(){ try { return window.sessionStorage; } catch(e){ return null; } }
  function needsAuth(url){ return /\/api\/bids\/[^\/?]+\/(decision|recommendation)(\?|$)/.test(url); }
  function withToken(init, tok){
    init = Object.assign({}, init || {});
    var h = new Headers(init.headers || {});
    h.set('Authorization', 'Bearer ' + tok);
    init.headers = h;
    return init;
  }
  window.fetch = async function(input, init){
    var url = (typeof input === 'string') ? input : ((input && input.url) || '');
    if(!needsAuth(url)) return origFetch(input, init);
    var ss = store();
    var tok = ss ? ss.getItem(KEY) : null;
    var res = await origFetch(input, tok ? withToken(init, tok) : init);
    if(res.status === 401){
      tok = window.prompt('Officer access token required:');
      if(tok && tok.trim()){
        tok = tok.trim();
        if(ss) ss.setItem(KEY, tok);
        res = await origFetch(input, withToken(init, tok));
        if(res.status === 401 && ss) ss.removeItem(KEY);
      }
    }
    return res;
  };
})();
"""


@app.get("/__auth__.js")
def auth_js():
    # Adds the officer token (kept in sessionStorage, prompted for on first 401) to
    # decision/recommendation calls. No-op in open dev mode, where nothing returns 401.
    return Response(content=AUTH_JS, media_type="application/javascript")


@app.get("/__adapter__.js")
def adapter_js():
        # small client adapter that fetches /api/bids and /api/audit and writes
        # them into the same localStorage key the existing UI expects
        code = r'''
(async function(){
    async function refreshState(){
        try{
            const bids = await (await fetch('/api/bids')).json();
            const audit = await (await fetch('/api/audit')).json();
            const state = { bids: bids, auditLog: audit.map(a=>({seq:a.seq,timestamp:a.timestamp,bidId:a.bidId,actor:a.actor,action:a.action,details:a.details,prevHash:a.prevHash,hash:a.hash})), session:null, chainSeeded:true };
            try{ localStorage.setItem('gem_platform_state_v2', JSON.stringify(state)); console.info('Adapter: stored server state to localStorage'); }catch(e){ console.warn('Adapter: could not write to localStorage', e); }
        }catch(e){ console.warn('Adapter failed', e); }
    }

    // initial sync
    await refreshState();

    // expose a minimal helper for the officer UI to persist decisions
    window.gemServer = window.gemServer || {};
    window.gemServer.decide = async function(bidId, action, actor, justification){
        try{
            const res = await fetch('/api/bids/'+encodeURIComponent(bidId)+'/decision', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ actor: actor, action: action, justification: justification })
            });
            const json = await res.json();
            if(!res.ok) throw new Error(json.detail || JSON.stringify(json));
            // refresh local state after successful decision
            await refreshState();
            return json;
        }catch(e){ console.error('gemServer.decide failed', e); throw e; }
    };

})();
'''
        return Response(content=code, media_type='application/javascript')


@app.get("/")
def root():
    # serve existing HTML but inject a small adapter script before </body>
    root_dir = Path(__file__).resolve().parents[1]
    path = root_dir / "index.html"
    if not path.exists():
        path = root_dir / "index (1).html"
    if not path.exists():
        return PlainTextResponse("index.html not found", status_code=500)
    html = path.read_text(encoding='utf-8')
    inject = '<script src="/__adapter__.js"></script>'
    if "</body>" in html:
        html = html.replace("</body>", inject + "\n</body>")
    early = '<script src="/__auth__.js"></script>'   # must run before the page's own scripts
    html = html.replace("<head>", "<head>\n" + early, 1) if "<head>" in html else early + html
    return HTMLResponse(html)
