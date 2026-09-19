import os
import threading
import time

import psycopg2
import pytest
from fastapi.testclient import TestClient

from backend import database, main

SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "samples")
VANTARA = os.path.join(SAMPLES_DIR, "bid_vantara_systems.pdf")
OFFICER_TOKEN = "officer-token-0123456789"
ADMIN_TOKEN = "admin-token-0123456789ab"
GOOD_JUSTIFICATION = "All documents verified against the tender criteria."


def _upload(client, name="Vantara Systems", path=VANTARA):
    with open(path, "rb") as f:
        r = client.post(
            "/api/verify",
            files={"file": ("bid.pdf", f, "application/pdf")},
            data={"bidder_name": name, "tender_id": "GEM-2026-IT-004521"},
        )
    assert r.status_code == 200, r.text
    return r.json()["bid_id"]


# ---------------------------------------------------------------- reset gating
def test_reset_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv("ENABLE_RESET", raising=False)
    _upload(client)
    assert client.delete("/api/reset").status_code == 403
    assert len(client.get("/api/bids").json()) == 1          # nothing was wiped


def test_reset_requires_admin_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("OFFICER_TOKENS", f"asha:{OFFICER_TOKEN}")
    _upload(client)
    assert client.delete("/api/reset").status_code == 401
    officer = {"Authorization": f"Bearer {OFFICER_TOKEN}"}
    assert client.delete("/api/reset", headers=officer).status_code == 401   # officer != admin
    assert len(client.get("/api/bids").json()) == 1
    assert client.delete("/api/reset", headers={"X-API-Key": ADMIN_TOKEN}).status_code == 200
    assert client.get("/api/bids").json() == []


# ------------------------------------------------------------------ officer auth
def test_decision_requires_valid_officer_token_and_uses_token_identity(client, monkeypatch):
    bid_id = _upload(client)
    monkeypatch.setenv("OFFICER_TOKENS", f"asha:{OFFICER_TOKEN}")
    body = {"actor": "someone-else", "action": "approve", "justification": GOOD_JUSTIFICATION}
    url = f"/api/bids/{bid_id}/decision"

    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers={"Authorization": "Bearer wrong-token-000000000"}).status_code == 401
    assert client.get(f"/api/bids/{bid_id}/recommendation").status_code == 401

    r = client.post(url, json=body, headers={"Authorization": f"Bearer {OFFICER_TOKEN}"})
    assert r.status_code == 200, r.text
    last = client.get("/api/audit").json()[-1]
    assert last["action"] == "OFFICER_APPROVED"
    assert last["actor"] == "asha"                 # authenticated identity, not the spoofed body value
    assert client.get("/api/audit/verify").json()["ok"] is True
    ok = client.get(f"/api/bids/{bid_id}/recommendation", headers={"X-API-Key": OFFICER_TOKEN})
    assert ok.status_code == 200


def test_decision_open_in_dev_mode_uses_body_actor(client):
    bid_id = _upload(client)
    r = client.post(f"/api/bids/{bid_id}/decision",
                    json={"actor": "Dev Officer", "action": "reject", "justification": GOOD_JUSTIFICATION})
    assert r.status_code == 200
    assert client.get("/api/audit").json()[-1]["actor"] == "Dev Officer"


def test_decision_validation_and_unknown_bid(client):
    bid_id = _upload(client)
    url = f"/api/bids/{bid_id}/decision"
    assert client.post(url, json={"action": "approve", "justification": GOOD_JUSTIFICATION}).status_code == 400   # no actor
    assert client.post(url, json={"actor": "x", "action": "maybe", "justification": GOOD_JUSTIFICATION}).status_code == 400
    assert client.post(url, json={"actor": "x", "action": "approve", "justification": "too short"}).status_code == 400
    r = client.post("/api/bids/does-not-exist/decision",
                    json={"actor": "x", "action": "approve", "justification": GOOD_JUSTIFICATION})
    assert r.status_code == 404


def test_decision_is_atomic_when_audit_append_fails(client, monkeypatch):
    bid_id = _upload(client)
    audit_before = len(client.get("/api/audit").json())
    real_append, fail = database.append_audit, {"on": True}

    def flaky_append(*a, **k):
        if fail["on"]:
            raise RuntimeError("simulated failure between decision update and audit append")
        return real_append(*a, **k)

    monkeypatch.setattr(database, "append_audit", flaky_append)
    with TestClient(main.app, raise_server_exceptions=False) as c2:
        r = c2.post(f"/api/bids/{bid_id}/decision",
                    json={"actor": "x", "action": "approve", "justification": GOOD_JUSTIFICATION})
        assert r.status_code == 500
        fail["on"] = False
        bid = c2.get(f"/api/bids/{bid_id}").json()
        assert "officer_decision" not in (bid["report"] or {})          # decision update rolled back too
        assert len(c2.get("/api/audit").json()) == audit_before


# -------------------------------------------------------------- upload hardening
def test_upload_rejects_non_pdf_and_empty(client):
    r = client.post("/api/verify", files={"file": ("x.pdf", b"<html>hi</html>", "application/pdf")})
    assert r.status_code == 400 and "PDF" in r.json()["detail"]
    r = client.post("/api/verify", files={"file": ("x.pdf", b"", "application/pdf")})
    assert r.status_code == 400
    assert client.get("/api/bids").json() == []


def test_upload_rejects_oversized_file(client, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "0.001")      # ~1 KB
    with open(VANTARA, "rb") as f:
        r = client.post("/api/verify", files={"file": ("bid.pdf", f, "application/pdf")})
    assert r.status_code == 413
    assert client.get("/api/bids").json() == []


def test_upload_rejects_corrupt_pdf_cleanly(client):
    r = client.post("/api/verify", files={"file": ("bad.pdf", b"%PDF-1.4\nthis is not really a pdf", "application/pdf")})
    assert r.status_code in (200, 422)                 # analysed leniently or rejected — never a 500
    assert r.status_code != 500


def test_write_endpoints_are_rate_limited(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "2")
    main._limiter.reset()
    codes = [client.post("/api/verify", files={"file": ("x.pdf", b"nope", "application/pdf")}).status_code
             for _ in range(3)]
    assert codes[:2] == [400, 400] and codes[2] == 429


# ------------------------------------------------------------- reads & plumbing
def test_pagination_and_total_count_header(client):
    _upload(client, "First")
    with open(os.path.join(SAMPLES_DIR, "bid_northstar_traders.pdf"), "rb") as f:
        client.post("/api/verify", files={"file": ("b.pdf", f, "application/pdf")},
                    data={"bidder_name": "Second", "tender_id": "GEM-2026-MSME-011"})
    r = client.get("/api/bids?limit=1")
    assert r.status_code == 200 and len(r.json()) == 1 and r.headers["X-Total-Count"] == "2"
    assert len(client.get("/api/bids?limit=1&offset=1").json()) == 1
    assert client.get("/api/bids?limit=1&offset=2").json() == []
    assert len(client.get("/api/bids").json()) == 2                     # default: everything
    assert client.get("/api/bids?limit=0").status_code == 422
    audit_total = int(client.get("/api/audit").headers["X-Total-Count"])
    assert len(client.get("/api/audit?limit=3").json()) == 3 and audit_total > 3


def test_health_reports_pool_and_connections_are_returned(client):
    for _ in range(5):
        client.get("/api/bids")
    h = client.get("/api/health").json()
    assert h["ok"] is True and h["pool"]["open"] is True
    assert h["pool"]["in_use"] <= 1                                     # only the health check itself


def test_security_headers_and_auth_script_injected(client):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    page = client.get("/")
    assert page.status_code == 200 and "/__auth__.js" in page.text
    assert client.get("/__auth__.js").status_code == 200


def test_production_refuses_to_start_without_officer_tokens(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("OFFICER_TOKENS", raising=False)
    with pytest.raises(RuntimeError):
        with TestClient(main.app):
            pass


# ------------------------------------------------------------ database-level
def test_audit_log_is_append_only(client):
    _upload(client)
    for sql in ("UPDATE audit_log SET actor = 'attacker'", "DELETE FROM audit_log", "TRUNCATE audit_log"):
        with pytest.raises(psycopg2.Error):
            with database.get_conn(read_only=False) as conn:
                conn.cursor().execute(sql)
    assert client.get("/api/audit/verify").json()["ok"] is True
    assert len(client.get("/api/audit").json()) > 0


def test_schema_rejects_bad_risk_level(db):
    db.init_db()
    row = {"id": "b1", "bidder_name": "x", "tender_id": "t", "filename": "f", "file_sha256": "a" * 64,
           "uploaded_at": 1.0, "compliance_score": 50.0, "risk_level": "Banana", "report": {}}
    with pytest.raises(psycopg2.Error):
        with db.get_conn(read_only=False) as conn:
            db.insert_bid(conn, row, commit=False)


def test_file_hash_lock_serializes_duplicate_check(db):
    """Two simultaneous submissions of the same file must not both see 'no duplicates'."""
    db.init_db()
    sha = "f" * 64
    seen, first_holds_lock = {}, threading.Event()

    def submit(name, hold):
        with db.get_conn(read_only=False) as conn:
            db.lock_file_hash(conn, sha)
            seen[name] = len(db.find_bids_by_sha256(conn, sha))
            if hold:
                first_holds_lock.set()
                time.sleep(0.6)                      # keep the transaction (and lock) open
            db.insert_bid(conn, {"id": f"bid-{name}", "bidder_name": name, "tender_id": "t", "filename": "f.pdf",
                                 "file_sha256": sha, "uploaded_at": time.time(), "compliance_score": 100.0,
                                 "risk_level": "Low", "report": {}}, commit=False)

    t1 = threading.Thread(target=submit, args=("a", True))
    t1.start()
    assert first_holds_lock.wait(10)
    t2 = threading.Thread(target=submit, args=("b", False))
    t2.start()
    t1.join(30)
    t2.join(30)
    assert seen == {"a": 0, "b": 1}


def test_concurrent_audit_appends_keep_chain_valid(db):
    db.init_db()
    with db.get_conn(read_only=False) as conn:
        db.insert_bid(conn, {"id": "b1", "bidder_name": "x", "tender_id": "t", "filename": "f", "file_sha256": "a" * 64,
                             "uploaded_at": 1.0, "compliance_score": 90.0, "risk_level": "Low", "report": {}},
                      commit=False)
    errors = []

    def append(i):
        try:
            with db.get_conn(read_only=False) as conn:
                db.append_audit(conn, f"actor{i}", "TEST", "b1", {"i": i}, commit=False)
        except Exception as e:                       # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=append, args=(i,)) for i in range(12)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert not errors
    with db.get_conn(read_only=True) as conn:
        assert len(db.fetch_audit(conn)) == 12
        assert db.verify_audit_chain(conn)["ok"] is True
