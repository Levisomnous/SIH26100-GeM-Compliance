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


def cloud_ocr_from_bytes(b: bytes) -> str:
    """Performs real cloud OCR using Google Gemini Vision API or OCR.Space Free Cloud REST API."""
    import os
    import base64
    import httpx

    # 1. Google Gemini 1.5/2.0 Flash Vision (if key provided)
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            b64_data = base64.b64encode(b).decode("utf-8")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
            payload = {
                "contents": [{
                    "parts": [
                        {"text": "Extract all text and tabular information verbatim from this Indian procurement tender/compliance document. Include all GSTIN, PAN, CIN, UDYAM, revenue and vendor details."},
                        {"inline_data": {"mime_type": "application/pdf", "data": b64_data}}
                    ]
                }]
            }
            resp = httpx.post(url, json=payload, timeout=20.0)
            if resp.status_code == 200:
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                if text and len(text.strip()) > 10:
                    return text.strip()
        except Exception:
            pass

    # 2. Free OCR.Space Cloud API (supports PDF and images without credentials)
    try:
        api_key = os.getenv("OCR_SPACE_API_KEY", "helloworld")
        resp = httpx.post(
            "https://api.ocr.space/parse/image",
            data={"apikey": api_key, "language": "eng", "isOverlayRequired": False},
            files={"file": ("document.pdf", b, "application/pdf")},
            timeout=18.0
        )
        if resp.status_code == 200:
            res_json = resp.json()
            results = res_json.get("ParsedResults") or []
            ocr_text = "\n\n".join([r.get("ParsedText", "") for r in results]).strip()
            if ocr_text:
                return ocr_text
    except Exception:
        pass

    return ""


def text_from_pdf_bytes(b: bytes) -> Dict[str, Any]:
    # 1. Try native digital text via pdfplumber
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

    # 2. If extraction produced very little text (scanned PDF / image), use Real Cloud OCR API
    total_chars = len(text.strip())
    if total_chars < 20:
        cloud_text = cloud_ocr_from_bytes(b)
        if cloud_text:
            text = (text + "\n\n" + cloud_text).strip()
            ocr_used = True
        else:
            # 3. Fallback to local pytesseract if available on system
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

    # GSTIN (handles optional spaces or hyphens from OCR)
    gstin_rx_flexible = r"[0-9]{2}[\s\-]?[A-Za-z]{5}[\s\-]?[0-9]{4}[\s\-]?[A-Za-z]{1}[\s\-]?[1-9A-Za-z]{1}[\s\-]?Z[\s\-]?[0-9A-Za-z]{1}"
    m = re.search(gstin_rx_flexible, text)
    if m:
        out['gstin'] = re.sub(r"[\s\-]", "", m.group(0)).upper()

    # PAN (handles optional spaces or hyphens from OCR)
    pan_rx_flexible = r"[A-Za-z]{5}[\s\-]?[0-9]{4}[\s\-]?[A-Za-z]{1}"
    m = re.search(pan_rx_flexible, text)
    if m:
        out['pan'] = re.sub(r"[\s\-]", "", m.group(0)).upper()
    elif out['gstin'] and len(out['gstin']) == 15:
        # Statutory rule: Characters 3-12 of GSTIN are the entity's PAN
        out['pan'] = out['gstin'][2:12]

    # CIN (handles optional spaces from OCR)
    cin_rx_flexible = r"[LUlu]{1}[\s\-]?[0-9]{5}[\s\-]?[A-Za-z]{2}[\s\-]?[0-9]{4}[\s\-]?[A-Za-z]{3}[\s\-]?[0-9]{6}"
    m = re.search(cin_rx_flexible, text)
    if m:
        out['cin'] = re.sub(r"[\s\-]", "", m.group(0)).upper()

    # UDYAM
    m = re.search(r"UDYAM[\s\-]?[A-Za-z]{2}[\s\-]?[0-9]{2}[\s\-]?[0-9]{7}", text, re.IGNORECASE)
    if m:
        raw_u = re.sub(r"[\s]", "", m.group(0)).upper()
        # Ensure standard formatting UDYAM-XX-00-0000000
        parts = re.split(r"[\-]", raw_u.replace("UDYAM", "").strip("-"))
        if len(parts) == 3:
            out['udyam'] = f"UDYAM-{parts[0]}-{parts[1]}-{parts[2]}"
        else:
            out['udyam'] = raw_u
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
