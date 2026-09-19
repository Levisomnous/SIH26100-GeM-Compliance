import time
import datetime
from typing import Any, Dict, List, Optional


STATUTORY_CLAUSES = {
    "lapsed_filing": {
        "title": "GST Return Filing Non-Compliance (GSTR-3B Default)",
        "clause": "Clause 4.1 of GeM General Terms and Conditions (GTC) & Central Goods and Services Tax Act 2017",
        "remedy": "Submit certified GSTR-3B and GSTR-1 acknowledgement receipts with payment challans for the defaulted tax periods."
    },
    "lapsed_itr_filing": {
        "title": "Income Tax Return (ITR) Compliance Lapse",
        "clause": "Rule 144(xi) of General Financial Rules (GFR) 2017 & Section 139 of the Income Tax Act 1961",
        "remedy": "Furnish authenticated ITR-V filing acknowledgements and audited Annual Financial Accounts for the last 3 financial years."
    },
    "turnover_inflation": {
        "title": "Discrepancy in Declared Annual Turnover",
        "clause": "Technical and Financial Evaluation Criteria, Clause 7.3 & GeM GTC Clause 12",
        "remedy": "Submit a Chartered Accountant (CA) Turnover Certificate bearing a valid Unique Document Identification Number (UDIN)."
    },
    "document_tamper_detected": {
        "title": "Digital Document Forensic Anomaly (Post-Save Incremental Alteration)",
        "clause": "Clause 19 of GeM Integrity Pact & Rule 175 of General Financial Rules (GFR) 2017",
        "remedy": "Submit original, cryptographically untampered source PDF certificates directly downloaded from the issuing statutory repository."
    },
    "editing_software_detected": {
        "title": "Submission of Digitally Manipulated Document (Design Software Metadata Detected)",
        "clause": "Clause 19 of GeM Integrity Pact & Section 65B of Indian Evidence Act",
        "remedy": "Provide explanation regarding editing software metadata and submit original digitally signed PDF issued by competent authority."
    },
    "local_content_mismatch": {
        "title": "Make in India (MII) Local Content Discrepancy",
        "clause": "Department for Promotion of Industry and Internal Trade (DPIIT) PPP-MII Order 2017, Clause 9(a)",
        "remedy": "Furnish an independent statutory auditor / Cost Accountant local content verification certificate specifying domestic value-addition breakdown."
    },
    "epfo_esic_noncompliant": {
        "title": "Statutory Labour Compliance Gap (EPFO / ESIC Default)",
        "clause": "Employees' Provident Funds and Miscellaneous Provisions Act 1952 & ESI Act 1948",
        "remedy": "Provide Electronic Challan cum Return (ECR) payment confirmation receipts and clearance certificate for the active billing cycle."
    },
    "bis_dpiit_unverified": {
        "title": "Unverified BIS / DPIIT Mandatory Quality Standard Certification",
        "clause": "Bureau of Indian Standards Act 2016 & Applicable Ministry Quality Control Orders (QCO)",
        "remedy": "Upload valid Bureau of Indian Standards (BIS) license copy / DPIIT recognition certificate with active QR verification link."
    },
    "debarment_match": {
        "title": "CPPP / GeM Debarment Registry Match",
        "clause": "Rule 151 of General Financial Rules (GFR) 2017 (Debarment from Bidding)",
        "remedy": "Provide legal standing clarification and judicial order / debarment revocation documentation if available."
    },
    "recycled_document_detected": {
        "title": "Duplicate Document Fingerprint Across Unrelated Tenders",
        "clause": "Clause 18 of GeM GTC (Integrity and Code of Conduct)",
        "remedy": "Clarify tender-specific customization and submit tender-referenced undertaking on non-judicial stamp paper."
    },
    "tender_ineligible": {
        "title": "Failure to Meet Minimum Tender Eligibility Thresholds",
        "clause": "Specific Tender Evaluation Matrix & Section 144 of GFR 2017",
        "remedy": "Provide documentary evidence satisfying required minimum experience, turnover, or technical specification."
    }
}

GENERIC_DEFAULT = {
    "title": "Documentary Discrepancy Requiring Officer Clarification",
    "clause": "Clause 4 of GeM General Terms and Conditions (GTC)",
    "remedy": "Provide authenticated documentary clarification addressing the officer observation within the statutory response period."
}


def generate_clarification_notice(
    report: Dict[str, Any],
    officer_name: str = "Aditi Sharma",
    response_hours: int = 72
) -> Dict[str, Any]:
    """Generates an official Government of India / GeM Show-Cause Notice citing
    exact legal procurement clauses based on verified discrepancies."""
    now = datetime.datetime.now(datetime.timezone.utc)
    deadline_dt = now + datetime.timedelta(hours=response_hours)

    bid_id = report.get("bid_id", "BID-UNKNOWN")
    tender_id = report.get("tender_id", "GEM-UNKNOWN")
    bidder_name = report.get("bidder_name", "Prospective Bidder")
    tender_title = report.get("tender_title") or tender_id

    extraction = report.get("extraction") or {}
    score_obj = report.get("score") or {}
    flags = score_obj.get("flags") or []

    # Map flags to specific statutory observations
    observations = []
    seen = set()
    for f in flags:
        if f in STATUTORY_CLAUSES and f not in seen:
            seen.add(f)
            item = dict(STATUTORY_CLAUSES[f])
            item["flag_code"] = f
            observations.append(item)

    if not observations:
        observations.append(GENERIC_DEFAULT)

    notice_ref = f"GEM/SCN/2026/{tender_id}/{bid_id.upper()}"
    issue_date_str = now.strftime("%d-%b-%Y %H:%M UTC")
    deadline_str = deadline_dt.strftime("%d-%b-%Y %H:%M UTC")

    legal_clauses_cited = [obs["clause"] for obs in observations]

    notice_body = (
        f"WHEREAS the bidder M/s {bidder_name} (GSTIN: {extraction.get('gstin', 'N/A')}, "
        f"PAN: {extraction.get('pan', 'N/A')}) has submitted a bid under Tender ID: {tender_id} "
        f"for '{tender_title}'.\n\n"
        f"AND WHEREAS during preliminary integrated compliance verification, forensic scan, "
        f"and statutory registry cross-referencing, the competent authority observed the following discrepancies:\n\n"
    )

    for i, obs in enumerate(observations, start=1):
        notice_body += (
            f"  {i}. {obs['title'].upper()}\n"
            f"     Governing Law / Regulation: {obs['clause']}\n"
            f"     Required Submission: {obs['remedy']}\n\n"
        )

    notice_body += (
        f"NOW THEREFORE, in accordance with the principles of natural justice and GeM GTC Clause 4, "
        f"M/s {bidder_name} is hereby called upon to SHOW CAUSE and upload the aforementioned "
        f"clarifications/remedial documents on the GeM portal within {response_hours} hours of this notice "
        f"(Strict Deadline: {deadline_str}).\n\n"
        f"Please take notice that failure to submit authentic verifiable documentation within the "
        f"prescribed timeline shall result in immediate technical disqualification under GFR Rule 144, "
        f"without further reference."
    )

    return {
        "notice_ref": notice_ref,
        "bid_id": bid_id,
        "tender_id": tender_id,
        "tender_title": tender_title,
        "bidder_name": bidder_name,
        "gstin": extraction.get("gstin", "N/A"),
        "pan": extraction.get("pan", "N/A"),
        "issue_date": issue_date_str,
        "deadline": deadline_str,
        "response_hours": response_hours,
        "observations": observations,
        "legal_clauses_cited": legal_clauses_cited,
        "notice_body": notice_body,
        "issuing_authority": {
            "officer_name": officer_name,
            "designation": "Senior Procurement Officer",
            "department": "GeM Automated Verification & Statutory Compliance Wing",
            "authority": "Government e-Marketplace (GeM), Ministry of Commerce & Industry"
        }
    }
