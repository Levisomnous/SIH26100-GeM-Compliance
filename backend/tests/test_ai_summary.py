from fastapi.testclient import TestClient
from backend.main import app
from backend import ai_summary

client = TestClient(app)

def test_ai_summary_clean_bid():
    clean_report = {
        "bidder_name": "Zenith Petrochem Tech Ltd",
        "tender_id": "CPCL-2026-VALV-089",
        "tender_title": "CPCL High-Pressure Valves",
        "score": {"total": 96, "risk_level": "Low", "flags": []},
        "extraction": {
            "gstin": "33AABCA5678D1Z2",
            "pan": "AABCA5678D",
            "cin": "U28910TN2015PTC099881",
            "udyam": "UDYAM-TN-02-0048123",
            "declared_local_content": 65
        },
        "forensics": {"incremental_update_count": 0},
        "registry_results": [{"registry": "GSTN", "status": "Active"}],
        "eligibility": {"eligible": True, "reasons": []}
    }
    summary = ai_summary.generate_executive_summary(clean_report)
    assert summary["verdict"] == "Eligible"
    assert summary["recommended_action"] == "approve"
    assert len(summary["strengths"]) >= 2
    assert len(summary["risk_factors"]) == 0
    assert len(summary["suggested_justification"]) >= 20

def test_ai_summary_debarred_bid():
    debarred_report = {
        "bidder_name": "Shadow Shell Enterprises",
        "tender_id": "GEM-2026-GEN-001",
        "score": {"total": 15, "risk_level": "Critical", "flags": ["debarment_match"]},
        "extraction": {"gstin": "07AAAAA0000A1Z5"},
        "forensics": {"incremental_update_count": 2},
        "registry_results": [{"registry": "CPPP Debarment Registry", "debarred": True}],
        "eligibility": {"eligible": False, "reasons": ["Debarred entity"]}
    }
    summary = ai_summary.generate_executive_summary(debarred_report)
    assert summary["verdict"] == "Disqualified"
    assert summary["recommended_action"] == "reject"
    assert any("Debarment" in r or "CRITICAL" in r for r in summary["risk_factors"])
    assert len(summary["suggested_justification"]) >= 20

def test_ai_summary_api_endpoint():
    bids_res = client.get("/api/bids")
    assert bids_res.status_code == 200
    bids = bids_res.json()
    assert len(bids) > 0
    test_bid_id = bids[0]["id"]

    ai_res = client.get(f"/api/bids/{test_bid_id}/ai-summary")
    assert ai_res.status_code == 200
    data = ai_res.json()
    assert "headline" in data
    assert "verdict" in data
    assert "executive_summary" in data
    assert "recommended_action" in data
    assert "suggested_justification" in data
