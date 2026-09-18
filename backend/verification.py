from typing import Dict, Any, List
from . import database
import json


def simulate_gstn_lookup(conn, gstin: str) -> Dict[str, Any]:
    # Try to reuse an existing bid record with same GSTIN as simulated source
    cur = conn.cursor()
    cur.execute("SELECT report_json FROM bids WHERE report_json LIKE ? LIMIT 1", ('%"gstin": "%'+(gstin or '')+'"%',))
    r = cur.fetchone()
    if r:
        try:
            rpt = json.loads(r[0])
            # pick any registry_results from the stored report
            for item in rpt.get('registry_results', []):
                if item.get('registry') == 'GSTN':
                    item['source'] = 'SIMULATED'
                    return item
        except Exception:
            pass
    # otherwise deterministic fallback
    if gstin and gstin.endswith('ZZ'):
        return { 'source':'SIMULATED', 'registry':'GSTN', 'gstin': gstin, 'status':'Active', 'return_filing_status': 'Overdue' }
    return { 'source':'SIMULATED', 'registry':'GSTN', 'gstin': gstin, 'status':'Active', 'return_filing_status': 'Up to date' }


def simulate_income_tax(conn, pan: str) -> Dict[str, Any]:
    if pan and pan.endswith('ZZ'):
        return { 'source':'SIMULATED', 'registry':'Income Tax Department', 'pan': pan, 'itr_filed': False }
    return { 'source':'SIMULATED', 'registry':'Income Tax Department', 'pan': pan, 'itr_filed': True, 'latest_filed_revenue': None }


def simulate_registry_checks(conn, extracted: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    gst_res = simulate_gstn_lookup(conn, extracted.get('gstin'))
    out.append(gst_res)
    it_res = simulate_income_tax(conn, extracted.get('pan'))
    out.append(it_res)
    # other registries: simple placeholders
    out.append({ 'source':'SIMULATED', 'registry':'MCA21', 'cin': extracted.get('cin'), 'company_status':'Active' })
    out.append({ 'source':'SIMULATED', 'registry':'DigiLocker', 'document_hash': extracted.get('sha256')[:16], 'issuer_signature_verified': True })
    # conditional claims
    if extracted.get('claims_startup_status'):
        out.append({ 'source':'SIMULATED', 'registry':'Startup India', 'status':'Unverified' if not extracted.get('udyam') else 'Verified' })
    return out
