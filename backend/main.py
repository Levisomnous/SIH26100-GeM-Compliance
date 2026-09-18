from fastapi import FastAPI, Response, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
import os
import json
from pathlib import Path

from . import database
from . import extraction, forensics, verification, scoring, recommendations
import uuid, time
import tempfile
from fastapi import UploadFile, File, Form

app = FastAPI()

# open a read-only connection at startup
@app.on_event("startup")
def startup():
    app.state.conn = database.connect(read_only=True)

@app.on_event("shutdown")
def shutdown():
    try:
        app.state.conn.close()
    except Exception:
        pass


@app.get("/api/health")
def health():
    try:
        cur = app.state.conn.cursor()
        cur.execute("SELECT 1")
        _ = cur.fetchone()
        return {"ok": True, "db": "connected"}
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
def api_bids():
    conn = app.state.conn
    rows = database.fetch_bids(conn)
    adapted = [adapt_bid_for_ui(r) for r in rows]
    return JSONResponse(adapted)


@app.get("/api/bids/{bid_id}")
def api_bid(bid_id: str):
    conn = app.state.conn
    row = database.fetch_bid(conn, bid_id)
    if not row:
        raise HTTPException(status_code=404, detail="bid not found")
    adapted = adapt_bid_for_ui(row)
    # include full report for details
    adapted["report"] = row.get("report")
    return JSONResponse(adapted)


@app.get("/api/audit")
def api_audit():
    conn = app.state.conn
    rows = database.fetch_audit(conn)
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
    return JSONResponse(out)


@app.get("/api/audit/verify")
def api_audit_verify():
    conn = app.state.conn
    result = database.verify_audit_chain(conn)
    return JSONResponse(result)


@app.get("/api/bids/{bid_id}/recommendation")
def api_bid_recommendation(bid_id: str):
    conn = app.state.conn
    row = database.fetch_bid(conn, bid_id)
    if not row:
        raise HTTPException(status_code=404, detail="bid not found")
    report = row.get("report") or {}
    rec = recommendations.recommend(report)
    return JSONResponse(rec)


@app.post("/api/bids/{bid_id}/decision")
async def api_bid_decision(bid_id: str, request: Request):
    body = await request.json()
    actor = body.get("actor")
    action = body.get("action")
    justification = body.get("justification")
    if not actor:
        raise HTTPException(status_code=400, detail="actor required")
    if action not in ("approve", "clarification", "reject"):
        raise HTTPException(status_code=400, detail="invalid action")
    if not justification:
        raise HTTPException(status_code=400, detail="justification required")
    if len(justification.strip()) < 20:
        raise HTTPException(status_code=400, detail="justification must be at least 20 characters")

    # persist decision in DB and append audit entry
    conn_w = database.connect(read_only=False)
    try:
        # map action to stored label
        action_map = {
            "approve": "OFFICER_APPROVED",
            "clarification": "OFFICER_CLARIFICATION_REQUESTED",
            "reject": "OFFICER_REJECTED"
        }
        stored_action = action_map.get(action, action)
        updated_report = database.update_bid_decision(conn_w, bid_id, action, actor, justification)
        audit_details = { "decision": action, "justification": justification }
        audit_res = database.append_audit(conn_w, actor, stored_action, bid_id, audit_details)
        # return updated bid and the audit entry meta
        updated_row = database.fetch_bid(conn_w, bid_id)
        adapted = adapt_bid_for_ui(updated_row)
        adapted["report"] = updated_row.get("report")
        return JSONResponse({ "ok": True, "bid": adapted, "audit": audit_res })
    finally:
        try:
            conn_w.close()
        except Exception:
            pass


@app.post("/api/verify")
async def api_verify(file: UploadFile = File(...), bidder_name: str = Form(None), tender_id: str = Form(None)):
    # read bytes
    data = await file.read()
    # run extraction
    extracted = extraction.analyze_pdf_bytes(data, filename=file.filename)
    # run forensics
    forens = forensics.analyze_pdf_forensics(data)
    # run simulated registry checks using writable connection
    conn_w = database.connect(read_only=False)
    try:
        registry_results = verification.simulate_registry_checks(conn_w, extracted)
        # assemble flags from heuristics
        flags = []
        if extracted.get('ocr_used'):
            flags.append('ocr_low_confidence')
        if forens.get('incremental_update_count', 0) > 0:
            flags.append('document_tamper_detected')
        # incorporate registry-based flags
        for r in registry_results:
            if r.get('registry')=='GSTN' and r.get('return_filing_status','')!='Up to date':
                flags.append('lapsed_filing')
        # scoring
        score_res = scoring.compute_score(flags)

        # build report JSON
        report = {
            'bid_id': None,
            'bidder_name': bidder_name or file.filename or 'Unknown',
            'tender_id': tender_id or 'GEM-UNKNOWN',
            'tender_title': '',
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
                'claims_startup_status': extracted.get('claims_startup_status'),
                'has_oem_letter_mention': extracted.get('has_oem_letter_mention')
            },
            'identity_checks': [
                { 'label':'GSTIN format & checksum', 'passed': True if extracted.get('gstin') else False, 'detail': str(extracted.get('gstin')), 'source':'REAL' },
            ],
            'registry_results': registry_results,
            'forensics': forens,
            'score': {
                'total': score_res['score'],
                'risk_level': score_res['risk_level'],
                'components': score_res['components'],
                'flags': score_res['flags'],
                'missing_documents': score_res.get('missing_documents',[])
            }
        }

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
        database.insert_bid(conn_w, bid_row)
        # append audit entries
        database.append_audit(conn_w, bidder_name or 'Uploader', 'BID_SUBMITTED', bid_id, { 'filename': file.filename, 'tender_id': bid_row['tender_id'] })
        database.append_audit(conn_w, 'EXTRACTION_ENGINE', 'EXTRACTION_COMPLETE', bid_id, report['extraction'])
        database.append_audit(conn_w, 'FORENSIC_ENGINE', 'FORENSIC_SCAN_COMPLETE', bid_id, report['forensics'])
        database.append_audit(conn_w, 'REGISTRY_ADAPTER (SIMULATED)', 'REGISTRY_LOOKUPS_COMPLETE', bid_id, { 'results': registry_results })
        database.append_audit(conn_w, 'RISK_SCORING_ENGINE', 'SCORE_COMPUTED', bid_id, { 'components': score_res['components'], 'risk_level': score_res['risk_level'], 'score': score_res['score'] })

        return JSONResponse({ 'ok': True, 'bid_id': bid_id, 'report': report })
    finally:
        try:
            conn_w.close()
        except Exception:
            pass


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
    path = Path(__file__).resolve().parents[1] / "index (1).html"
    if not path.exists():
        return PlainTextResponse("index (1).html not found", status_code=500)
    html = path.read_text(encoding='utf-8')
    inject = '<script src="/__adapter__.js"></script>'
    if "</body>" in html:
        html = html.replace("</body>", inject + "\n</body>")
    return HTMLResponse(html)
