"""Read the SSO dental reports (ทันตกรรม ประกันสังคม) downloaded as Excel.

REPORT_01 "รายงานการส่งเบิกค่าบริการทางการแพทย์ทันตกรรม..." (submission):
  ลำดับ | เลขประจำตัวประชาชน | ชื่อ-นามสกุล | วันที่ใช้สิทธิ | วันที่ส่งเบิก | ประเภทค่าบริการ | สถานะรับแจ้ง | จำนวนเงินที่ขอเบิก
REPORT_02 "รายงานการจ่ายค่าบริการทางการแพทย์ทันตกรรม..." (payment):
  ลำดับ | เลขประจำตัวประชาชน | ชื่อ-นามสกุล | วันที่ใช้สิทธิ | วันที่ส่งเบิก | ประเภทค่าบริการ | จำนวนเงินที่ขอเบิก |
  จำนวนเงินจ่าย | เลขที่ใบสำคัญรับเงิน | วันที่โอนเข้าบัญชี
Rows are found by their header names, so a moved column still reads. The patient's name is not kept.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import openpyxl

from .repstm import thai_date

SUBMISSION, PAYMENT = "submission", "payment"


class DentalError(ValueError):
    """The file is not a dental report this program can read."""


@dataclass
class DentalRow:
    cid: str
    service_date: str
    claim_date: str
    service_type: str
    claim_amount: str
    status: str = ""
    paid_amount: str = ""
    voucher: str = ""
    transfer_date: str = ""
    seq: int = 1  # the nth claim with the same CID, day, type and amount in this report


@dataclass
class DentalReport:
    kind: str
    hcode: str = ""
    period_start: str = ""
    period_end: str = ""
    total: str = "0"
    rows: list[DentalRow] = field(default_factory=list)


def _clean(value) -> str:
    return re.sub(r"\s+", " ", "" if value is None else str(value)).strip()


def _key(header: str) -> str:
    return re.sub(r"\s+", "", header)


def _money(value) -> str:
    text = _clean(value).replace(",", "")
    try:
        return f"{float(text):.2f}" if text else ""
    except ValueError:
        return ""


COLUMNS = {
    "cid": "เลขประจำตัวประชาชน", "service_date": "วันที่ใช้สิทธิ", "claim_date": "วันที่ส่งเบิก",
    "service_type": "ประเภทค่าบริการ", "status": "สถานะรับแจ้ง", "claim_amount": "จำนวนเงินที่ขอเบิก",
    "paid_amount": "จำนวนเงินจ่าย", "voucher": "เลขที่ใบสำคัญรับเงิน", "transfer_date": "วันที่โอนเข้าบัญชี",
}


def parse_report(data: bytes) -> DentalReport:
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise DentalError("เปิดไฟล์ไม่ได้ กรุณาใช้ไฟล์ .xlsx ที่ดาวน์โหลดจากระบบ") from None
    try:
        rows = [tuple(_clean(v) for v in r) for r in book.worksheets[0].iter_rows(values_only=True)]
    finally:
        book.close()
    title = next((r[0] for r in rows if r and r[0]), "")
    if "ทันตกรรม" not in title:
        raise DentalError("ไม่ใช่รายงานทันตกรรมประกันสังคม")
    if "การจ่าย" in title:
        kind = PAYMENT
    elif "การส่งเบิก" in title:
        kind = SUBMISSION
    else:
        raise DentalError("ไม่รู้จักชนิดรายงาน (ต้องเป็นรายงานการส่งเบิก หรือรายงานการจ่าย)")
    report = DentalReport(kind=kind)
    for row in rows[:8]:
        line = row[0] if row else ""
        if match := re.search(r"รหัสสถานพยาบาล.*?:\s*(\d{5})", line):
            report.hcode = match.group(1)
        if match := re.search(r"ตั้งแต่วันที่\s+(.+?)\s+ถึงวันที่\s+(.+)$", line):
            report.period_start, report.period_end = thai_date(match.group(1)), thai_date(match.group(2))
    header_at = next((i for i, r in enumerate(rows) if r and r[0] == "ลำดับ"), None)
    if header_at is None:
        raise DentalError("ไม่พบหัวตาราง (ลำดับ, เลขประจำตัวประชาชน, ...)")
    names = [_key(h) for h in rows[header_at]]
    where = {field_: names.index(_key(label)) for field_, label in COLUMNS.items() if _key(label) in names}
    need = {"cid", "service_date", "claim_amount"} | ({"paid_amount"} if kind == PAYMENT else {"status"})
    if missing := need - set(where):
        raise DentalError("ไม่พบคอลัมน์: " + ", ".join(COLUMNS[m] for m in sorted(missing)))
    get = lambda r, f: r[where[f]] if f in where and where[f] < len(r) else ""
    for row in rows[header_at + 1:]:
        if not row or not row[0]:
            continue
        if not row[0].isdigit():
            if row[0].startswith("รวม"):
                report.total = next((_money(v) for v in row[1:] if _money(v)), "0")
            continue
        cid = re.sub(r"\D", "", get(row, "cid"))
        service = thai_date(get(row, "service_date"))
        if len(cid) != 13 or not service:
            raise DentalError(f"แถวลำดับ {row[0]}: เลขประจำตัวหรือวันที่ใช้สิทธิไม่ถูกต้อง")
        report.rows.append(DentalRow(
            cid=cid, service_date=service, claim_date=thai_date(get(row, "claim_date")),
            service_type=get(row, "service_type"), claim_amount=_money(get(row, "claim_amount")) or "0.00",
            status=get(row, "status"), paid_amount=_money(get(row, "paid_amount")),
            voucher=get(row, "voucher"), transfer_date=thai_date(get(row, "transfer_date")),
        ))
    if not report.rows:
        raise DentalError("ไม่พบรายการในรายงาน")
    # One person may have several claims on one day; the claimed amount and its order keep them apart.
    seen: dict[tuple, int] = {}
    for row in report.rows:
        key = (row.cid, row.service_date, row.service_type, row.claim_amount)
        seen[key] = row.seq = seen.get(key, 0) + 1
    return report
