"""Regenerate the two demo bid PDFs referenced in the README quickstart.

    python3 samples/generate_samples.py

Requires reportlab (dev-only dependency — see requirements-dev.txt).
"""
import os
import sys

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backend.verification import _GST_CODEPOINTS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def valid_gstin(prefix14: str) -> str:
    """Append a correct GSTN mod-36 check digit to a 14-char GSTIN prefix."""
    factor = 1
    total = 0
    mod = 36
    for ch in prefix14:
        code_point = _GST_CODEPOINTS.index(ch)
        digit = factor * code_point
        digit = (digit // mod) + (digit % mod)
        total += digit
        factor = 2 if factor == 1 else 1
    checksum_char = _GST_CODEPOINTS[(mod - (total % mod)) % mod]
    return prefix14 + checksum_char


def write_clean_bid():
    path = os.path.join(HERE, "bid_vantara_systems.pdf")
    gstin = valid_gstin("07AAACV1234F1Z")  # last char is the real check digit
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica", 12)
    lines = [
        "Vantara Systems Pvt Ltd",
        "Bid submission for GEM-2026-IT-004521 (Supply of IT Hardware and Peripherals)",
        "",
        f"GSTIN: {gstin}",
        "PAN: AAACV1234F",
        "CIN: U72200DL2015PTC281234",
        "Declared revenue: Rs 85,000,000",
        "Local content: 62%",
        "",
        "This bid is submitted in full compliance with GeM eligibility norms.",
    ]
    y = 730
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.showPage()
    c.save()
    print(f"wrote {path} (GSTIN {gstin} — valid checksum)")


def write_flagged_bid():
    path = os.path.join(HERE, "bid_northstar_traders.pdf")
    # deliberately corrupt the check digit of an otherwise well-formed GSTIN
    good = valid_gstin("07AAACN9876G1Z")
    bad_last = "A" if good[-1] != "A" else "B"
    bad_gstin = good[:-1] + bad_last

    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica", 12)
    lines = [
        "Northstar Traders",
        "Bid submission for GEM-2026-MSME-011 (MSME-Reserved: Office Furniture)",
        "",
        f"GSTIN: {bad_gstin}",
        "PAN: AAACN9876G",
        "Declared revenue: Rs 1,200,000",
        "Local content: 10%",
        "",
        "This bidder claims Startup India recognition.",
        "(No Udyam registration number is included in this submission.)",
    ]
    y = 730
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.showPage()
    c.save()

    # Simulate a post-signing incremental edit: append a second %%EOF marker.
    # forensics.analyze_pdf_forensics() flags any file with more than one as
    # having been re-opened and modified after its initial save.
    with open(path, "ab") as f:
        f.write(b"\n%%EOF\n")

    print(f"wrote {path} (GSTIN {bad_gstin} — invalid checksum, +incremental update, startup claim w/o Udyam)")


if __name__ == "__main__":
    write_clean_bid()
    write_flagged_bid()
