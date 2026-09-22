from datetime import date
import unittest

from invoice_converter.parser import parse_invoice_text
from invoice_converter.models import OcrLine


SAMPLE_TEXT = """
增值税电子普通发票
发票代码：031002200411 发票号码：12345678
开票日期：2026年09月18日
购买方信息
名称：上海示例科技有限公司
统一社会信用代码：91310000123456789X
销售方信息
名称：北京供应商有限公司
纳税人识别号：91110108123456789A
项目名称 *信息技术服务*软件服务
不含税金额：1000.00
税额合计：60.00
价税合计（小写）：￥1060.00
税率 6%
收款人：张三 复核人：李四 开票人：王五
"""


class ParserTests(unittest.TestCase):
    def test_parse_common_vat_invoice(self):
        result = parse_invoice_text(SAMPLE_TEXT, source_file="sample.pdf")
        self.assertEqual(result.invoice_number, "12345678")
        self.assertEqual(result.invoice_code, "031002200411")
        self.assertEqual(result.invoice_date, date(2026, 9, 18))
        self.assertEqual(result.buyer_name, "上海示例科技有限公司")
        self.assertEqual(result.buyer_tax_id, "91310000123456789X")
        self.assertEqual(result.seller_name, "北京供应商有限公司")
        self.assertEqual(result.seller_tax_id, "91110108123456789A")
        self.assertEqual(result.amount_without_tax, 1000.0)
        self.assertEqual(result.tax_amount, 60.0)
        self.assertEqual(result.total_amount, 1060.0)
        self.assertEqual(result.tax_rate, "6%")
        self.assertEqual(result.status, "成功")

    def test_missing_fields_are_flagged(self):
        result = parse_invoice_text("电子发票\n发票号码：12345678")
        self.assertEqual(result.status, "需复核")
        self.assertIn("开票日期", result.issues)
        self.assertIn("价税合计", result.issues)

    def test_side_by_side_party_layout_uses_ocr_coordinates(self):
        lines = [
            OcrLine("购买方信息", 47, 59, 75, 181, 0.99),
            OcrLine("销售方信息", 721, 57, 749, 181, 0.99),
            OcrLine("名称：上海示例科技有限公司", 85, 67, 445, 102, 0.99),
            OcrLine("名称：上海示例餐饮有限公司", 762, 72, 1035, 98, 0.99),
            OcrLine("统一社会信用代码/纳税人识别号：91310000EXAMPLE01X", 83, 136, 681, 164, 0.99),
            OcrLine("统一社会信用代码/纳税人识别号：91310000EXAMPLE02A", 760, 136, 1357, 164, 0.99),
        ]
        text = "\n".join(line.text for line in lines)
        result = parse_invoice_text(text, ocr_lines=lines)
        self.assertEqual(result.buyer_name, "上海示例科技有限公司")
        self.assertEqual(result.buyer_tax_id, "91310000EXAMPLE01X")
        self.assertEqual(result.seller_name, "上海示例餐饮有限公司")
        self.assertEqual(result.seller_tax_id, "91310000EXAMPLE02A")

    def test_native_pdf_layout_with_split_labels_and_values(self):
        lines = [
            OcrLine("购", 19.5, 95.3, 28.5, 104.3),
            OcrLine("销", 304.5, 95.3, 313.5, 104.3),
            OcrLine("名称：", 34.5, 102.7, 61.5, 111.7),
            OcrLine("上海示例科技有限公司", 59.5, 100.6, 185.5, 109.6),
            OcrLine("名称：", 321.5, 101.3, 348.5, 110.3),
            OcrLine("上海示例餐饮有限公司", 346.5, 100.6, 436.5, 109.6),
            OcrLine("统一社会信用代码/纳税人识别号：", 34.5, 129.3, 160.6, 138.3),
            OcrLine("91310000EXAMPLE01X", 156.5, 127.3, 286.1, 136.3),
            OcrLine("统一社会信用代码/纳税人识别号：", 321.5, 129.3, 447.6, 138.3),
            OcrLine("91310000EXAMPLE02A", 443.5, 127.3, 573.1, 136.3),
        ]
        text = "\n".join(line.text for line in lines)
        result = parse_invoice_text(text, ocr_lines=lines)
        self.assertEqual(result.buyer_name, "上海示例科技有限公司")
        self.assertEqual(result.buyer_tax_id, "91310000EXAMPLE01X")
        self.assertEqual(result.seller_name, "上海示例餐饮有限公司")
        self.assertEqual(result.seller_tax_id, "91310000EXAMPLE02A")


if __name__ == "__main__":
    unittest.main()
