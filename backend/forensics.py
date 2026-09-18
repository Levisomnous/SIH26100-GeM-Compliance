import io
from typing import Any, Dict
import pikepdf


def sha256_bytes(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()


def analyze_pdf_forensics(b: bytes) -> Dict[str, Any]:
    res: Dict[str, Any] = {}
    res['sha256'] = sha256_bytes(b)
    flags = []
    try:
        # count occurrences of EOF markers as a heuristic for incremental updates
        eof_count = b.count(b'%%EOF')
        incremental_updates = max(0, eof_count - 1)
        res['incremental_update_count'] = incremental_updates
        if incremental_updates > 0:
            flags.append(f"Document contains {incremental_updates} incremental update(s) after initial save — it was re-opened and modified in an editor after creation")
        # open with pikepdf to read metadata
        pdf = pikepdf.Pdf.open(io.BytesIO(b))
        info = pdf.docinfo
        res['producer'] = str(info.get('/Producer')) if info.get('/Producer') else None
        res['creator'] = str(info.get('/Creator')) if info.get('/Creator') else None
        res['creation_date'] = str(info.get('/CreationDate')) if info.get('/CreationDate') else None
        res['mod_date'] = str(info.get('/ModDate')) if info.get('/ModDate') else None
        # simple structural checks
        if pdf.pages:
            res['page_count'] = len(pdf.pages)
        else:
            res['page_count'] = None
        pdf.close()
    except Exception:
        # best-effort metadata
        res['producer'] = None
        res['creator'] = None
        res['creation_date'] = None
        res['mod_date'] = None
        res['page_count'] = None

    res['flags'] = flags
    return res
