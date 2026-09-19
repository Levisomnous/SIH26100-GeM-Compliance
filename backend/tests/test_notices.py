from fastapi.testclient import TestClient
from backend.main import app
from backend import notices

client = TestClient(app)


def test_notice_generation_with_statutory_flags():
    sample_report = {
        "bid_id": "TEST-BID-001",
        "tender_id": "GEM-2026-TEST-777",
        "tender_title": "Procurement of High-Capacity Networking Racks",
        "bidder_name": "Apex Infra Networks Pvt Ltd",
        "extraction": {
            "gstin": "07AABCA1234F1Z8",
            "pan": "AABCA1234F"
        },
        "score": {
            "total": 52.0,
            "risk_level": "High",
            "flags": ["lapsed_filing", "document_tamper_detected", "local_content_mismatch"]
        }
    }

    notice = notices.generate_clarification_notice(sample_report, officer_name="Aditi Sharma", response_hours=72)

    assert notice["bid_id"] == "TEST-BID-001"
    assert notice["tender_id"] == "GEM-2026-TEST-777"
    assert "GEM/SCN/2026/GEM-2026-TEST-777/TEST-BID-001" in notice["notice_ref"]
    assert notice["bidder_name"] == "Apex Infra Networks Pvt Ltd"
    assert notice["gstin"] == "07AABCA1234F1Z8"
    assert notice["response_hours"] == 72
    assert len(notice["observations"]) == 3

    # Verify legal clause mappings
    clauses = notice["legal_clauses_cited"]
    assert any("Clause 4.1" in c for c in clauses)  # GST
    assert any("Rule 175" in c or "Integrity Pact" in c for c in clauses)  # Tamper
    assert any("PPP-MII" in c for c in clauses)  # Make in India

    # Verify notice body text contains legal preamble
    assert "WHEREAS the bidder M/s Apex Infra Networks Pvt Ltd" in notice["notice_body"]
    assert "SHOW CAUSE" in notice["notice_body"]
    assert "principles of natural justice" in notice["notice_body"]
    assert notice["issuing_authority"]["officer_name"] == "Aditi Sharma"


def test_notice_generation_clean_fallback():
    clean_report = {
        "bid_id": "CLEAN-BID-100",
        "tender_id": "GEM-2026-CLEAN-001",
        "bidder_name": "Prime Systems Ltd",
        "score": {"total": 95.0, "flags": []}
    }
    notice = notices.generate_clarification_notice(clean_report)
    assert len(notice["observations"]) >= 1
    assert "GeM General Terms and Conditions" in notice["observations"][0]["clause"]


def test_notice_api_endpoints():
    bids_res = client.get("/api/bids")
    assert bids_res.status_code == 200
    bids = bids_res.json()
    assert len(bids) > 0
    test_bid = bids[0]
    test_bid_id = test_bid["id"]

    # Test GET notice
    get_res = client.get(f"/api/bids/{test_bid_id}/clarification-notice")
    assert get_res.status_code == 200
    notice_data = get_res.json()
    assert "notice_ref" in notice_data
    assert "notice_body" in notice_data
    assert "observations" in notice_data
    assert "deadline" in notice_data
    assert "issuing_authority" in notice_data
