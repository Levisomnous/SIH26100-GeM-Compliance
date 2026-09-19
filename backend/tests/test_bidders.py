from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_register_bidder_validation():
    # Missing company name
    res = client.post("/api/bidders/register", json={"gstin": "27AAECB1234F1Z5"})
    assert res.status_code == 400

    # Missing GSTIN
    res = client.post("/api/bidders/register", json={"company_name": "Test Company"})
    assert res.status_code == 400

def test_register_and_fetch_bidder():
    bidder_payload = {
        "company_name": "Automated Test Hydraulics Ltd",
        "gstin": "36AAACB1234E1Z9",
        "pan": "AAACB1234E",
        "cin": "U29100TG2018PTC123456",
        "udyam": "UDYAM-TS-01-0012345",
        "state": "Telangana",
        "email": "info@testhydraulics.com"
    }
    res = client.post("/api/bidders/register", json=bidder_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["bidder"]["entity_name"] == "Automated Test Hydraulics Ltd"
    assert data["bidder"]["gstin"] == "36AAACB1234E1Z9"

    # Fetch by GSTIN
    fetch_res = client.get("/api/bidders/36AAACB1234E1Z9")
    assert fetch_res.status_code == 200
    fetched = fetch_res.json()
    assert fetched["entity_name"] == "Automated Test Hydraulics Ltd"
    assert fetched["pan"] == "AAACB1234E"

    # Fetch all bidders
    all_res = client.get("/api/bidders")
    assert all_res.status_code == 200
    all_bidders = all_res.json()
    assert any(b["gstin"] == "36AAACB1234E1Z9" for b in all_bidders)
