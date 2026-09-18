from typing import List, Dict, Any

# Keep deduction values in sync with the frontend
DEDUCTIONS = {
    'ocr_low_confidence': { 'amount': 5,  'label': 'Minor OCR-confidence flag' },
    'document_tamper_detected': { 'amount': 20, 'label': 'Certificate tampering detected (metadata/font mismatch)' },
    'turnover_inflation': { 'amount': 20, 'label': 'Turnover inflation vs. GST returns' },
    'lapsed_filing':      { 'amount': 15, 'label': 'Lapsed GSTR-3B filing' },
    'lapsed_itr_filing':  { 'amount': 15, 'label': 'Latest ITR not filed / processed' },
    'local_content_mismatch': { 'amount': 15, 'label': 'Local content overstated vs. portal-verified figure' },
    'oem_authorization_invalid': { 'amount': 10, 'label': 'OEM authorization letter could not be verified' },
    'startup_status_unverified': { 'amount': 10, 'label': 'Startup India recognition could not be confirmed' },
    'nsic_status_unverified': { 'amount': 10, 'label': 'NSIC registration could not be confirmed' },
    'bis_dpiit_unverified': { 'amount': 10, 'label': 'BIS / DPIIT certification could not be confirmed' },
    'document_authenticity_mismatch': { 'amount': 25, 'label': 'DigiLocker authenticity check failed on a submitted document' }
}

RISK_THRESHOLDS = { 'low':90, 'medium':70, 'high':40 }


def compute_score(flags: List[str]) -> Dict[str, Any]:
    if 'debarment_match' in flags:
        return { 'score': 0.0, 'risk_level': 'Critical', 'flags': flags }
    score = 100.0
    for f in flags:
        if f in DEDUCTIONS:
            score -= DEDUCTIONS[f]['amount']
    if score < 0: score = 0.0
    if score >= RISK_THRESHOLDS['low']:
        risk='Low'
    elif score >= RISK_THRESHOLDS['medium']:
        risk='Medium'
    elif score >= RISK_THRESHOLDS['high']:
        risk='High'
    else:
        risk='Critical'

    components = {
        'identity_validation': 30.0,
        'registry_checks': 30.0,
        'revenue_consistency': 15.0,
        'forensic_integrity': 25.0
    }

    return {
        'score': round(score,1),
        'risk_level': risk,
        'flags': flags,
        'missing_documents': [],
        'components': components
    }
