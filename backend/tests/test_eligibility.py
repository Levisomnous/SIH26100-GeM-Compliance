from backend import eligibility


def test_unknown_tender_is_skipped_not_failed():
    res = eligibility.check_eligibility("NO-SUCH-TENDER", {})
    assert res["known_tender"] is False
    assert res["eligible"] is None


def test_meets_all_criteria_is_eligible():
    extracted = {"declared_revenue": 6_000_000, "declared_local_content": 55}
    res = eligibility.check_eligibility("GEM-2026-IT-004521", extracted)
    assert res["eligible"] is True


def test_below_min_turnover_is_ineligible():
    extracted = {"declared_revenue": 1_000_000, "declared_local_content": 55}
    res = eligibility.check_eligibility("GEM-2026-IT-004521", extracted)
    assert res["eligible"] is False


def test_startup_reservation_requires_udyam_backing():
    # claims startup status but has no Udyam number to back it
    extracted = {"declared_local_content": 30, "claims_startup_status": True, "udyam": None}
    res = eligibility.check_eligibility("GEM-2026-MSME-011", extracted)
    assert res["eligible"] is False

    extracted["udyam"] = "UDYAM-DL-01-1234567"
    res2 = eligibility.check_eligibility("GEM-2026-MSME-011", extracted)
    assert res2["eligible"] is True
