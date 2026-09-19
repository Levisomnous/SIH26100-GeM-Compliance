import io
from typing import Any, Dict
import pikepdf


def sha256_bytes(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()


SUSPICIOUS_SOFTWARE = [
    "canva", "photoshop", "gimp", "ilovepdf", "smallpdf", 
    "sejda", "pdfescape", "foxit phantom", "nitro pro", "wondershare"
]


def analyze_pdf_forensics(b: bytes) -> Dict[str, Any]:
    res: Dict[str, Any] = {}
    res['sha256'] = sha256_bytes(b)
    flags = []
    flag_codes = []
    
    try:
        # count occurrences of EOF markers as a heuristic for incremental updates
        eof_count = b.count(b'%%EOF')
        incremental_updates = max(0, eof_count - 1)
        res['incremental_update_count'] = incremental_updates
        if incremental_updates > 0:
            flags.append(f"Document contains {incremental_updates} incremental update(s) after initial save — it was re-opened and modified in an editor after creation")
            flag_codes.append('document_tamper_detected')
            
        # open with pikepdf to read metadata
        pdf = pikepdf.Pdf.open(io.BytesIO(b))
        info = pdf.docinfo
        producer = str(info.get('/Producer')) if info.get('/Producer') else None
        creator = str(info.get('/Creator')) if info.get('/Creator') else None
        creation_date = str(info.get('/CreationDate')) if info.get('/CreationDate') else None
        mod_date = str(info.get('/ModDate')) if info.get('/ModDate') else None
        
        res['producer'] = producer
        res['creator'] = creator
        res['creation_date'] = creation_date
        res['mod_date'] = mod_date
        
        # Check for disallowed/editing software in metadata
        combined_meta = f"{producer or ''} {creator or ''}".lower()
        for soft in SUSPICIOUS_SOFTWARE:
            if soft in combined_meta:
                flags.append(f"Suspicious editing software detected in metadata: '{soft.title()}' (Official statutory certificates are rarely generated using consumer design tools)")
                flag_codes.append('editing_software_detected')
                break
                
        # Check creation vs mod date discrepancy
        if creation_date and mod_date and creation_date != mod_date:
            flags.append(f"PDF Modification Date ({mod_date}) differs from Creation Date ({creation_date}), confirming post-issuance file alteration")
            if 'document_tamper_detected' not in flag_codes:
                flag_codes.append('date_tamper_suspected')
        
        # simple structural checks
        if pdf.pages:
            res['page_count'] = len(pdf.pages)
        else:
            res['page_count'] = None
        pdf.close()
    except Exception:
        res['producer'] = None
        res['creator'] = None
        res['creation_date'] = None
        res['mod_date'] = None
        res['page_count'] = None

    res['flags'] = flags
    res['flag_codes'] = flag_codes
    return res

