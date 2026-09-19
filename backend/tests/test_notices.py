from fastapi.testclient import TestClient
from backend.main import app
from backend import notices

client = TestClient(app)


def test_clarification_notice_generation():
    flagged_report = {
        "bid_id": "TEST-BID-01",
        "bidder_name": "Shivalik Engineering Works",
        "tender_id": "goods-electronics",
        "tender_title": "Goods - Electronics Tender",
        "score": {
            "total": 45,
            "risk_level": "High",
            "flags": [
                "turnover_inflation",
                "lapsed_filing",
                "local_content_mismatch",
                "document_tamper_detected"
            ]
        },
        "extraction": {
            "gstin": "09AACCS5678K1ZR",
            "pan": "AACCS5678K"
        }
    }
    notice = notices.generate_clarification_notice(flagged_report, officer_name="Ramesh Kumar")
    assert notice["bid_id"] == "TEST-BID-01"
    assert "GEM/SCN/2026/goods-electronics/TEST-BID-01" in notice["notice_ref"]
    assert notice["response_hours"] == 72
    assert len(notice["observations"]) == 4
    assert any("GST" in obs["title"] for obs in notice["observations"])
    assert any("General Financial Rules" in clause or "GFR" in clause for clause in notice["legal_clauses_cited"])
    assert "SHOW CAUSE" in notice["notice_body"]
    assert notice["issuing_authority"]["officer_name"] == "Ramesh Kumar"


def test_clarification_notice_api_endpoint():
    # 1. GET with known/seed ID
    res = client.get("/api/bids/seed_shivalik/clarification-notice")
    assert res.status_code == 200
    data = res.json()
    assert "notice_ref" in data
    assert "observations" in data
    assert len(data["observations"]) > 0

    # 2. GET with dynamic unknown ID (must not 404)
    res_dyn = client.get("/api/bids/bid_dyn_12345/clarification-notice")
    assert res_dyn.status_code == 200
    data_dyn = res_dyn.json()
    assert "notice_ref" in data_dyn

    # 3. POST to standalone endpoint with bid payload
    res_post = client.post("/api/clarification-notice", json={
        "bid": {
            "company": "Om Sai Traders",
            "flags": ["debarment_match", "lapsed_itr_filing"]
        }
    })
    assert res_post.status_code == 200
    data_post = res_post.json()
    assert data_post["bidder_name"] == "Om Sai Traders"
    assert any("Debarment" in obs["title"] for obs in data_post["observations"])
