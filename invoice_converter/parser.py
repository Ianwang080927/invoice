from __future__ import annotations

import re
import unicodedata
from datetime import date

from .models import InvoiceRecord, OcrLine


MONEY_RE = r"(?:[¥￥]?\s*)([-+]?\d[\d,，]*\.\d{1,2})"
TAX_ID_RE = r"([0-9A-Z]{15,20})"


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _first(text: str, patterns: list[str], flags: int = re.I | re.S) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            value = match.group(1).strip(" :：|丨_-")
            if value:
                return value
    return ""


def _section(text: str, starts: tuple[str, ...], ends: tuple[str, ...]) -> str:
    positions = [text.find(token) for token in starts if token in text]
    if not positions:
        return ""
    start = min(pos for pos in positions if pos >= 0)
    candidates = [text.find(token, start + 2) for token in ends]
    candidates = [pos for pos in candidates if pos > start]
    end = min(candidates) if candidates else min(len(text), start + 700)
    return text[start:end]


def _clean_party(value: str) -> str:
    value = re.split(r"(?:纳税人识别号|统一社会信用代码|地址|电话|开户行|账号)", value)[0]
    return value.strip(" :：|丨_-")[:100]


def _party(section: str) -> tuple[str, str]:
    if not section:
        return "", ""
    name = _first(
        section,
        [
            r"名称\s*[:：]\s*([^\n]{2,100})",
            r"(?:购买方|销售方)名称\s*[:：]?\s*([^\n]{2,100})",
        ],
    )
    tax_id = _first(
        _compact(section),
        [
            rf"(?:纳税人识别号|统一社会信用代码|税号)[:：]?{TAX_ID_RE}",
        ],
        re.I,
    )
    return _clean_party(name), tax_id


def _party_from_layout(lines: list[OcrLine]) -> tuple[str, str, str, str]:
    """Extract left/right party fields from side-by-side invoice regions."""
    buyer_headers = [line for line in lines if "购买方" in _compact(line.text)]
    seller_headers = [line for line in lines if "销售方" in _compact(line.text)]
    if buyer_headers and seller_headers:
        buyer_header = min(buyer_headers, key=lambda line: line.left)
        seller_header = max(seller_headers, key=lambda line: line.left)
        if seller_header.center_x <= buyer_header.center_x:
            return "", "", "", ""
        top = min(buyer_header.top, seller_header.top) - 40
        bottom = max(buyer_header.bottom, seller_header.bottom) + 50
        left_boundary = buyer_header.center_x
        split_x = seller_header.center_x
    else:
        # Native electronic PDFs often split the vertical party headers into
        # individual characters. Two positioned "名称：" labels are more stable
        # anchors than trying to rebuild those vertical words.
        name_labels = [
            line
            for line in lines
            if re.search(r"(?:^|\s)名称\s*[:：]", _normalize(line.text))
            and "项目名称" not in line.text
        ]
        if len(name_labels) < 2:
            return "", "", "", ""
        name_labels = sorted(name_labels, key=lambda line: line.left)
        buyer_label, seller_label = name_labels[0], name_labels[-1]
        tax_labels = [
            line
            for line in lines
            if "纳税人识别号" in _compact(line.text) or "统一社会信用代码" in _compact(line.text)
        ]
        top = min(buyer_label.top, seller_label.top) - 20
        bottom = max([buyer_label.bottom, seller_label.bottom] + [line.bottom for line in tax_labels]) + 25
        left_boundary = max(0, buyer_label.left - 20)
        split_x = (buyer_label.left + seller_label.left) / 2

    buyer_lines = [
        line
        for line in lines
        if top <= line.center_y <= bottom
        and line.left >= left_boundary
        and line.left < split_x
        and line not in buyer_headers
        and line not in seller_headers
    ]
    seller_lines = [
        line
        for line in lines
        if top <= line.center_y <= bottom
        and line.left >= split_x
        and line not in seller_headers
    ]
    buyer_text = _positioned_text(buyer_lines)
    seller_text = _positioned_text(seller_lines)
    buyer_name, buyer_tax_id = _party(buyer_text)
    seller_name, seller_tax_id = _party(seller_text)
    return buyer_name, buyer_tax_id, seller_name, seller_tax_id


def _positioned_text(lines: list[OcrLine]) -> str:
    """Join positioned spans by visual row, then from left to right."""
    rows: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda item: (item.center_y, item.left)):
        target = next(
            (
                row
                for row in rows
                if abs(sum(item.center_y for item in row) / len(row) - line.center_y) <= 8
            ),
            None,
        )
        if target is None:
            rows.append([line])
        else:
            target.append(line)
    return "\n".join(
        "".join(_normalize(line.text) for line in sorted(row, key=lambda item: item.left))
        for row in rows
    )


def _money(text: str, patterns: list[str]) -> float | None:
    for label in patterns:
        match = re.search(label + r".{0,45}?" + MONEY_RE, text, re.I | re.S)
        if match:
            try:
                return float(match.group(1).replace(",", "").replace("，", ""))
            except ValueError:
                continue
    return None


def _parse_date(text: str) -> date | None:
    for pattern in (
        r"开票日期\s*[:：]?\s*(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?",
        r"开票日期\s*[:：]?\s*(20\d{2})(\d{2})(\d{2})",
    ):
        match = re.search(pattern, text)
        if match:
            try:
                return date(*(int(part) for part in match.groups()))
            except ValueError:
                return None
    return None


def _invoice_type(text: str) -> str:
    compact = _compact(text)
    candidates = (
        "增值税电子专用发票",
        "增值税专用发票",
        "增值税电子普通发票",
        "增值税普通发票",
        "电子发票(增值税专用发票)",
        "电子发票(普通发票)",
        "机动车销售统一发票",
        "二手车销售统一发票",
        "通用机打发票",
        "航空运输电子客票行程单",
        "铁路电子客票",
    )
    for candidate in candidates:
        if _compact(candidate) in compact:
            return candidate
    if "发票" in compact:
        return "其他发票"
    return ""


def _line_after_label(text: str, label: str, max_len: int = 200) -> str:
    match = re.search(rf"{label}\s*[:：]?\s*([^\n]{{1,{max_len}}})", text, re.I)
    return match.group(1).strip() if match else ""


def _item_names(text: str) -> str:
    items: list[str] = []
    for match in re.finditer(r"(?:^|\s)(\*[^*\n]{1,30}\*[^\n¥￥]{1,60})", text, re.M):
        item = re.sub(r"\s+", " ", match.group(1)).strip()
        if item not in items:
            items.append(item)
    if not items:
        value = _first(text, [r"(?:货物或应税劳务、服务名称|项目名称)\s*([^\n]{2,120})"])
        value = re.split(r"(?:规格型号|单位|数量|单价|金额|税率)", value)[0].strip()
        if value:
            items.append(value)
    return "；".join(items[:8])


def parse_invoice_text(
    text: str,
    *,
    source_file: str = "",
    page_number: int = 1,
    recognition_method: str = "",
    ocr_confidence: float | None = None,
    ocr_lines: list[OcrLine] | None = None,
) -> InvoiceRecord:
    text = _normalize(text)
    compact = _compact(text)
    record = InvoiceRecord(
        source_file=source_file,
        page_number=page_number,
        recognition_method=recognition_method,
        ocr_confidence=ocr_confidence,
        raw_text=text,
    )
    record.invoice_type = _invoice_type(text)
    record.invoice_code = _first(
        compact,
        [r"发票代码[:：]?([0-9]{10,12})(?!\d)", r"机器编号[:：]?[0-9]{8,16}发票代码[:：]?([0-9]{10,12})"],
        re.I,
    )
    record.invoice_number = _first(
        compact,
        [
            r"发票号码[:：]?([0-9]{8,20})(?!\d)",
            r"票据号码[:：]?([0-9]{8,20})(?!\d)",
            r"No\.?[:：]?([0-9]{8,20})(?!\d)",
        ],
        re.I,
    )
    record.invoice_date = _parse_date(text)

    buyer_section = _section(
        text,
        ("购买方信息", "购买方", "购方信息"),
        ("销售方信息", "销售方", "项目名称", "货物或应税劳务"),
    )
    seller_section = _section(
        text,
        ("销售方信息", "销售方", "销方信息"),
        ("备注", "收款人", "复核", "开票人"),
    )
    record.buyer_name, record.buyer_tax_id = _party(buyer_section)
    record.seller_name, record.seller_tax_id = _party(seller_section)

    # Some electronic invoices expose explicit flattened labels instead of sections.
    if not record.buyer_name:
        record.buyer_name = _clean_party(_first(text, [r"购买方名称\s*[:：]?\s*([^\n]{2,100})"]))
    if not record.seller_name:
        record.seller_name = _clean_party(_first(text, [r"销售方名称\s*[:：]?\s*([^\n]{2,100})"]))
    if not record.buyer_tax_id:
        record.buyer_tax_id = _first(compact, [rf"购买方(?:信息)?[^销售方]{{0,120}}?(?:纳税人识别号|统一社会信用代码|税号)[:：]?{TAX_ID_RE}"], re.I)
    if not record.seller_tax_id:
        record.seller_tax_id = _first(compact, [rf"销售方(?:信息)?.{{0,160}}?(?:纳税人识别号|统一社会信用代码|税号)[:：]?{TAX_ID_RE}"], re.I)

    if ocr_lines:
        buyer_name, buyer_tax_id, seller_name, seller_tax_id = _party_from_layout(ocr_lines)
        if buyer_name:
            record.buyer_name = buyer_name
        if buyer_tax_id:
            record.buyer_tax_id = buyer_tax_id
        if seller_name:
            record.seller_name = seller_name
        if seller_tax_id:
            record.seller_tax_id = seller_tax_id

    record.total_amount = _money(
        text,
        [r"价税合计(?:\s*\(小写\))?", r"小写\s*[:：]", r"合计金额\s*[:：]"],
    )
    record.amount_without_tax = _money(
        text,
        [r"不含税金额\s*[:：]", r"金额合计\s*[:：]", r"合\s*计(?!.*价税)"],
    )
    record.tax_amount = _money(
        text,
        [r"税额合计\s*[:：]", r"合计税额\s*[:：]", r"税额\s*[:：]"],
    )
    if record.total_amount is not None and record.amount_without_tax is not None and record.tax_amount is None:
        record.tax_amount = round(record.total_amount - record.amount_without_tax, 2)
    if record.total_amount is not None and record.tax_amount is not None and record.amount_without_tax is None:
        record.amount_without_tax = round(record.total_amount - record.tax_amount, 2)
    if record.total_amount is None and record.amount_without_tax is not None and record.tax_amount is not None:
        record.total_amount = round(record.amount_without_tax + record.tax_amount, 2)

    rates = []
    for value in re.findall(r"(?<!\d)(\d{1,2}(?:\.\d+)?)\s*%", text):
        rate = f"{value}%"
        if rate not in rates:
            rates.append(rate)
    if "免税" in text and "免税" not in rates:
        rates.append("免税")
    record.tax_rate = "、".join(rates[:5])
    record.item_name = _item_names(text)
    record.remarks = _line_after_label(text, "备注")
    record.payee = _first(text, [r"收款人\s*[:：]\s*([^\s:：]{1,20})"])
    record.reviewer = _first(text, [r"复核(?:人)?\s*[:：]\s*([^\s:：]{1,20})"])
    record.issuer = _first(text, [r"开票人\s*[:：]\s*([^\s:：]{1,20})"])

    missing: list[str] = []
    if not record.invoice_number:
        missing.append("发票号码")
    if not record.invoice_date:
        missing.append("开票日期")
    if not record.seller_name:
        missing.append("销售方名称")
    if record.total_amount is None:
        missing.append("价税合计")
    if not text:
        record.status = "失败"
        record.issues = "未提取到文字"
    elif missing:
        record.status = "需复核"
        record.issues = "缺少：" + "、".join(missing)
    return record


def looks_like_invoice(text: str) -> bool:
    compact = _compact(_normalize(text))
    signals = ("发票", "发票号码", "开票日期", "购买方", "销售方", "价税合计")
    return sum(signal in compact for signal in signals) >= 2
