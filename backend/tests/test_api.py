import os
import pytest

SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "samples")


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_clean_bid_scores_100_low(client):
    pdf_path = os.path.join(SAMPLES_DIR, "bid_vantara_systems.pdf")
    with open(pdf_path, "rb") as f:
        r = client.post(
            "/api/verify",
            files={"file": ("bid.pdf", f, "application/pdf")},
            data={"bidder_name": "Vantara Systems", "tender_id": "GEM-2026-IT-004521"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["report"]["score"]["risk_level"] == "Low"
    assert body["report"]["eligibility"]["eligible"] is True

    assert len(client.get("/api/bids").json()) == 1
    assert client.get("/api/audit/verify").json()["ok"] is True


def test_flagged_bid_trips_expected_flags(client):
    pdf_path = os.path.join(SAMPLES_DIR, "bid_northstar_traders.pdf")
    with open(pdf_path, "rb") as f:
        r = client.post(
            "/api/verify",
            files={"file": ("bid.pdf", f, "application/pdf")},
            data={"bidder_name": "Northstar Traders", "tender_id": "GEM-2026-MSME-011"},
        )
    flags = r.json()["report"]["score"]["flags"]
    assert "gstin_checksum_invalid" in flags
    assert "document_tamper_detected" in flags


def test_recycled_document_detected_on_second_submission(client):
    pdf_path = os.path.join(SAMPLES_DIR, "bid_vantara_systems.pdf")
    with open(pdf_path, "rb") as f:
        client.post(
            "/api/verify",
            files={"file": ("bid.pdf", f, "application/pdf")},
            data={"bidder_name": "Vantara Systems", "tender_id": "GEM-2026-IT-004521"},
        )
    with open(pdf_path, "rb") as f:
        r2 = client.post(
            "/api/verify",
            files={"file": ("bid.pdf", f, "application/pdf")},
            data={"bidder_name": "A Different Company", "tender_id": "GEM-2026-IT-004521"},
        )
    assert "recycled_document_detected" in r2.json()["report"]["score"]["flags"]


def test_reset_clears_all_bids(client):
    pdf_path = os.path.join(SAMPLES_DIR, "bid_vantara_systems.pdf")
    with open(pdf_path, "rb") as f:
        client.post(
            "/api/verify",
            files={"file": ("bid.pdf", f, "application/pdf")},
            data={"bidder_name": "Vantara Systems", "tender_id": "GEM-2026-IT-004521"},
        )
    assert len(client.get("/api/bids").json()) == 1
    client.delete("/api/reset")
    assert client.get("/api/bids").json() == []
