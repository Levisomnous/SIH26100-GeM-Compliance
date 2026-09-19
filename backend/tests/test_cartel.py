from fastapi.testclient import TestClient
from backend.main import app
from backend import cartel

client = TestClient(app)


def test_cartel_pair_detection():
    bid1 = {
        "id": "BID-A",
        "bidder_name": "Vantara Industrial Systems Ltd",
        "tender_id": "TENDER-101",
        "file_sha256": "hash_111",
        "uploaded_at": 1710000000.0,
        "compliance_score": 75.0,
        "report": {
            "extraction": {
                "pan": "AABCV1234F",
                "gstin": "07AABCV1234F1Z5",
                "email": "procurement@vantara-infra.in"
            },
            "forensics": {
                "producer": "ModifiedDesignPDFEngine v4.2",
                "incremental_update_count": 2
            }
        }
    }

    bid2 = {
        "id": "BID-B",
        "bidder_name": "Vantara Cyber Infra Pvt Ltd",
        "tender_id": "TENDER-101",
        "file_sha256": "hash_222",
        "uploaded_at": 1710000300.0,  # 5 mins later
        "compliance_score": 60.0,
        "report": {
            "extraction": {
                "pan": "AABCV9999K",
                "gstin": "07AABCV9999K1Z9",
                "email": "tenders@vantara-infra.in"
            },
            "forensics": {
                "producer": "ModifiedDesignPDFEngine v4.2",
                "incremental_update_count": 2
            }
        }
    }

    pair_res = cartel.compare_bid_pair(bid1, bid2)
    assert pair_res is not None
    flags = pair_res["flags"]
    assert "SHARED_PDF_ENGINE_METADATA" in flags
    assert "SAME_CORPORATE_GROUP_PAN_ROOT" in flags
    assert "SHARED_CORPORATE_EMAIL_DOMAIN" in flags
    assert "SYNCHRONIZED_SUBMISSION_TIMING" in flags
    assert pair_res["confidence"] >= 0.8
    assert "Section 3(3)" in pair_res["statutory_clause"]


def test_cartel_tender_analysis():
    bid1 = {
        "id": "BID-A",
        "bidder_name": "Entity A",
        "tender_id": "TENDER-101",
        "uploaded_at": 1710000000.0,
        "report": {"extraction": {"pan": "AABCA1111A"}, "forensics": {"producer": "CommonTool"}}
    }
    bid2 = {
        "id": "BID-B",
        "bidder_name": "Entity B",
        "tender_id": "TENDER-101",
        "uploaded_at": 1710000100.0,
        "report": {"extraction": {"pan": "AABCA2222B"}, "forensics": {"producer": "CommonTool"}}
    }
    bid3 = {
        "id": "BID-C",
        "bidder_name": "Independent Bidder C",
        "tender_id": "TENDER-101",
        "uploaded_at": 1710050000.0,
        "report": {"extraction": {"pan": "ZZZZZ9999Z"}, "forensics": {"producer": "OtherEngine"}}
    }

    res = cartel.analyze_tender_cartel("TENDER-101", [bid1, bid2, bid3])
    assert res["total_bids"] == 3
    assert res["rings_count"] >= 1
    assert res["collusion_risk_score"] > 20
    assert len(res["network_graph"]["nodes"]) == 3
    assert len(res["network_graph"]["links"]) >= 1


def test_cartel_api_endpoints():
    bids_res = client.get("/api/bids")
    assert bids_res.status_code == 200
    bids = bids_res.json()
    assert len(bids) > 0
    test_bid = bids[0]
    test_bid_id = test_bid["id"]
    tender_id = test_bid.get("tenderCategory") or test_bid.get("tender_id") or "CPCL-2026-VALV-089"

    # 1. Tender Cartel Radar
    radar_res = client.get(f"/api/tenders/{tender_id}/cartel-radar")
    assert radar_res.status_code == 200
    data = radar_res.json()
    assert "collusion_risk_score" in data
    assert "risk_level" in data
    assert "network_graph" in data

    # 2. Individual Bid Collusion Risk
    bid_risk_res = client.get(f"/api/bids/{test_bid_id}/collusion-risk")
    assert bid_risk_res.status_code == 200
    risk_data = bid_risk_res.json()
    assert "has_collusion_risk" in risk_data

    # 3. Global Cartel Overview
    overview_res = client.get("/api/cartel/overview")
    assert overview_res.status_code == 200
    ov_data = overview_res.json()
    assert "tenders_scanned" in ov_data
    assert "total_bids_scanned" in ov_data

    # 4. Dossier Export
    dossier_res = client.get(f"/api/bids/{test_bid_id}/dossier/export")
    assert dossier_res.status_code == 200
    assert "attachment; filename=" in dossier_res.headers.get("content-disposition", "")
    dossier_json = dossier_res.json()
    assert dossier_json["dossier_type"] == "OFFICIAL_GEM_BID_COMPLIANCE_DOSSIER"

    # 5. Public Certificate Verification
    cert_id = f"GEM-EVID-{test_bid_id.upper()}"
    cert_res = client.get(f"/api/verify/certificate/{cert_id}")
    assert cert_res.status_code == 200
    cert_data = cert_res.json()
    assert cert_data["valid"] is True
    assert cert_data["status"] == "OFFICIALLY_VERIFIED"
