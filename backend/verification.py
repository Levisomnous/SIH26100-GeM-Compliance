from typing import Dict, Any, List, Optional
from . import database
import json


# GSTN's published check-digit algorithm (mod-36). This runs against the
# extracted string itself — no external API needed — and catches typos or
# fabricated GSTINs that merely match the format regex but aren't valid.
_GST_CODEPOINTS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def validate_gstin_checksum(gstin: Optional[str]) -> bool:
    if not gstin or len(gstin) != 15:
        return False
    gstin = gstin.upper()
    factor = 1
    total = 0
    mod = 36
    for ch in gstin[:-1]:
        if ch not in _GST_CODEPOINTS:
            return False
        code_point = _GST_CODEPOINTS.index(ch)
        digit = factor * code_point
        digit = (digit // mod) + (digit % mod)
        total += digit
        factor = 2 if factor == 1 else 1
    checksum_char = _GST_CODEPOINTS[(mod - (total % mod)) % mod]
    return checksum_char == gstin[-1]


def validate_pan_format(pan: Optional[str]) -> Dict[str, Any]:
    """Validates structural format of Indian Permanent Account Number (PAN).
    Format: 5 letters + 4 digits + 1 letter (e.g. ABCDE1234F).
    4th character designates entity type.
    """
    if not pan or len(pan) != 10:
        return {"valid": False, "entity_type": None, "entity_name": "Invalid / Missing", "reason": "Length must be exactly 10 characters"}
    pan = pan.upper()
    import re
    if not re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$", pan):
        return {"valid": False, "entity_type": None, "entity_name": "Invalid Format", "reason": "Does not match standard 5-alpha 4-digit 1-alpha format"}
    
    entity_map = {
        'C': 'Company (Private Ltd / Public Ltd)',
        'P': 'Individual / Sole Proprietor',
        'H': 'Hindu Undivided Family (HUF)',
        'F': 'Partnership Firm / LLP',
        'A': 'Association of Persons (AOP)',
        'T': 'Trust',
        'B': 'Body of Individuals (BOI)',
        'L': 'Local Authority',
        'J': 'Artificial Juridical Person',
        'G': 'Government Agency'
    }
    entity_char = pan[3]
    entity_name = entity_map.get(entity_char, "Other / Unknown")
    return {
        "valid": True,
        "pan": pan,
        "entity_type": entity_char,
        "entity_name": entity_name
    }


def validate_cin_format(cin: Optional[str]) -> Dict[str, Any]:
    """Validates structural format of 21-digit Corporate Identity Number (CIN).
    Format: Listing (1 char) + Industry (5 digits) + State (2 letters) + Year (4 digits) + Classification (3 letters) + RegNo (6 digits).
    Example: U72200DL2018PTC123456
    """
    if not cin or len(cin) != 21:
        return {"valid": False, "reason": "CIN must be exactly 21 alphanumeric characters"}
    cin = cin.upper()
    import re
    m = re.match(r"^([LUlu])([0-9]{5})([A-Za-z]{2})([0-9]{4})([A-Za-z]{3})([0-9]{6})$", cin)
    if not m:
        return {"valid": False, "reason": "CIN structure mismatch with MCA21 standards"}
    
    listing_status = "Listed" if m.group(1) == 'L' else "Unlisted"
    state_code = m.group(3).upper()
    incorporation_year = int(m.group(4))
    class_code = m.group(5).upper()
    
    class_map = {
        'PTC': 'Private Limited Company',
        'PLC': 'Public Limited Company',
        'GOI': 'Government of India Company',
        'ULL': 'Unlimited Liability Company',
        'SGC': 'State Government Company',
        'FTC': 'Foreign Subsidiary Company'
    }
    company_type = class_map.get(class_code, f"{class_code} Entity")
    
    return {
        "valid": True,
        "cin": cin,
        "listing_status": listing_status,
        "state_code": state_code,
        "incorporation_year": incorporation_year,
        "company_type": company_type
    }


def validate_udyam_format(udyam: Optional[str]) -> Dict[str, Any]:
    """Validates MSME Udyam Registration number format.
    Format: UDYAM-XX-00-0000000 (e.g. UDYAM-DL-01-0012345)
    """
    if not udyam:
        return {"valid": False, "reason": "Missing Udyam identifier"}
    import re
    udyam = udyam.upper().strip()
    if re.match(r"^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$", udyam):
        return {"valid": True, "udyam": udyam}
    return {"valid": False, "reason": "Does not conform to UDYAM-StateCode-DistrictCode-7Digits"}


def simulate_gstn_lookup(conn, gstin: str) -> Dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT report_json::text FROM bids WHERE report_json::text LIKE %s LIMIT 1", ('%"gstin": "'+(gstin or '')+'"%',))
    r = cur.fetchone()
    if r:
        try:
            rpt = json.loads(r[0])
            for item in rpt.get('registry_results', []):
                if item.get('registry') == 'GSTN':
                    item['source'] = 'SIMULATED'
                    return item
        except Exception:
            pass
    if gstin and (gstin.endswith('ZZ') or gstin.endswith('FAIL')):
        return { 'source':'SIMULATED', 'registry':'GSTN', 'gstin': gstin, 'status':'Active', 'return_filing_status': 'Overdue', 'taxpayer_type': 'Regular' }
    return { 'source':'SIMULATED', 'registry':'GSTN', 'gstin': gstin, 'status':'Active', 'return_filing_status': 'Up to date', 'taxpayer_type': 'Regular' }


def simulate_income_tax(conn, pan: str) -> Dict[str, Any]:
    if pan and (pan.endswith('ZZ') or pan.endswith('FAIL')):
        return { 'source':'SIMULATED', 'registry':'Income Tax Department', 'pan': pan, 'itr_filed': False, 'latest_ay': '2024-25', 'status': 'Defaulter' }
    return { 'source':'SIMULATED', 'registry':'Income Tax Department', 'pan': pan, 'itr_filed': True, 'latest_ay': '2024-25', 'status': 'Compliant', 'latest_filed_revenue': None }


def simulate_mca_lookup(conn, cin: str) -> Dict[str, Any]:
    if cin and (cin.endswith('ZZ') or cin.endswith('FAIL')):
        return { 'source':'SIMULATED', 'registry':'MCA21', 'cin': cin, 'company_status':'Strike Off / Inactive', 'compliance_status': 'Defaulted' }
    return { 'source':'SIMULATED', 'registry':'MCA21', 'cin': cin, 'company_status':'Active', 'compliance_status': 'Compliant' }


def simulate_digilocker_verification(extracted: Dict[str, Any]) -> Dict[str, Any]:
    sha = extracted.get('sha256') or ''
    # If document has incremental updates or ends with FAIL, simulate unverified signature
    if extracted.get('ocr_used') and not extracted.get('gstin'):
        return { 'source':'SIMULATED', 'registry':'DigiLocker', 'document_hash': sha[:16], 'issuer_signature_verified': False, 'status': 'Signature Unverified' }
    return { 'source':'SIMULATED', 'registry':'DigiLocker', 'document_hash': sha[:16], 'issuer_signature_verified': True, 'status': 'Digitally Verified' }


def simulate_epfo_esic(pan: Optional[str]) -> Dict[str, Any]:
    """Simulate EPFO Establishment Code & ESIC compliance check.

    In production this would call the EPFO Unified Portal API to verify
    the employer's establishment code, ECR filing history and ESIC
    contribution status.
    """
    if pan and (pan.endswith('ZZ') or pan.endswith('FAIL')):
        return {
            'source': 'SIMULATED', 'registry': 'EPFO / ESIC',
            'establishment_code': 'DLCPM0012345000',
            'ecr_filed_current_month': False,
            'esic_compliant': False,
            'total_employees': 48,
            'status': 'Defaulter — ECR not filed for current month'
        }
    return {
        'source': 'SIMULATED', 'registry': 'EPFO / ESIC',
        'establishment_code': 'TNCPM0067890000',
        'ecr_filed_current_month': True,
        'esic_compliant': True,
        'total_employees': 126,
        'status': 'Compliant'
    }


def simulate_cppp_debarment(cin: Optional[str], pan: Optional[str]) -> Dict[str, Any]:
    """Simulate CPPP (Central Public Procurement Portal) and GeM debarment registry check.

    In production this would query the GeM seller blacklist and the CPPP
    Debarred Vendors list maintained by DGS&D / MoF.
    """
    # Simulate a debarred entity if CIN or PAN ends with specific markers
    if (cin and cin.endswith('DEBAR')) or (pan and pan.endswith('DEBAR')):
        return {
            'source': 'SIMULATED', 'registry': 'CPPP Debarment Registry',
            'debarred': True,
            'reason': 'Debarred by DGS&D Order No. 2024/DB/0731 for fraudulent supply',
            'debarment_period': '2024-07-01 to 2027-06-30',
            'status': 'DEBARRED'
        }
    return {
        'source': 'SIMULATED', 'registry': 'CPPP Debarment Registry',
        'debarred': False,
        'gem_seller_status': 'Active',
        'status': 'Not Debarred'
    }


def simulate_nsic(udyam: Optional[str]) -> Dict[str, Any]:
    """Simulate NSIC (National Small Industries Corporation) registration check."""
    if not udyam:
        return {
            'source': 'SIMULATED', 'registry': 'NSIC',
            'registered': False,
            'status': 'No MSME/Udyam identifier — NSIC lookup skipped'
        }
    return {
        'source': 'SIMULATED', 'registry': 'NSIC',
        'registered': True,
        'nsic_certificate_no': f'NSIC/{udyam[-7:]}/2025',
        'valid_until': '2027-03-31',
        'status': 'Registered & Valid'
    }


def simulate_bis_dpiit(extracted: Dict[str, Any]) -> Dict[str, Any]:
    """Simulate BIS (Bureau of Indian Standards) and DPIIT certification check."""
    has_claim = extracted.get('claims_startup_status') or extracted.get('has_oem_letter_mention')
    if has_claim and not extracted.get('udyam'):
        return {
            'source': 'SIMULATED', 'registry': 'BIS / DPIIT',
            'bis_certified': False,
            'dpiit_recognized': False,
            'status': 'Unverified — supporting documentation missing'
        }
    return {
        'source': 'SIMULATED', 'registry': 'BIS / DPIIT',
        'bis_certified': True,
        'bis_license_no': 'CM/L-9876543',
        'dpiit_recognized': bool(extracted.get('claims_startup_status')),
        'status': 'Certified'
    }


def classify_make_in_india(local_content_pct: Optional[float]) -> Dict[str, Any]:
    """Classify bidder under Make in India purchase preference policy.

    Class-I Local Supplier:  ≥ 50% local content
    Class-II Local Supplier: ≥ 20% and < 50%
    Non-Local Supplier:      < 20% or undeclared

    Ref: DPIIT Order P-45021/2/2017-PP (BE-II), Make in India policy for
    public procurement under GeM.
    """
    if local_content_pct is None:
        return {
            'source': 'REAL', 'registry': 'Make in India Classification',
            'declared_local_content': None,
            'classification': 'Non-Local Supplier',
            'purchase_preference_eligible': False,
            'status': 'Local content not declared'
        }
    if local_content_pct >= 50:
        cls = 'Class-I Local Supplier'
        eligible = True
    elif local_content_pct >= 20:
        cls = 'Class-II Local Supplier'
        eligible = True
    else:
        cls = 'Non-Local Supplier'
        eligible = False
    return {
        'source': 'REAL', 'registry': 'Make in India Classification',
        'declared_local_content': local_content_pct,
        'classification': cls,
        'purchase_preference_eligible': eligible,
        'status': cls
    }


def simulate_registry_checks(conn, extracted: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    gst_res = simulate_gstn_lookup(conn, extracted.get('gstin'))
    out.append(gst_res)

    it_res = simulate_income_tax(conn, extracted.get('pan'))
    out.append(it_res)

    mca_res = simulate_mca_lookup(conn, extracted.get('cin'))
    out.append(mca_res)

    dl_res = simulate_digilocker_verification(extracted)
    out.append(dl_res)

    # EPFO / ESIC labour compliance
    epfo_res = simulate_epfo_esic(extracted.get('pan'))
    out.append(epfo_res)

    # CPPP / GeM debarment registry
    debar_res = simulate_cppp_debarment(extracted.get('cin'), extracted.get('pan'))
    out.append(debar_res)

    # NSIC registration
    nsic_res = simulate_nsic(extracted.get('udyam'))
    out.append(nsic_res)

    # BIS / DPIIT certification
    bis_res = simulate_bis_dpiit(extracted)
    out.append(bis_res)

    # Make in India classification (real — computed from extracted data)
    mii_res = classify_make_in_india(extracted.get('declared_local_content'))
    out.append(mii_res)

    # Startup India (conditional)
    if extracted.get('claims_startup_status'):
        status = 'Verified' if extracted.get('udyam') else 'Unverified'
        out.append({ 'source':'SIMULATED', 'registry':'Startup India', 'status': status, 'dppit_recognition': status == 'Verified' })
    return out

