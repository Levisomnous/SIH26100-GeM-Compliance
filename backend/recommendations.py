from typing import Any, Dict, List, Optional


def _collect_issues(report: Dict[str, Any]) -> Dict[str, List[str]]:
    issues = {"missing": [], "inconsistencies": [], "critical": []}
    score = (report.get("score") or {}).get("total") if isinstance(report.get("score"), dict) else None
    flags = (report.get("score") or {}).get("flags") if isinstance(report.get("score"), dict) else []
    extraction = report.get("extraction") or {}
    registry = report.get("registry_results") or []

    # missing documents
    if extraction.get("gstin") is None and extraction.get("pan") is None and extraction.get("cin") is None:
        issues["missing"].append("no_identifiers_extracted")

    # forensic critical flags
    if forensics := report.get("forensics"):
        if forensics.get("incremental_update_count", 0) > 0:
            issues["critical"].append("forensic_incremental_updates")

    # registry inconsistencies
    # if GSTIN present but GSTN lookup missing or inactive
    gstn = next((r for r in registry if r.get("registry") == "GSTN"), None)
    extracted_gstin = extraction.get("gstin")
    if extracted_gstin and gstn:
        status = gstn.get("status")
        if status != "Active":
            issues["inconsistencies"].append("gstn_return_status_not_active")
    if extracted_gstin and not gstn:
        issues["inconsistencies"].append("gstn_lookup_missing")

    # flags reported by scoring
    CRITICAL_FLAGS = {
        "document_tamper_detected",
        "gstin_checksum_invalid",
        "recycled_document_detected",
        "tender_ineligible",
        "debarment_match",
    }
    for f in flags or []:
        if f in CRITICAL_FLAGS:
            issues["critical"].append(f)
        else:
            issues["inconsistencies"].append(f)

    return issues


def recommend(report: Dict[str, Any], tender_requirements: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Deterministic recommendation engine.

    Inputs: the stored `report` (from bids.report_json) and optional tender requirements.
    Outputs a dict with summary, missing_requirements, inconsistencies, recommended_action, confidence, reasons.
    """
    issues = _collect_issues(report)
    score = (report.get("score") or {}).get("total") if isinstance(report.get("score"), dict) else None
    risk = (report.get("score") or {}).get("risk_level") if isinstance(report.get("score"), dict) else None

    reasons: List[str] = []
    missing = issues.get("missing", [])
    inconsistencies = issues.get("inconsistencies", [])
    critical = issues.get("critical", [])

    # Decision defaults
    action = "CLARIFY"
    confidence = 0.5

    # Critical problems => REJECT recommendation
    if critical:
        action = "REJECT"
        confidence = 0.95
        reasons.append("forensic or critical flags detected: %s" % (",".join(critical)))

    # High score + no issues => APPROVE
    elif (score is not None and score >= 85) and not missing and not inconsistencies:
        action = "APPROVE"
        confidence = 0.9
        reasons.append("high score and no reported issues")

    # Moderate score but with inconsistencies or missing docs => CLARIFY
    elif score is not None and 50 <= score < 85 and (missing or inconsistencies):
        action = "CLARIFY"
        confidence = 0.6
        reasons.append("moderate score with inconsistencies or missing documents")

    # Low score => REJECT-leaning
    elif score is not None and score < 50:
        action = "REJECT"
        confidence = 0.85
        reasons.append("low compliance score")

    # If no numeric score available, base on extracted identifiers and registry
    elif score is None:
        if not missing and not inconsistencies:
            action = "APPROVE"
            confidence = 0.7
            reasons.append("no explicit score but identifiers present and no inconsistencies")
        else:
            action = "CLARIFY"
            confidence = 0.5
            reasons.append("insufficient information to auto-approve")

    # Add more granular reasons
    if missing:
        reasons.append("missing identifiers or documents: %s" % (",".join(missing)))
    if inconsistencies:
        reasons.append("inconsistencies detected: %s" % (",".join(inconsistencies)))

    summary_parts = []
    if score is not None:
        summary_parts.append(f"score={score}")
    if risk:
        summary_parts.append(f"risk={risk}")
    if missing:
        summary_parts.append(f"missing={len(missing)}")
    if inconsistencies:
        summary_parts.append(f"issues={len(inconsistencies)}")
    summary = ", ".join(summary_parts) or "no summary"

    return {
        "summary": summary,
        "missing_requirements": missing,
        "inconsistencies": inconsistencies,
        "recommended_action": action,
        "confidence": round(float(confidence), 2),
        "reasons": reasons,
    }
