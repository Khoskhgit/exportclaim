"""Read the SSO disability reports (ค่าบริการทางการแพทย์ผู้ทุพพลภาพ ผู้ป่วยนอก/ใน) downloaded as Excel.

RE_DISABLEDOPD_SEND "รายงานการส่งเบิกค่าบริการทางการแพทย์กรณีค่าบริการทางการแพทย์ผู้ทุพพลภาพ (ผู้ป่วยนอก/ใน)":
  ลำดับ | เลขประจำตัวประชาชน | ชื่อ-นามสกุล | Admit Date | Discharge Date | วันที่ส่งเบิก | ประเภท | สถานะ |
  จำนวนเงินรวมที่ขอเบิก
A payment report ("รายงานการจ่าย…ทุพพลภาพ") is read the same way when it has a paid-amount column. Columns are found
by their header names, so a moved column still reads. Dates are dd/mm/yyyy (Buddhist year) or "1 ตุลาคม 2569".
The patient's name is not kept.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date

import openpyxl

from .repstm import thai_date

SUBMISSION, PAYMENT = "submission", "payment"
OPD, IPD = "OPD", "IPD"


class DisabledError(ValueError):
    """The file is not a disability report this program can read."""


@dataclass
class DisabledRow:
    cid: str
    admit_date: str
    discharge_date: str
    kind: str  # OPD / IPD
    claim_amount: str
    claim_date: str = ""
    status: str = ""
    paid_amount: str = ""
    voucher: str = ""
    transfer_date: str = ""
    seq: int = 1  # the nth claim with the same CID, admit date, kind and amount in this report


@dataclass
class DisabledReport:
    kind: str
    hcode: str = ""
    period_start: str = ""
    period_end: str = ""
    total: str = "0"
    rows: list[DisabledRow] = field(default_factory=list)


def _clean(value) -> str:
    return re.sub(r"\s+", " ", "" if value is None else str(value)).strip()


def _key(header: str) -> str:
    return re.sub(r"\s+", "", header).lower()


def _money(value) -> str:
    text = _clean(value).replace(",", "")
    try:
        return f"{float(text):.2f}" if text else ""
    except ValueError:
        return ""


def day(value) -> str:
    """'01/10/2569' or '1 ตุลาคม 2569' -> '2026-10-01' (a year below 2400 is taken as A.D.)."""
    text = _clean(value)
    if match := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text):
        d, m, y = (int(x) for x in match.groups())
        try:
            return date(y - 543 if y > 2400 else y, m, d).isoformat()
        except ValueError:
            return ""
    return thai_date(text)


# field -> header names it may carry
COLUMNS = {
    "cid": ("เลขประจำตัวประชาชน",), "admit_date": ("AdmitDate", "วันที่เข้ารับบริการ", "วันที่ใช้สิทธิ"),
    "discharge_date": ("DischargeDate", "วันที่จำหน่าย"), "claim_date": ("วันที่ส่งเบิก",), "kind": ("ประเภท",),
    "status": ("สถานะ", "สถานะรับแจ้ง"), "claim_amount": ("จำนวนเงินรวมที่ขอเบิก", "จำนวนเงินที่ขอเบิก"),
    "paid_amount": ("จำนวนเงินจ่าย", "จำนวนเงินที่จ่าย", "จำนวนเงินรวมที่จ่าย"),
    "voucher": ("เลขที่ใบสำคัญรับเงิน",), "transfer_date": ("วันที่โอนเข้าบัญชี", "วันที่โอน"),
}
LABELS = {"cid": "เลขประจำตัวประชาชน", "admit_date": "Admit Date", "claim_amount": "จำนวนเงินรวมที่ขอเบิก",
          "paid_amount": "จำนวนเงินจ่าย"}


def _kind(value: str) -> str:
    return IPD if "ใน" in value or value.upper().startswith("IP") else OPD


def parse_report(data: bytes) -> DisabledReport:
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise DisabledError("เปิดไฟล์ไม่ได้ กรุณาใช้ไฟล์ .xlsx ที่ดาวน์โหลดจากระบบ") from None
    try:
        rows = [tuple(_clean(v) for v in r) for r in book.worksheets[0].iter_rows(values_only=True)]
    finally:
        book.close()
    lines = [" ".join(v for v in r if v) for r in rows[:12]]
    title = next((x for x in lines if "รายงาน" in x), "")
    if "ทุพพลภาพ" not in title:
        raise DisabledError("ไม่ใช่รายงานค่าบริการทางการแพทย์ผู้ทุพพลภาพ")
    if "การจ่าย" in title:
        kind = PAYMENT
    elif "การส่งเบิก" in title:
        kind = SUBMISSION
    else:
        raise DisabledError("ไม่รู้จักชนิดรายงาน (ต้องเป็นรายงานการส่งเบิก หรือรายงานการจ่าย)")
    report = DisabledReport(kind=kind)
    for line in lines:
        if match := re.search(r"รหัสสถานพยาบาล\D*(\d{5,9})", line):
            digits = match.group(1)  # 001086600 -> 10866
            report.hcode = digits if len(digits) == 5 else digits[2:7]
        if match := re.search(r"ตั้งแต่วันที่\s+(.+?)\s+ถึงวันที่\s+(\S+(?:\s+\S+\s+\d{4})?)", line):
            report.period_start, report.period_end = day(match.group(1)), day(match.group(2))
    header_at = next((i for i, r in enumerate(rows) if r and r[0] == "ลำดับ"), None)
    if header_at is None:
        raise DisabledError("ไม่พบหัวตาราง (ลำดับ, เลขประจำตัวประชาชน, ...)")
    names = [_key(h) for h in rows[header_at]]
    where = {}
    for field_, labels in COLUMNS.items():
        for label in labels:
            if _key(label) in names:
                where[field_] = names.index(_key(label))
                break
    need = {"cid", "admit_date", "claim_amount"} | ({"paid_amount"} if kind == PAYMENT else set())
    if missing := need - set(where):
        raise DisabledError("ไม่พบคอลัมน์: " + ", ".join(LABELS[m] for m in sorted(missing)))
    get = lambda r, f: r[where[f]] if f in where and where[f] < len(r) else ""
    for row in rows[header_at + 1:]:
        if not row or not row[0]:
            continue
        if not row[0].isdigit():
            if row[0].startswith("รวม"):
                report.total = next((_money(v) for v in row[1:] if _money(v)), "0")
            continue
        cid = re.sub(r"\D", "", get(row, "cid"))
        admit = day(get(row, "admit_date"))
        if len(cid) != 13 or not admit:
            raise DisabledError(f"แถวลำดับ {row[0]}: เลขประจำตัวหรือ Admit Date ไม่ถูกต้อง")
        report.rows.append(DisabledRow(
            cid=cid, admit_date=admit, discharge_date=day(get(row, "discharge_date")) or admit,
            kind=_kind(get(row, "kind")), claim_amount=_money(get(row, "claim_amount")) or "0.00",
            claim_date=day(get(row, "claim_date")), status=get(row, "status"), paid_amount=_money(get(row, "paid_amount")),
            voucher=get(row, "voucher"), transfer_date=day(get(row, "transfer_date")),
        ))
    if not report.rows:
        raise DisabledError("ไม่พบรายการในรายงาน")
    seen: dict[tuple, int] = {}
    for row in report.rows:
        key = (row.cid, row.admit_date, row.kind, row.claim_amount)
        seen[key] = row.seq = seen.get(key, 0) + 1
    return report
