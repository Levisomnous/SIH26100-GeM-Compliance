import os
import json
import time
import hashlib
import logging
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger("gem.database")

DATABASE_URL = os.environ.get("DATABASE_URL")

# Pool sizing / behaviour — all overridable via environment variables.
DB_POOL_MIN = int(os.environ.get("DB_POOL_MIN", "1"))
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "10"))
DB_CONNECT_TIMEOUT = int(os.environ.get("DB_CONNECT_TIMEOUT", "10"))   # seconds per connect attempt
DB_POOL_WAIT = float(os.environ.get("DB_POOL_WAIT", "30"))             # seconds to wait for a free connection
DB_CONNECT_RETRIES = int(os.environ.get("DB_CONNECT_RETRIES", "5"))    # startup retries


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS bids (
        id TEXT PRIMARY KEY,
        bidder_name TEXT,
        tender_id TEXT,
        filename TEXT,
        file_sha256 TEXT,
        uploaded_at DOUBLE PRECISION NOT NULL,
        compliance_score DOUBLE PRECISION NOT NULL,
        risk_level TEXT NOT NULL
            CHECK (risk_level IN ('Low', 'Medium', 'High', 'Critical')),
        report_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        seq INTEGER PRIMARY KEY,
        timestamp DOUBLE PRECISION NOT NULL,
        bid_id TEXT NOT NULL REFERENCES bids(id),
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        details TEXT,
        prev_hash TEXT NOT NULL,
        row_hash TEXT NOT NULL UNIQUE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_bids_sha256 ON bids(file_sha256)",
    "CREATE INDEX IF NOT EXISTS idx_bids_tender ON bids(tender_id)",
    "CREATE INDEX IF NOT EXISTS idx_bids_uploaded_at ON bids(uploaded_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_bid_id ON audit_log(bid_id)",
    # The audit log is append-only at the database level: any UPDATE / DELETE /
    # TRUNCATE fails, so tampering needs a schema change (DROP TRIGGER), not just
    # an UPDATE statement. (DROP TABLE, used by reset_db, is unaffected.)
    """
    CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'audit_log is append-only (% blocked)', TG_OP;
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_audit_no_change ON audit_log",
    """
    CREATE TRIGGER trg_audit_no_change BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()
    """,
    "DROP TRIGGER IF EXISTS trg_audit_no_truncate ON audit_log",
    """
    CREATE TRIGGER trg_audit_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only()
    """,
    """
    CREATE TABLE IF NOT EXISTS bidders (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        profile_id UUID,
        entity_name TEXT NOT NULL,
        entity_type TEXT,
        udyam_number TEXT,
        gstin TEXT UNIQUE,
        pan TEXT,
        cin TEXT,
        email TEXT,
        phone TEXT,
        state TEXT,
        business_type TEXT,
        msme_category TEXT,
        registered_address TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
    )
    """,
    "ALTER TABLE bidders ALTER COLUMN profile_id DROP NOT NULL",
    "ALTER TABLE bidders ADD COLUMN IF NOT EXISTS cin TEXT",
    "ALTER TABLE bidders ADD COLUMN IF NOT EXISTS email TEXT",
    "ALTER TABLE bidders ADD COLUMN IF NOT EXISTS phone TEXT",
    "ALTER TABLE bidders ADD COLUMN IF NOT EXISTS state TEXT",
    "ALTER TABLE bidders ADD COLUMN IF NOT EXISTS business_type TEXT",
    "ALTER TABLE bidders ADD COLUMN IF NOT EXISTS msme_category TEXT",
    "ALTER TABLE bidders ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_bidders_gstin_uniq ON bidders(gstin)",
    "CREATE INDEX IF NOT EXISTS idx_bidders_entity ON bidders(entity_name)",
]

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()
# ThreadedConnectionPool raises immediately when exhausted; this semaphore makes
# callers wait (up to DB_POOL_WAIT) for a free connection instead.
_slots: Optional[threading.BoundedSemaphore] = None
_in_use = 0
_stats_lock = threading.Lock()


def _require_url() -> str:
    url = os.environ.get("DATABASE_URL") or DATABASE_URL
    if not url or "YOUR_HOST" in url or "YOUR_PASSWORD_HERE" in url:
        raise RuntimeError(
            "DATABASE_URL is not set or contains placeholder values. Update .env with your "
            "actual Supabase/Postgres connection string."
        )
    return url




def init_pool() -> None:
    """Create the connection pool (idempotent). Retries with backoff so the app
    survives Postgres still starting up when the API boots."""
    global _pool, _slots
    with _pool_lock:
        if _pool is not None:
            return
        url = _require_url()
        last_err: Optional[Exception] = None
        for attempt in range(1, DB_CONNECT_RETRIES + 1):
            try:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    DB_POOL_MIN, DB_POOL_MAX, url, connect_timeout=DB_CONNECT_TIMEOUT
                )
                _slots = threading.BoundedSemaphore(DB_POOL_MAX)
                return
            except psycopg2.OperationalError as e:
                last_err = e
                log.warning("DB connect attempt %d/%d failed: %s", attempt, DB_CONNECT_RETRIES, e)
                if attempt < DB_CONNECT_RETRIES:
                    time.sleep(min(2 ** (attempt - 1), 8))
        raise RuntimeError(f"Could not connect to Postgres after {DB_CONNECT_RETRIES} attempts: {last_err}")


def close_pool() -> None:
    """Close every pooled connection. Called on app shutdown."""
    global _pool, _slots
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
        _pool = None
        _slots = None


def connect(read_only: bool = True):
    """Check a connection out of the pool. Must be paired with release(conn).
    Prefer the get_conn() context manager, which does that for you.

    read_only=True sets the Postgres session itself to read-only (the server
    rejects writes)."""
    init_pool()
    if not _slots.acquire(timeout=DB_POOL_WAIT):
        raise RuntimeError("Timed out waiting for a free database connection")
    try:
        conn = _pool.getconn()
        try:
            if conn.closed:
                raise psycopg2.InterfaceError("stale pooled connection")
            conn.rollback()
            conn.set_session(readonly=read_only, autocommit=False)
        except Exception:
            _pool.putconn(conn, close=True)
            conn = _pool.getconn()
            conn.set_session(readonly=read_only, autocommit=False)
        global _in_use
        with _stats_lock:
            _in_use += 1
        return conn
    except Exception:
        _slots.release()
        raise


def release(conn, commit: bool = False) -> None:
    """Return a connection to the pool. commit=True commits pending work first;
    otherwise anything uncommitted is rolled back. Broken connections are
    discarded instead of being handed to the next caller."""
    bad = False
    try:
        if not conn.closed:
            if commit:
                conn.commit()
            else:
                conn.rollback()
    except Exception:
        bad = True
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        try:
            if _pool is not None:
                _pool.putconn(conn, close=bad or bool(conn.closed))
            else:
                conn.close()
        finally:
            global _in_use
            with _stats_lock:
                _in_use = max(0, _in_use - 1)
            if _slots is not None:
                try:
                    _slots.release()
                except ValueError:
                    pass


@contextmanager
def get_conn(read_only: bool = True) -> Iterator[Any]:
    """Pooled connection as a context manager.

    read-only: always rolled back on exit (nothing to persist).
    read-write: committed on clean exit, rolled back if the block raises — so a
    multi-step write (insert bid + several audit rows) is all-or-nothing."""
    conn = connect(read_only=read_only)
    try:
        yield conn
    except BaseException:
        release(conn, commit=False)
        raise
    else:
        release(conn, commit=not read_only)


def init_db() -> None:
    """Create the tables if they don't already exist. Safe to call every startup."""
    init_pool()
    with get_conn(read_only=False) as conn:
        cur = conn.cursor()
        # several workers may boot at once; serialize schema setup (DDL is transactional)
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('gem_schema_init'))")
        for stmt in SCHEMA:
            cur.execute(stmt)


def reset_db() -> None:
    """Drop and recreate all tables — wipes all bids and audit history."""
    init_pool()
    with get_conn(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS audit_log")
        cur.execute("DROP TABLE IF EXISTS bids")
    init_db()


def pool_stats() -> Dict[str, Any]:
    return {"min": DB_POOL_MIN, "max": DB_POOL_MAX, "in_use": _in_use, "open": _pool is not None}


def ping() -> bool:
    with get_conn(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        return cur.fetchone()[0] == 1


import decimal

def _normalize_val(v: Any) -> Any:
    if hasattr(v, 'timestamp'):
        return v.timestamp()
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


def _rows_as_dicts(cur) -> List[Dict[str, Any]]:
    cols = [c.name for c in cur.description]
    return [{k: _normalize_val(v) for k, v in zip(cols, r)} for r in cur.fetchall()]



def find_bids_by_sha256(conn, sha256: Optional[str], exclude_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return prior bids that submitted a byte-identical file — used for recycled-document detection."""
    if not sha256:
        return []
    cur = conn.cursor()
    if exclude_id:
        cur.execute("SELECT id, bidder_name, tender_id, uploaded_at FROM bids WHERE file_sha256 = %s AND id != %s", (sha256, exclude_id))
    else:
        cur.execute("SELECT id, bidder_name, tender_id, uploaded_at FROM bids WHERE file_sha256 = %s", (sha256,))
    return _rows_as_dicts(cur)


def count_bids(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM bids")
    return cur.fetchone()[0]


def count_audit(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM audit_log")
    return cur.fetchone()[0]


def lock_file_hash(conn, sha256: Optional[str]) -> None:
    """Serialize concurrent submissions of the same file until this transaction
    ends, so 'check for duplicates, then insert' can't race. Always take this
    lock before the audit-chain lock (consistent order => no deadlock)."""
    if sha256:
        conn.cursor().execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("gem_bid_sha:" + sha256,))


def fetch_bids(conn, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    sql = "SELECT id, bidder_name, tender_id, filename, file_sha256, uploaded_at, compliance_score, risk_level, report_json FROM bids ORDER BY uploaded_at DESC, id"
    if limit is not None:
        cur.execute(sql + " LIMIT %s OFFSET %s", (int(limit), int(offset)))
    else:
        cur.execute(sql)
    out = _rows_as_dicts(cur)
    for d in out:
        try:
            d["report"] = json.loads(d.get("report_json") or "null")
        except Exception:
            d["report"] = d.get("report_json")
    return out


def fetch_bid(conn, bid_id: str) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT id, bidder_name, tender_id, filename, file_sha256, uploaded_at, compliance_score, risk_level, report_json FROM bids WHERE id = %s", (bid_id,))
    rows = _rows_as_dicts(cur)
    if not rows:
        return None
    d = rows[0]
    try:
        d["report"] = json.loads(d.get("report_json") or "null")
    except Exception:
        d["report"] = d.get("report_json")
    return d


def fetch_bids_by_tender(conn, tender_id: str) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    sql = "SELECT id, bidder_name, tender_id, filename, file_sha256, uploaded_at, compliance_score, risk_level, report_json FROM bids WHERE tender_id = %s ORDER BY uploaded_at DESC, id"
    cur.execute(sql, (tender_id,))
    out = _rows_as_dicts(cur)
    for d in out:
        try:
            d["report"] = json.loads(d.get("report_json") or "null")
        except Exception:
            d["report"] = d.get("report_json")
    return out


def fetch_audit(conn, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    sql = "SELECT seq, timestamp, bid_id, actor, action, details, prev_hash, row_hash FROM audit_log ORDER BY seq ASC"
    if limit is not None:
        cur.execute(sql + " LIMIT %s OFFSET %s", (int(limit), int(offset)))
    else:
        cur.execute(sql)
    out = _rows_as_dicts(cur)
    for d in out:
        try:
            d["details_parsed"] = json.loads(d.get("details") or "null")
        except Exception:
            d["details_parsed"] = d.get("details")
    return out


def sha256_hex(s: str) -> str:
    h = hashlib.sha256()
    h.update(s.encode("utf-8"))
    return h.hexdigest()


def _json_canonical(obj: Any) -> str:
    # produce JSON similar to JS's JSON.stringify with no extra spaces
    return json.dumps(obj, separators=(',', ':'), ensure_ascii=False)


def verify_audit_chain(conn) -> Dict[str, Any]:
    """Attempt to reproduce the historical audit row_hashes.

    Tries two likely canonicalizations for the embedded `details` value:
      - details parsed as JSON object (if possible)
      - details left as raw string

    Returns a dict {ok:bool, reason:..., broken_at:? , method:?}
    """
    cur = conn.cursor()
    cur.execute("SELECT seq, timestamp, bid_id, actor, action, details, prev_hash, row_hash FROM audit_log ORDER BY seq ASC")
    rows = _rows_as_dicts(cur)
    if not rows:
        return {"ok": True, "count": 0, "method": None}

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

    method_a_ok = True
    for row in rows:
        details_raw = row.get("details")
        try:
            details_obj = json.loads(details_raw) if isinstance(details_raw, str) else details_raw
        except Exception:
            details_obj = details_raw
        payload = build_payload(row, details_obj)
        recomputed = sha256_hex(payload)
        if recomputed != (row.get("row_hash") or ""):
            method_a_ok = False
            break

    if method_a_ok:
        return {"ok": True, "count": len(rows), "method": "details-as-json"}

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


def insert_bid(conn, bid: Dict[str, Any], commit: bool = True) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO bids (id, bidder_name, tender_id, filename, file_sha256, uploaded_at, compliance_score, risk_level, report_json) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
    if commit:
        conn.commit()


def append_audit(conn, actor: str, action: str, bid_id: str, details: Any, commit: bool = True) -> Dict[str, Any]:
    """Append one row to the hash-chained audit log.

    Uses a Postgres advisory transaction lock so that two concurrent appends
    (e.g. two officers acting at once) can't both read the same "last row"
    and each compute a prevHash that's already stale by the time they insert
    — something the original single-shared-SQLite-connection version was
    exposed to under real concurrency.
    """
    cur = conn.cursor()
    # Serializes only *appends*; released automatically at commit/rollback.
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('gem_audit_log_chain'))")

    cur.execute("SELECT seq, row_hash FROM audit_log ORDER BY seq DESC LIMIT 1")
    last = cur.fetchone()
    if last:
        last_seq, prev_hash = last[0], last[1]
    else:
        last_seq, prev_hash = 0, "GENESIS"
    seq = last_seq + 1
    timestamp = time.time()

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
    details_str = json.dumps(details_payload, ensure_ascii=False) if isinstance(details_payload, (dict, list)) else str(details_payload)

    cur.execute(
        "INSERT INTO audit_log (seq, timestamp, bid_id, actor, action, details, prev_hash, row_hash) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (seq, timestamp, bid_id, actor, action, details_str, prev_hash, row_hash)
    )
    if commit:
        conn.commit()
    return {"seq": seq, "timestamp": timestamp, "prev_hash": prev_hash, "row_hash": row_hash}


def update_bid_decision(conn, bid_id: str, action: str, actor: str, justification: str, commit: bool = True) -> Dict[str, Any]:
    """Update the bid's report_json with an officer decision and return the updated report object."""
    cur = conn.cursor()
    cur.execute("SELECT report_json FROM bids WHERE id = %s", (bid_id,))
    r = cur.fetchone()
    if not r:
        raise ValueError("bid not found")
    try:
        report = json.loads(r[0]) if r[0] else {}
    except Exception:
        report = {}
    decision_record = {
        "action": action,
        "actor": actor,
        "justification": justification,
        "timestamp": time.time()
    }
    report["officer_decision"] = decision_record
    cur.execute("UPDATE bids SET report_json = %s WHERE id = %s", (json.dumps(report, ensure_ascii=False), bid_id))
    if commit:
        conn.commit()
    return report


def upsert_bidder(conn, bidder: Dict[str, Any], commit: bool = True) -> Dict[str, Any]:
    """Insert or update a registered bidder entity in the database."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO bidders (
            id, entity_name, gstin, pan, cin, udyam_number,
            email, phone, state, business_type, msme_category, registered_address
        )
        VALUES (
            gen_random_uuid(), %(entity_name)s, %(gstin)s, %(pan)s, %(cin)s, %(udyam_number)s,
            %(email)s, %(phone)s, %(state)s, %(business_type)s, %(msme_category)s, %(registered_address)s
        )
        ON CONFLICT (gstin) DO UPDATE SET
            entity_name = EXCLUDED.entity_name,
            pan = COALESCE(EXCLUDED.pan, bidders.pan),
            cin = COALESCE(EXCLUDED.cin, bidders.cin),
            udyam_number = COALESCE(EXCLUDED.udyam_number, bidders.udyam_number),
            email = COALESCE(EXCLUDED.email, bidders.email),
            phone = COALESCE(EXCLUDED.phone, bidders.phone),
            state = COALESCE(EXCLUDED.state, bidders.state),
            business_type = COALESCE(EXCLUDED.business_type, bidders.business_type),
            msme_category = COALESCE(EXCLUDED.msme_category, bidders.msme_category),
            registered_address = COALESCE(EXCLUDED.registered_address, bidders.registered_address),
            updated_at = now()
        RETURNING id, entity_name, gstin, pan, cin, udyam_number, email, phone, state, business_type, msme_category, registered_address, created_at, updated_at
        """,
        {
            "entity_name": bidder.get("entity_name") or bidder.get("company_name") or "Unnamed Bidder",
            "gstin": bidder.get("gstin"),
            "pan": bidder.get("pan"),
            "cin": bidder.get("cin"),
            "udyam_number": bidder.get("udyam_number") or bidder.get("udyam"),
            "email": bidder.get("email"),
            "phone": bidder.get("phone"),
            "state": bidder.get("state"),
            "business_type": bidder.get("business_type"),
            "msme_category": bidder.get("msme_category"),
            "registered_address": bidder.get("registered_address") or bidder.get("address")
        }
    )
    cols = [c.name for c in cur.description]
    row = cur.fetchone()
    if commit:
        conn.commit()
    return {k: _normalize_val(v) for k, v in zip(cols, row)}


def fetch_bidders(conn, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    sql = "SELECT id, entity_name, gstin, pan, cin, udyam_number, email, phone, state, business_type, msme_category, registered_address, created_at, updated_at FROM bidders ORDER BY updated_at DESC"
    if limit is not None:
        cur.execute(sql + " LIMIT %s", (int(limit),))
    else:
        cur.execute(sql)
    return _rows_as_dicts(cur)


def fetch_bidder_by_gstin(conn, gstin: str) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        "SELECT id, entity_name, gstin, pan, cin, udyam_number, email, phone, state, business_type, msme_category, registered_address, created_at, updated_at FROM bidders WHERE gstin = %s",
        (gstin,)
    )
    rows = _rows_as_dicts(cur)
    return rows[0] if rows else None

