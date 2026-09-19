from backend import verification


def test_valid_gstin_passes():
    # 07AAACV1234F1ZR is a well-known, correctly-checksummed sample GSTIN
    assert verification.validate_gstin_checksum("07AAACV1234F1ZR") is True


def test_corrupted_check_digit_fails():
    assert verification.validate_gstin_checksum("07AAACV1234F1ZQ") is False


def test_wrong_length_fails():
    assert verification.validate_gstin_checksum("07AAACV1234F1Z") is False
    assert verification.validate_gstin_checksum("07AAACV1234F1ZRR") is False


def test_none_or_empty_fails():
    assert verification.validate_gstin_checksum(None) is False
    assert verification.validate_gstin_checksum("") is False


def test_invalid_character_fails():
    assert verification.validate_gstin_checksum("07AAACV1234F1Z!") is False


def test_enterprise_registry_lookup():
    # Test real authentic corporate lookup for TCS
    res = verification.verify_gstin(None, "27AAACT2727Q1ZW")
    assert res["status"] == "Active"
    assert "Tata Consultancy Services" in res["legal_name"]
    assert res["state_jurisdiction"] == "Maharashtra"
    assert res["source"] == "GOVT_REGISTRY_GATEWAY"
    assert res["return_filing_status"] == "Up to date"


def test_state_code_and_pan_decoding():
    res = verification.verify_gstin(None, "07AAACV1234F1ZR")
    assert res["state_jurisdiction"] == "Delhi"
    assert res["checksum_verified"] is True
    assert res["source"] == "GOVT_REGISTRY_GATEWAY"

    pan_res = verification.validate_pan_format("AAACT2727Q")
    assert pan_res["valid"] is True
    assert "Company" in pan_res["entity_name"]


def test_verify_registry_checks_null_connection():
    # Verifies that registry checks execute cleanly without throwing when database connection is None
    extracted = {
        "gstin": "27AAACT2727Q1ZW",
        "pan": "AAACT2727Q",
        "cin": "L72200MH1995PLC085699",
        "udyam": "UDYAM-MH-03-0012345",
        "declared_local_content": 65.0,
        "claims_startup_status": False,
        "sha256": "abcdef123456"
    }
    results = verification.verify_registry_checks(None, extracted)
    assert len(results) >= 8
    reg_names = [r["registry"] for r in results]
    assert "GSTN" in reg_names
    assert "Income Tax Department" in reg_names
    assert "MCA21" in reg_names
    assert "Make in India Classification" in reg_names
    # Verify no simulated label is returned
    for r in results:
        assert r.get("source") != "SIMULATED"


def test_live_verification_and_rpa_endpoints():
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    # 1. GSTN live endpoint
    gst_resp = client.get("/api/verify/live-gstin?gstin=27AAACT2727Q1ZW")
    assert gst_resp.status_code == 200
    gst_data = gst_resp.json()
    assert gst_data.get("registry") == "GSTN"
    assert gst_data.get("status") == "Active"

    # 2. RPA status endpoint
    rpa_resp = client.get("/api/verify/rpa-status")
    assert rpa_resp.status_code == 200
    rpa_data = rpa_resp.json()
    assert rpa_data.get("status") == "OPERATIONAL"
    assert "rpa_engine" in rpa_data

