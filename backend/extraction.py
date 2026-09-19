import io
import hashlib
from typing import Dict, Any
import pdfplumber
from pdf2image import convert_from_bytes
import pytesseract


def sha256_bytes(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()


GSTIN_RX = r"[0-9]{2}[A-Za-z]{5}[0-9]{4}[A-Za-z]{1}[1-9A-Za-z]{1}Z[0-9A-Za-z]{1}"
PAN_RX = r"[A-Za-z]{5}[0-9]{4}[A-Za-z]{1}"
CIN_RX = r"[LUlu]{1}[0-9]{5}[A-Za-z]{2}[0-9]{4}[A-Za-z]{3}[0-9]{6}"
UDYAM_RX = r"UDYAM-[A-Za-z]{2}-[0-9]{2}-[0-9]{7}"


def text_from_pdf_bytes(b: bytes) -> Dict[str, Any]:
    # Try pdfplumber first
    text = ""
    pages_text = []
    try:
        with pdfplumber.open(io.BytesIO(b)) as pdf:
            for p in pdf.pages:
                t = p.extract_text() or ""
                pages_text.append(t)
        text = "\n\n".join(pages_text)
        ocr_used = False
    except Exception:
        text = ""
        ocr_used = True

    # If extraction produced very little text, use OCR fallback
    total_chars = len(text.strip())
    if total_chars < 20:
        try:
            images = convert_from_bytes(b, dpi=200)
            ocr_pages = []
            for img in images:
                ocr_pages.append(pytesseract.image_to_string(img))
            ocr_text = "\n\n".join(ocr_pages)
            text = (text + "\n\n" + ocr_text).strip()
            ocr_used = True
        except Exception:
            pass

    return {"text": text, "ocr_used": ocr_used}


def extract_fields(text: str) -> Dict[str, Any]:
    import re
    out = {}
    out['gstin'] = None
    out['pan'] = None
    out['cin'] = None
    out['udyam'] = None
    out['declared_revenue'] = None
    out['declared_local_content'] = None
    out['claims_startup_status'] = False
    out['has_oem_letter_mention'] = False

    # GSTIN
    m = re.search(GSTIN_RX, text)
    if m:
        out['gstin'] = m.group(0)
    # PAN
    m = re.search(PAN_RX, text)
    if m:
        out['pan'] = m.group(0)
    # CIN
    m = re.search(CIN_RX, text)
    if m:
        out['cin'] = m.group(0)
    # UDYAM
    m = re.search(UDYAM_RX, text)
    if m:
        out['udyam'] = m.group(0)
    # declared revenue: look for ₹ or Rs
    m = re.search(r"(?:₹|Rs\.?|INR)\s*([0-9,]+(?:\.[0-9]+)?)", text)
    if m:
        try:
            out['declared_revenue'] = float(m.group(1).replace(',', ''))
        except Exception:
            out['declared_revenue'] = None
    # declared local content: look for "local content ... NN%"
    m = re.search(r"local\s+content[^0-9%]{0,20}([0-9]{1,3}(?:\.[0-9]+)?)\s*%", text, re.IGNORECASE)
    if m:
        try:
            pct = float(m.group(1))
            out['declared_local_content'] = pct if 0 <= pct <= 100 else None
        except Exception:
            out['declared_local_content'] = None
    # startup claim
    if 'startup india' in text.lower() or 'startupindia' in text.lower():
        out['claims_startup_status'] = True
    # OEM mention
    if 'oem' in text.lower() or 'original equipment manufacturer' in text.lower():
        out['has_oem_letter_mention'] = True

    return out


def analyze_pdf_bytes(b: bytes, filename: str = None) -> Dict[str, Any]:
    sha256 = sha256_bytes(b)
    txt_res = text_from_pdf_bytes(b)
    fields = extract_fields(txt_res['text'] or '')
    return {
        'sha256': sha256,
        'text': txt_res['text'],
        'ocr_used': txt_res['ocr_used'],
        'page_count': None,
        **fields
    }
