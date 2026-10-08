import sys
from pathlib import Path
import ddddocr

# Đảm bảo in được tiếng Việt trên console Windows (tránh lỗi cp1252)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

image_path = Path(r"C:\Users\Administrator\Documents\Claude\KiemTraViPhamGiaoThong\output\code.png")

if not image_path.exists():
    raise FileNotFoundError(f"Không tìm thấy: {image_path.resolve()}")

ocr = ddddocr.DdddOcr(show_ad=False)

with image_path.open("rb") as f:
    image_bytes = f.read()

result = ocr.classification(image_bytes)

print("Kết quả OCR:", result)