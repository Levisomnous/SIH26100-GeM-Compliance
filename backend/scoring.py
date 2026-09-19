from typing import List, Dict, Any

# Keep deduction values in sync with the frontend
DEDUCTIONS = {
    'ocr_low_confidence': { 'amount': 5,  'label': 'Minor OCR-confidence flag' },
    'document_tamper_detected': { 'amount': 25, 'label': 'Certificate tampering detected (metadata/incremental update)' },
    'editing_software_detected': { 'amount': 25, 'label': 'Consumer editing tool (Canva/Photoshop) detected in statutory certificate' },
    'date_tamper_suspected': { 'amount': 15, 'label': 'Post-issuance modification timestamp anomaly detected' },
    'turnover_inflation': { 'amount': 20, 'label': 'Turnover inflation vs. GST returns' },
    'lapsed_filing':      { 'amount': 15, 'label': 'Lapsed GSTR-3B filing' },
    'lapsed_itr_filing':  { 'amount': 15, 'label': 'Latest ITR not filed / processed' },
    'mca_company_inactive': { 'amount': 30, 'label': 'Company status in MCA21 registry is Strike Off, Dormant, or Inactive' },
    'local_content_mismatch': { 'amount': 15, 'label': 'Local content overstated vs. portal-verified figure' },
    'oem_authorization_invalid': { 'amount': 10, 'label': 'OEM authorization letter could not be verified' },
    'startup_status_unverified': { 'amount': 10, 'label': 'Startup India recognition could not be confirmed' },
    'nsic_status_unverified': { 'amount': 10, 'label': 'NSIC registration could not be confirmed' },
    'bis_dpiit_unverified': { 'amount': 10, 'label': 'BIS / DPIIT certification could not be confirmed' },
    'document_authenticity_mismatch': { 'amount': 25, 'label': 'DigiLocker authenticity check failed on a submitted document' },
    'gstin_checksum_invalid': { 'amount': 25, 'label': 'GSTIN fails GSTN\'s official checksum — fabricated or mistyped' },
    'pan_format_invalid': { 'amount': 20, 'label': 'PAN does not conform to statutory Income Tax format' },
    'cin_format_invalid': { 'amount': 20, 'label': 'CIN does not conform to MCA21 corporate structure' },
    'recycled_document_detected': { 'amount': 35, 'label': 'Identical document file previously submitted under a different bid' },
    'tender_ineligible': { 'amount': 20, 'label': 'Bid does not meet the selected tender\'s minimum eligibility criteria' },
    'epfo_esic_noncompliant': { 'amount': 15, 'label': 'EPFO ECR not filed or ESIC contribution defaulted — labour compliance failure' }
}

RISK_THRESHOLDS = { 'low': 90, 'medium': 70, 'high': 40 }

FRAUD_SIGNALS = {
    'debarment_match',
    'document_tamper_detected',
    'editing_software_detected',
    'recycled_document_detected',
    'mca_company_inactive'
}


def compute_score(flags: List[str]) -> Dict[str, Any]:
    if 'debarment_match' in flags:
        return {
            'score': 0.0,
            'risk_level': 'Critical',
            'flags': flags,
            'missing_documents': [],
            'components': {'identity_validation': 0.0, 'registry_checks': 0.0, 'revenue_consistency': 0.0, 'forensic_integrity': 0.0}
        }

    score = 100.0
    for f in flags:
        if f in DEDUCTIONS:
            score -= DEDUCTIONS[f]['amount']
    if score < 0:
        score = 0.0

    # Slide 1 Rule: "Any Fraud Signal? -> YES -> Mark as High Risk & Add Flags"
    has_fraud_signal = any(f in FRAUD_SIGNALS for f in flags)
    
    if has_fraud_signal:
        # Cap score so it falls into High or Critical risk band
        if score >= RISK_THRESHOLDS['high']:
            score = 39.0
        risk = 'Critical' if score < 25 else 'High'
    else:
        if score >= RISK_THRESHOLDS['low']:
            risk = 'Low'
        elif score >= RISK_THRESHOLDS['medium']:
            risk = 'Medium'
        elif score >= RISK_THRESHOLDS['high']:
            risk = 'High'
        else:
            risk = 'Critical'

    # Compute dynamic components
    id_deduct = sum(DEDUCTIONS[f]['amount'] for f in flags if f in ('gstin_checksum_invalid', 'pan_format_invalid', 'cin_format_invalid'))
    reg_deduct = sum(DEDUCTIONS[f]['amount'] for f in flags if f in ('lapsed_filing', 'lapsed_itr_filing', 'mca_company_inactive', 'document_authenticity_mismatch', 'startup_status_unverified', 'nsic_status_unverified', 'bis_dpiit_unverified', 'epfo_esic_noncompliant'))
    rev_deduct = sum(DEDUCTIONS[f]['amount'] for f in flags if f in ('turnover_inflation', 'local_content_mismatch', 'tender_ineligible', 'oem_authorization_invalid'))
    for_deduct = sum(DEDUCTIONS[f]['amount'] for f in flags if f in ('document_tamper_detected', 'editing_software_detected', 'date_tamper_suspected', 'recycled_document_detected', 'ocr_low_confidence'))

    components = {
        'identity_validation': round(max(0.0, 30.0 - id_deduct), 1),
        'registry_checks': round(max(0.0, 30.0 - reg_deduct), 1),
        'revenue_consistency': round(max(0.0, 15.0 - rev_deduct), 1),
        'forensic_integrity': round(max(0.0, 25.0 - for_deduct), 1)
    }

    return {
        'score': round(score, 1),
        'risk_level': risk,
        'flags': flags,
        'missing_documents': [],
        'components': components
    }

