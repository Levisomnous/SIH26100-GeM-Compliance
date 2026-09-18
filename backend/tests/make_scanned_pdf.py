from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import os
os.makedirs('tests', exist_ok=True)
# create image
img = Image.new('RGB', (600, 200), color='white')
d = ImageDraw.Draw(img)
# use default font
d.text((10,10), "Scanned Company\nGSTIN: 07AAACV1234F1ZZ\nPAN: AAACV1234F", fill=(0,0,0))
img_path = 'tests/tmp_scan.jpg'
img.save(img_path)
# create PDF with image
pdf_path = 'tests/scanned_sample.pdf'
c = canvas.Canvas(pdf_path, pagesize=letter)
c.drawImage(img_path, 50, 500, width=500, height=150)
c.showPage()
c.save()
print('wrote', pdf_path)
