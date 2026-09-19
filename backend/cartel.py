"""Cartel & Collusive Bidding Radar (Competition Commission of India / GeM Compliance).

Detects collusive bidding rings, cover bidding, and syndicated submissions under
Section 3(3) of the Competition Act, 2002, Rule 175 of GFR 2017, and GeM GTC Clause 18.
"""

from typing import Any, Dict, List, Optional
import datetime


STATUTORY_CARTEL_ACT = "Section 3(3)(d) of Competition Act 2002 (Bid Rigging & Cover Bidding) & GeM GTC Clause 18"


def extract_email_domain(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    domain = email.split("@")[-1].strip().lower()
    common_public = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com", "nic.in", "gov.in"}
    if domain in common_public:
        return None
    return domain


def compare_bid_pair(bid_a: Dict[str, Any], bid_b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Compares two competing bids submitted for the same tender to identify
    covert linkages, shared metadata fingerprints, or syndicated bidding."""
    if bid_a.get("id") == bid_b.get("id"):
        return None

    rep_a = bid_a.get("report") or {}
    rep_b = bid_b.get("report") or {}

    ext_a = rep_a.get("extraction") or {}
    ext_b = rep_b.get("extraction") or {}

    for_a = rep_a.get("forensics") or {}
    for_b = rep_b.get("forensics") or {}

    flags = []
    evidence_points = []
    confidence_weight = 0.0

    # 1. Exact Duplicate File Hash
    hash_a = bid_a.get("file_sha256") or for_a.get("file_sha256")
    hash_b = bid_b.get("file_sha256") or for_b.get("file_sha256")
    if hash_a and hash_b and hash_a == hash_b:
        flags.append("IDENTICAL_DOCUMENT_SHA256")
        evidence_points.append("Both competing bids submitted identical document byte hashes (recycled document across competitors).")
        confidence_weight += 0.95

    # 2. Forensic Metadata & Software Fingerprint Match
    prod_a = (for_a.get("producer") or "").strip().lower()
    prod_b = (for_b.get("producer") or "").strip().lower()
    if prod_a and prod_b and prod_a == prod_b and prod_a not in {"pdf", "standard", "unknown"}:
        flags.append("SHARED_PDF_ENGINE_METADATA")
        evidence_points.append(f"Identical PDF engine signature detected: '{for_a.get('producer')}'.")
        confidence_weight += 0.40

    # Incremental update pattern match (tamper tool fingerprint)
    upd_a = for_a.get("incremental_update_count", 0)
    upd_b = for_b.get("incremental_update_count", 0)
    if upd_a > 0 and upd_b > 0 and upd_a == upd_b:
        flags.append("MATCHING_INCREMENTAL_UPDATE_FINGERPRINT")
        evidence_points.append(f"Both bids exhibit an identical forensic incremental update pattern ({upd_a} post-creation edits).")
        confidence_weight += 0.35

    # 3. Corporate & Statutory Lineage
    pan_a = (ext_a.get("pan") or bid_a.get("pan") or "").strip().upper()
    pan_b = (ext_b.get("pan") or bid_b.get("pan") or "").strip().upper()
    if pan_a and pan_b:
        if pan_a == pan_b:
            flags.append("COMMON_PAN_IDENTITY")
            evidence_points.append(f"Both entities share identical PAN ({pan_a}) under different bidding names.")
            confidence_weight += 0.90
        elif len(pan_a) >= 5 and len(pan_b) >= 5 and pan_a[:4] == pan_b[:4]:
            flags.append("SAME_CORPORATE_GROUP_PAN_ROOT")
            evidence_points.append(f"Shared PAN corporate root prefix ({pan_a[:4]}) indicating common parent/holding group.")
            confidence_weight += 0.45

    # GSTIN State & Entity Match
    gstin_a = (ext_a.get("gstin") or bid_a.get("gstin") or "").strip().upper()
    gstin_b = (ext_b.get("gstin") or bid_b.get("gstin") or "").strip().upper()
    if gstin_a and gstin_b and len(gstin_a) >= 12 and len(gstin_b) >= 12:
        if gstin_a[2:12] == gstin_b[2:12]:
            flags.append("SAME_PAN_ACROSS_STATE_GSTINS")
            evidence_points.append("Sister branches of the same corporate entity bidding against each other for the same tender.")
            confidence_weight += 0.85

    # Shared Business Email Domain
    dom_a = extract_email_domain(ext_a.get("email"))
    dom_b = extract_email_domain(ext_b.get("email"))
    if dom_a and dom_b and dom_a == dom_b:
        flags.append("SHARED_CORPORATE_EMAIL_DOMAIN")
        evidence_points.append(f"Competitors share identical private corporate email domain: @{dom_a}.")
        confidence_weight += 0.70

    # 4. Temporal Submission Proximity
    time_a = bid_a.get("uploaded_at")
    time_b = bid_b.get("uploaded_at")
    if time_a and time_b and isinstance(time_a, (int, float)) and isinstance(time_b, (int, float)):
        diff_mins = abs(time_a - time_b) / 60.0
        if diff_mins <= 15:
            flags.append("SYNCHRONIZED_SUBMISSION_TIMING")
            evidence_points.append(f"Competing bids uploaded within {diff_mins:.1f} minutes of each other.")
            confidence_weight += 0.30

    if not flags:
        return None

    confidence = min(0.98, round(confidence_weight, 2))
    name_a = bid_a.get("bidder_name") or bid_a.get("company") or "Bidder A"
    name_b = bid_b.get("bidder_name") or bid_b.get("company") or "Bidder B"

    return {
        "bidder_a": {
            "id": bid_a.get("id"),
            "name": name_a,
            "score": bid_a.get("compliance_score") or bid_a.get("score")
        },
        "bidder_b": {
            "id": bid_b.get("id"),
            "name": name_b,
            "score": bid_b.get("compliance_score") or bid_b.get("score")
        },
        "flags": flags,
        "confidence": confidence,
        "evidence": " ".join(evidence_points),
        "statutory_clause": STATUTORY_CARTEL_ACT
    }


def analyze_tender_cartel(tender_id: str, bids: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Scans all bids submitted for a specific tender to identify cartel rings,
    cover bidding, and syndicated bidding patterns."""
    total_bids = len(bids)
    if total_bids < 2:
        return {
            "tender_id": tender_id,
            "total_bids": total_bids,
            "collusion_risk_score": 0,
            "risk_level": "Clean",
            "rings_count": 0,
            "detected_rings": [],
            "flagged_pairs": [],
            "network_graph": {"nodes": [], "links": []},
            "executive_narrative": f"Tender {tender_id} has {total_bids} bid(s) registered. Multi-bid cross-correlation requires at least 2 competing submissions."
        }

    flagged_pairs: List[Dict[str, Any]] = []
    seen_pairs = set()

    for i in range(total_bids):
        for j in range(i + 1, total_bids):
            pair_key = tuple(sorted([bids[i].get("id", ""), bids[j].get("id", "")]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            result = compare_bid_pair(bids[i], bids[j])
            if result:
                flagged_pairs.append(result)

    # Calculate overall tender collusion risk
    if not flagged_pairs:
        risk_score = 10
        risk_level = "Clean"
    else:
        max_conf = max(p["confidence"] for p in flagged_pairs)
        pair_count_multiplier = min(1.3, 1.0 + (len(flagged_pairs) * 0.1))
        risk_score = min(100, int(max_conf * 100 * pair_count_multiplier))
        if risk_score >= 75:
            risk_level = "Critical"
        elif risk_score >= 45:
            risk_level = "High"
        else:
            risk_level = "Elevated"

    # Build Network Graph Nodes and Links for Visualizer
    nodes = []
    node_ids = set()
    for b in bids:
        bid_id = b.get("id", "")
        if bid_id not in node_ids:
            node_ids.add(bid_id)
            name = b.get("bidder_name") or b.get("company") or "Bidder"
            score = b.get("compliance_score") or b.get("score")
            nodes.append({
                "id": bid_id,
                "label": name,
                "score": score,
                "flags_count": len(b.get("flags") or [])
            })

    links = []
    for p in flagged_pairs:
        links.append({
            "source": p["bidder_a"]["id"],
            "target": p["bidder_b"]["id"],
            "confidence": p["confidence"],
            "flags": p["flags"],
            "evidence": p["evidence"]
        })

    # Group into cartel rings
    detected_rings = []
    ring_members_seen = set()
    for idx, p in enumerate(flagged_pairs, start=1):
        id_a = p["bidder_a"]["id"]
        id_b = p["bidder_b"]["id"]
        ring_key = tuple(sorted([id_a, id_b]))
        if ring_key not in ring_members_seen:
            ring_members_seen.add(ring_key)
            detected_rings.append({
                "ring_id": f"CARTEL-RING-{idx:02d}",
                "bidders": [p["bidder_a"]["name"], p["bidder_b"]["name"]],
                "confidence": p["confidence"],
                "primary_flags": p["flags"],
                "evidence_summary": p["evidence"],
                "governing_law": p["statutory_clause"]
            })

    if risk_level in {"Critical", "High"}:
        narrative = (
            f"ALERT: Tender {tender_id} exhibits {risk_level.upper()} risk of collusive bidding / cartelization "
            f"under Section 3(3) of the Competition Act, 2002. Detected {len(flagged_pairs)} suspicious pairwise link(s) "
            f"and {len(detected_rings)} potential syndicate ring(s). Immediate scrutiny of shared document origins, "
            f"cover bidding, or corporate lineage recommended prior to commercial opening."
        )
    else:
        narrative = (
            f"Tender {tender_id} evaluation completed across {total_bids} competing bids. "
            f"No structural collusion rings or coordinated forensic signatures detected. "
            f"Bidding pattern conforms to independent submission criteria under GeM GTC Clause 18."
        )

    return {
        "tender_id": tender_id,
        "total_bids": total_bids,
        "collusion_risk_score": risk_score,
        "risk_level": risk_level,
        "rings_count": len(detected_rings),
        "detected_rings": detected_rings,
        "flagged_pairs": flagged_pairs,
        "network_graph": {
            "nodes": nodes,
            "links": links
        },
        "executive_narrative": narrative
    }


def analyze_bid_collusion_risk(bid_id: str, all_bids: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Finds all competitors linked to a specific bid across the tender."""
    target_bid = next((b for b in all_bids if b.get("id") == bid_id), None)
    if not target_bid:
        return {"bid_id": bid_id, "has_collusion_risk": False, "linked_competitors": []}

    tender_id = target_bid.get("tender_id") or "UNKNOWN"
    tender_bids = [b for b in all_bids if b.get("tender_id") == tender_id]

    linked = []
    for other in tender_bids:
        if other.get("id") == bid_id:
            continue
        pair_res = compare_bid_pair(target_bid, other)
        if pair_res:
            linked.append({
                "competitor_id": other.get("id"),
                "competitor_name": other.get("bidder_name") or other.get("company"),
                "confidence": pair_res["confidence"],
                "flags": pair_res["flags"],
                "evidence": pair_res["evidence"]
            })

    return {
        "bid_id": bid_id,
        "tender_id": tender_id,
        "has_collusion_risk": len(linked) > 0,
        "collusion_flags_count": len(linked),
        "linked_competitors": linked
    }
