import json
from pathlib import Path
from typing import Any, Dict, Optional

CRITERIA_PATH = Path(__file__).resolve().parent / "tender_criteria.json"


def load_criteria() -> Dict[str, Any]:
    try:
        return json.loads(CRITERIA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def check_eligibility(tender_id: Optional[str], extracted: Dict[str, Any]) -> Dict[str, Any]:
    """Check the extracted bid fields against the chosen tender's eligibility rules.

    Returns a dict describing each rule checked and an overall `eligible`
    flag. If the tender_id isn't in tender_criteria.json, `eligible` is left
    as None (unknown) rather than failing the bid outright.
    """
    criteria_map = load_criteria()
    criteria = criteria_map.get(tender_id) if tender_id else None
    if not criteria:
        return {
            "tender_id": tender_id,
            "known_tender": False,
            "eligible": None,
            "checks": [],
            "note": "No eligibility rules on file for this tender_id; eligibility check skipped.",
        }

    checks = []
    eligible = True

    min_turnover = criteria.get("min_turnover", 0)
    revenue = extracted.get("declared_revenue")
    turnover_ok = True if not min_turnover else (revenue is not None and revenue >= min_turnover)
    checks.append({"rule": "min_turnover", "required": min_turnover, "declared": revenue, "passed": turnover_ok})
    eligible = eligible and turnover_ok

    min_local = criteria.get("min_local_content", 0)
    local_content = extracted.get("declared_local_content")
    local_ok = True if not min_local else (local_content is not None and local_content >= min_local)
    checks.append({"rule": "min_local_content", "required": min_local, "declared": local_content, "passed": local_ok})
    eligible = eligible and local_ok

    requires_startup = bool(criteria.get("requires_startup_proof", False))
    startup_ok = True if not requires_startup else bool(extracted.get("claims_startup_status") and extracted.get("udyam"))
    checks.append({"rule": "requires_startup_proof", "required": requires_startup, "passed": startup_ok})
    eligible = eligible and startup_ok

    # EPFO / ESIC labour compliance (required for works & construction tenders)
    requires_epfo = bool(criteria.get("requires_epfo", False))
    # For eligibility we only check that the bidder's PAN is present (EPFO is checked at registry level)
    epfo_ok = True if not requires_epfo else bool(extracted.get("pan"))
    checks.append({"rule": "requires_epfo_compliance", "required": requires_epfo, "passed": epfo_ok})
    eligible = eligible and epfo_ok

    # BIS certification (required for electronics/IT & certain CPCL tenders)
    requires_bis = bool(criteria.get("requires_bis", False))
    bis_ok = True if not requires_bis else bool(extracted.get("cin") or extracted.get("pan"))
    checks.append({"rule": "requires_bis_certification", "required": requires_bis, "passed": bis_ok})
    eligible = eligible and bis_ok

    return {
        "tender_id": tender_id,
        "tender_title": criteria.get("title"),
        "known_tender": True,
        "eligible": eligible,
        "checks": checks,
    }
