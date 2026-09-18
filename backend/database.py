import sqlite3
import json
import hashlib
from typing import Any, Dict, List, Optional, Tuple


DB_PATH = "./gem_compliance.db"


def connect(read_only: bool = True) -> sqlite3.Connection:
    uri = DB_PATH
    if read_only:
        # open read-only
        uri = f"file:{DB_PATH}?mode=ro"
        return sqlite3.connect(uri, uri=True, check_same_thread=False)
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def fetch_bids(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT id, bidder_name, tender_id, filename, file_sha256, uploaded_at, compliance_score, risk_level, report_json FROM bids ORDER BY uploaded_at DESC")
    rows = cur.fetchall()
    out = []
    for r in rows:
        d = dict(zip([c[0] for c in cur.description], r))
        # try parse report_json
        try:
            d["report"] = json.loads(d.get("report_json") or "null")
        except Exception:
            d["report"] = d.get("report_json")
        out.append(d)
    return out


def fetch_bid(conn: sqlite3.Connection, bid_id: str) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT id, bidder_name, tender_id, filename, file_sha256, uploaded_at, compliance_score, risk_level, report_json FROM bids WHERE id = ?", (bid_id,))
    r = cur.fetchone()
    if not r:
        return None
    d = dict(zip([c[0] for c in cur.description], r))
    try:
        d["report"] = json.loads(d.get("report_json") or "null")
    except Exception:
        d["report"] = d.get("report_json")
    return d


def fetch_audit(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT seq, timestamp, bid_id, actor, action, details, prev_hash, row_hash FROM audit_log ORDER BY seq ASC")
    rows = cur.fetchall()
    out = []
    for r in rows:
        d = dict(zip([c[0] for c in cur.description], r))
        # try parse details
        try:
            d["details_parsed"] = json.loads(d.get("details") or "null")
        except Exception:
            d["details_parsed"] = d.get("details")
        out.append(d)
    return out


def sha256_hex(s: str) -> str:
    h = hashlib.sha256()
    h.update(s.encode("utf-8"))
    return h.hexdigest()


def _json_canonical(obj: Any) -> str:
    # produce JSON similar to JS's JSON.stringify with no extra spaces
    return json.dumps(obj, separators=(',', ':'), ensure_ascii=False)


def verify_audit_chain(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Attempt to reproduce the historical audit row_hashes.

    Tries two likely canonicalizations for the embedded `details` value:
      - details parsed as JSON object (if possible)
      - details left as raw string

    Returns a dict {ok:bool, reason:..., broken_at:? , method:?}
    """
    cur = conn.cursor()
    cur.execute("SELECT seq, timestamp, bid_id, actor, action, details, prev_hash, row_hash FROM audit_log ORDER BY seq ASC")
    rows = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
    if not rows:
        return {"ok": True, "count": 0, "method": None}

    # helper to build payload string using a given choice for details
    def build_payload(row, details_value) -> str:
        payload_obj = {
            "seq": row["seq"],
            "timestamp": row["timestamp"],
            "actor": row["actor"],
            "action": row["action"],
            "bidId": row["bid_id"],
            "details": details_value,
            "prevHash": row["prev_hash"]
        }
        return _json_canonical(payload_obj)

    # Try to find a single method that reproduces all row_hashes
    # Method A: parse details as JSON where possible
    method_a_ok = True
    for row in rows:
        details_raw = row.get("details")
        try:
            details_obj = json.loads(details_raw) if isinstance(details_raw, str) else details_raw
        except Exception:
            details_obj = details_raw
        payload = build_payload(row, details_obj)
        recomputed = sha256_hex(payload)
        if recomputed != (row.get("row_hash") or row.get("rowhash") or ""):
            method_a_ok = False
            break

    if method_a_ok:
        return {"ok": True, "count": len(rows), "method": "details-as-json"}

    # Method B: treat details as raw string (so JSON string value will be escaped)
    method_b_ok = True
    for row in rows:
        details_raw = row.get("details")
        payload = build_payload(row, details_raw)
        recomputed = sha256_hex(payload)
        if recomputed != (row.get("row_hash") or ""):
            method_b_ok = False
            break

    if method_b_ok:
        return {"ok": True, "count": len(rows), "method": "details-as-raw-string"}

    # If neither method reproduces the stored hashes, report first mismatch
    # Find first row where neither method matches and provide diagnostics
    for row in rows:
        details_raw = row.get("details")
        try:
            details_obj = json.loads(details_raw) if isinstance(details_raw, str) else details_raw
        except Exception:
            details_obj = details_raw
        p_a = build_payload(row, details_obj)
        p_b = build_payload(row, details_raw)
        r_a = sha256_hex(p_a)
        r_b = sha256_hex(p_b)
        if r_a != row.get("row_hash") and r_b != row.get("row_hash"):
            return {
                "ok": False,
                "broken_at": row.get("seq"),
                "expected": row.get("row_hash"),
                "recomputed_a": r_a,
                "recomputed_b": r_b,
                "payload_a": p_a,
                "payload_b": p_b,
                "reason": "unable to match stored row_hash with expected canonicalizations"
            }

    return {"ok": False, "reason": "unknown mismatch"}


def insert_bid(conn: sqlite3.Connection, bid: Dict[str, Any]) -> None:
    cur = conn.cursor()
    # Expect keys: id, bidder_name, tender_id, filename, file_sha256, uploaded_at, compliance_score, risk_level, report_json
    cur.execute(
        "INSERT INTO bids (id, bidder_name, tender_id, filename, file_sha256, uploaded_at, compliance_score, risk_level, report_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            bid.get("id"),
            bid.get("bidder_name"),
            bid.get("tender_id"),
            bid.get("filename"),
            bid.get("file_sha256"),
            bid.get("uploaded_at"),
            bid.get("compliance_score"),
            bid.get("risk_level"),
            json.dumps(bid.get("report"), ensure_ascii=False)
        )
    )
    conn.commit()


def append_audit(conn: sqlite3.Connection, actor: str, action: str, bid_id: str, details: Any) -> Dict[str, Any]:
    cur = conn.cursor()
    # determine prev_hash and next seq
    cur.execute("SELECT seq, row_hash FROM audit_log ORDER BY seq DESC LIMIT 1")
    last = cur.fetchone()
    if last:
        last_seq = last[0]
        prev_hash = last[1]
    else:
        last_seq = 0
        prev_hash = "GENESIS"
    seq = last_seq + 1
    import time
    timestamp = time.time()
    # prepare details as JSON if possible
    try:
        details_payload = details if isinstance(details, (dict, list)) else json.loads(details)
    except Exception:
        details_payload = details
    payload_obj = {
        "seq": seq,
        "timestamp": timestamp,
        "actor": actor,
        "action": action,
        "bidId": bid_id,
        "details": details_payload,
        "prevHash": prev_hash
    }
    payload = json.dumps(payload_obj, separators=(',', ':'), ensure_ascii=False)
    row_hash = sha256_hex(payload)
    # store details as JSON string if dict/list, else raw string
    details_str = json.dumps(details_payload, ensure_ascii=False) if isinstance(details_payload, (dict, list)) else str(details_payload)
    cur.execute(
        "INSERT INTO audit_log (seq, timestamp, bid_id, actor, action, details, prev_hash, row_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (seq, timestamp, bid_id, actor, action, details_str, prev_hash, row_hash)
    )
    conn.commit()
    return {"seq": seq, "timestamp": timestamp, "prev_hash": prev_hash, "row_hash": row_hash}


def update_bid_decision(conn: sqlite3.Connection, bid_id: str, action: str, actor: str, justification: str) -> Dict[str, Any]:
    """Update the bid's report_json with an officer decision and return the updated report object.

    This function preserves existing score/risk fields. It does not modify historical audit rows.
    """
    cur = conn.cursor()
    cur.execute("SELECT report_json FROM bids WHERE id = ?", (bid_id,))
    r = cur.fetchone()
    if not r:
        raise ValueError("bid not found")
    try:
        report = json.loads(r[0]) if r[0] else {}
    except Exception:
        report = {}
    import time
    decision_record = {
        "action": action,
        "actor": actor,
        "justification": justification,
        "timestamp": time.time()
    }
    report["officer_decision"] = decision_record
    # store updated report_json
    cur.execute("UPDATE bids SET report_json = ? WHERE id = ?", (json.dumps(report, ensure_ascii=False), bid_id))
    conn.commit()
    return report
