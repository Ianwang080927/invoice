from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path


@dataclass(slots=True, frozen=True)
class OcrLine:
    text: str
    left: float
    top: float
    right: float
    bottom: float
    confidence: float | None = None

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass(slots=True)
class InvoiceRecord:
    invoice_type: str = ""
    invoice_code: str = ""
    invoice_number: str = ""
    invoice_date: date | None = None
    buyer_name: str = ""
    buyer_tax_id: str = ""
    seller_name: str = ""
    seller_tax_id: str = ""
    amount_without_tax: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None
    tax_rate: str = ""
    item_name: str = ""
    remarks: str = ""
    payee: str = ""
    reviewer: str = ""
    issuer: str = ""
    source_file: str = ""
    page_number: int = 1
    recognition_method: str = ""
    ocr_confidence: float | None = None
    status: str = "成功"
    issues: str = ""
    raw_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def source_name(self) -> str:
        return Path(self.source_file).name
