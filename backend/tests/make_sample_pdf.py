from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

fn='tests/sample_text.pdf'
import os
os.makedirs('tests', exist_ok=True)
c = canvas.Canvas(fn, pagesize=letter)
c.setFont('Helvetica', 12)
c.drawString(72,720, 'Vantara Systems')
c.drawString(72,700, 'GSTIN: 07AAACV1234F1ZR')
c.drawString(72,680, 'PAN: AAACV1234F')
c.drawString(72,660, 'CIN: U72200DL2015PTC281234')
c.drawString(72,640, 'Declared revenue: ₹85,000,000')
c.showPage()
c.save()
print('wrote', fn)
