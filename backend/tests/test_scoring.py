from backend import scoring


def test_no_flags_scores_100_low():
    res = scoring.compute_score([])
    assert res["score"] == 100.0
    assert res["risk_level"] == "Low"


def test_known_flag_deducts_correct_amount():
    res = scoring.compute_score(["ocr_low_confidence"])
    assert res["score"] == 95.0


def test_unknown_flag_is_ignored_not_penalized():
    res = scoring.compute_score(["some_flag_not_in_table"])
    assert res["score"] == 100.0


def test_score_never_goes_below_zero():
    heavy_flags = ["document_tamper_detected", "recycled_document_detected",
                   "gstin_checksum_invalid", "turnover_inflation",
                   "document_authenticity_mismatch"]
    res = scoring.compute_score(heavy_flags)
    assert res["score"] >= 0.0


def test_debarment_match_forces_critical_zero():
    res = scoring.compute_score(["debarment_match", "ocr_low_confidence"])
    assert res["score"] == 0.0
    assert res["risk_level"] == "Critical"


def test_risk_band_thresholds():
    assert scoring.compute_score([])["risk_level"] == "Low"
    # 89 -> just under the Low threshold (90)
    res = scoring.compute_score(["ocr_low_confidence"] * 3)  # 100 - 15 = 85
    assert res["risk_level"] == "Medium"
