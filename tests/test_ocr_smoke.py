from pathlib import Path
import unittest

from PIL import Image, ImageDraw, ImageFont

from invoice_converter.extractor import InvoiceExtractor


class OcrSmokeTests(unittest.TestCase):
    def test_local_chinese_ocr_engine(self):
        font_path = Path("C:/Windows/Fonts/msyh.ttc")
        if not font_path.exists():
            self.skipTest("Windows Chinese font is unavailable")
        image = Image.new("RGB", (1400, 360), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(font_path), 52)
        draw.text((40, 35), "增值税电子普通发票", font=font, fill="black")
        draw.text((40, 130), "发票号码：12345678", font=font, fill="black")
        draw.text((40, 225), "开票日期：2026年09月18日", font=font, fill="black")
        extracted = InvoiceExtractor()._ocr_image(image)
        self.assertIn("12345678", extracted.text.replace(" ", ""))
        self.assertIsNotNone(extracted.confidence)


if __name__ == "__main__":
    unittest.main()
