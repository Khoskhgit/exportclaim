"""Read the Krungthai Health Care (เบิกจ่ายตรงข้าราชการ) daily file and tie each Approve Code to its HOSxP visit.

The bank's download (DWN_HOSPITAL_<hcode>_<time>) holds HCG<hcode>_<merchant>_TXT001_<yyyymmdd>.txt and the PDF
reports HCD001-HCD004 of the same transactions. Only the TXT is read: UTF-8, one transaction per line, fields split by |.

  0 EA00<hcode>  1 company (HCG<hcode>)  2 company name  3 merchant no  4 merchant name  5 B  6 terminal id
  7 transaction date (dd/mm/yyyy)  8 transaction time  9 post date  10 post time  11 personal id (the patient)
  12-13 patient name  14 REF PID  15 PID CUS  16-17 PID CUS name  18-19 codes  20 birth date  21 date
  22 amount (refunds negative)  23 batch  24 trace  25 Payment / Refund  26 APPR. CODE  27 Trans Ref ID  28 user id
  29 menu  30 reason for keying in by hand  31 APPR. CODE of the old payment  35 channel (TMS = EDC, CB-IBANK = keyed in)

The same day's transactions can also be downloaded from the Krungthai web as healthcare_online_dd-mm-yyyy.csv
(TIS-620, quoted, one header row): No, Terminal ID, Post Date, Post Time, Transaction Date, Transaction Time,
Personal ID, Personal Name, REF PID, PID CUS, Menu, Paid Type, Trans Amount (1,140.50), Channel (EDC / CB-IBANK), …,
Trans Ref ID, TXNS Code (Payment / Refund / Void), App. Code, User ID (…@00000<hcode> when keyed in), Reason,
Old Approvel Code, Reason Other. A Void cancels the payment with the same Trans Ref ID, as a refund does.
Both files give the same Approve Code and Trans Ref ID, so importing the CSV and later the TXT001 does not double up.

Names, birth dates and the other people's IDs are not kept.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime

PAYMENT, REFUND, VOID = "PAYMENT", "REFUND", "VOID"
MIN_FIELDS = 36


class ApproveError(ValueError):
    """The file is not a Krungthai Health Care transaction file this program can read."""


@dataclass
class Approval:
    appr_code: str
    trans_ref: str
    kind: str
    cid: str
    txn_date: str
    txn_time: str
    post_date: str
    post_time: str
    amount: str
    channel: str = ""
    terminal: str = ""
    reason: str = ""
    appr_old: str = ""


@dataclass
class ApproveFile:
    hcode: str = ""
    file_date: str = ""
    rows: list[Approval] = field(default_factory=list)

    @property
    def total(self) -> str:
        return f"{sum(float(r.amount) for r in self.rows):.2f}"


def iso_date(value: str) -> str:
    """dd/mm/yyyy or dd-mm-yyyy (Christian or Buddhist year) to yyyy-mm-dd; '' when it is not a date."""
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", (value or "").strip())
    if not match:
        return ""
    day, month, year = (int(g) for g in match.groups())
    if year > 2400:
        year -= 543
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _time(value: str) -> str:
    value = (value or "").strip()
    return value if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", value) else ""


def _money(value: str) -> str:
    try:
        return f"{float((value or '').replace(',', '')):.2f}"
    except ValueError:
        return ""


def parse_txt(data: bytes, name: str = "") -> ApproveFile:
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = data.decode("cp874", errors="replace")
    out = ApproveFile()
    if match := re.search(r"TXT\d*_(\d{8})", name):
        try:
            out.file_date = datetime.strptime(match.group(1), "%Y%m%d").date().isoformat()
        except ValueError:
            pass
    for number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        f = [p.strip() for p in line.split("|")]
        if len(f) < MIN_FIELDS or not f[1].startswith("HCG"):
            raise ApproveError(f"บรรทัด {number}: ไม่ใช่รูปแบบไฟล์ TXT ของกรุงไทย (Health Care)")
        hcode = f[1][3:]
        if out.hcode and hcode != out.hcode:
            raise ApproveError(f"บรรทัด {number}: รหัสสถานพยาบาลไม่ตรงกับบรรทัดแรก")
        out.hcode = hcode
        kind = f[25].upper()
        cid = re.sub(r"\D", "", f[11])
        amount = _money(f[22])
        if kind not in (PAYMENT, REFUND):
            raise ApproveError(f"บรรทัด {number}: ไม่รู้จักชนิดรายการ {f[25]!r}")
        if not f[26] or len(cid) != 13 or not iso_date(f[7]) or not amount:
            raise ApproveError(f"บรรทัด {number}: Approve Code, เลขบัตร, วันที่ หรือยอดเงินไม่ถูกต้อง")
        out.rows.append(Approval(
            appr_code=f[26], trans_ref=f[27], kind=kind, cid=cid, txn_date=iso_date(f[7]), txn_time=_time(f[8]),
            post_date=iso_date(f[9]), post_time=_time(f[10]), amount=amount, channel=f[35], terminal=f[6],
            reason=f[30], appr_old=f[31] if f[31].isdigit() else "",
        ))
    if not out.rows:
        raise ApproveError("ไม่พบรายการในไฟล์")
    return out


CSV_NEEDED = ("Transaction Date", "Personal ID", "Trans Amount", "Trans Ref ID", "TXNS Code", "App. Code")


def parse_csv(data: bytes, name: str = "") -> ApproveFile:
    """The online report healthcare_online_dd-mm-yyyy.csv from the Krungthai web."""
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = data.decode("cp874", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    header = [h.strip() for h in reader.fieldnames or []]
    if any(h not in header for h in CSV_NEEDED):
        raise ApproveError("ไม่ใช่ไฟล์ CSV รายการ Health Care ของกรุงไทย (healthcare_online_….csv)")
    reader.fieldnames = header
    out = ApproveFile()
    if match := re.search(r"(\d{2}-\d{2}-\d{4})", name):
        out.file_date = iso_date(match.group(1))
    for number, raw in enumerate(reader, 2):
        f = {k: (v or "").strip() for k, v in raw.items() if k}
        if not any(f.values()):
            continue
        kind = f["TXNS Code"].upper()
        cid = re.sub(r"\D", "", f["Personal ID"])
        amount = _money(f["Trans Amount"])
        if kind not in (PAYMENT, REFUND, VOID):
            raise ApproveError(f"บรรทัด {number}: ไม่รู้จักชนิดรายการ {f['TXNS Code']!r}")
        if not f["App. Code"] or len(cid) != 13 or not iso_date(f["Transaction Date"]) or not amount:
            raise ApproveError(f"บรรทัด {number}: Approve Code, เลขบัตร, วันที่ หรือยอดเงินไม่ถูกต้อง")
        if kind != PAYMENT and not amount.startswith("-"):
            amount = f"-{amount}"  # a void or refund takes money back, as the TXT shows it
        if match := re.search(r"@0*(\d{5})$", f.get("User ID", "")):
            if out.hcode and match.group(1) != out.hcode:
                raise ApproveError(f"บรรทัด {number}: รหัสสถานพยาบาลไม่ตรงกับบรรทัดก่อน")
            out.hcode = match.group(1)
        old = f.get("Old Approvel Code", "") or f.get("Old Approval Code", "")
        channel = f.get("Channel", "")
        out.rows.append(Approval(
            appr_code=f["App. Code"], trans_ref=f["Trans Ref ID"], kind=kind, cid=cid,
            txn_date=iso_date(f["Transaction Date"]), txn_time=_time(f.get("Transaction Time", "")),
            post_date=iso_date(f.get("Post Date", "")), post_time=_time(f.get("Post Time", "")), amount=amount,
            channel="TMS" if channel == "EDC" else channel, terminal=f.get("Terminal ID", ""),
            reason=" ".join(v for v in (f.get("Reason", ""), f.get("Reason Other", "")) if v),
            appr_old=old if old.isdigit() else "",
        ))
    if not out.rows:
        raise ApproveError("ไม่พบรายการในไฟล์")
    return out


def parse_upload(name: str, data: bytes) -> tuple[list[tuple[str, ApproveFile]], list[str]]:
    """A TXT, or a ZIP of the bank's download folder. Returns the files read and the names that were skipped."""
    lower = name.lower()
    if lower.endswith(".zip"):
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ApproveError("เปิดไฟล์ ZIP ไม่ได้") from None
        found, skipped = [], []
        with archive:
            for info in archive.infolist():
                member = info.filename.rsplit("/", 1)[-1]
                if info.is_dir() or not member:
                    continue
                if member.lower().endswith(".txt"):
                    found.append((member, parse_txt(archive.read(info), member)))
                else:
                    skipped.append(member)
        if not found:
            raise ApproveError("ใน ZIP ไม่มีไฟล์ TXT ของกรุงไทย")
        return found, skipped
    if lower.endswith(".txt"):
        return [(name, parse_txt(data, name))], []
    if lower.endswith(".csv"):
        return [(name, parse_csv(data, name))], []
    raise ApproveError("รับเฉพาะไฟล์ .txt (TXT001), .csv (healthcare_online) หรือ .zip ของโฟลเดอร์ที่ดาวน์โหลด")


# ------------------------------------------------------------------ refunds and matching
def cancelled(rows: list[dict]) -> dict[str, str]:
    """Payments taken back by a refund or a void: payment appr_code -> refund appr_code.

    A refund (or void) names the payment by its Trans Ref ID (or the old Approve Code); failing that, it takes back the latest
    earlier payment of the same patient and amount."""
    payments = sorted((r for r in rows if r["kind"] == PAYMENT), key=lambda r: (r["txn_date"], r["txn_time"]))
    out: dict[str, str] = {}
    for refund in sorted((r for r in rows if r["kind"] in (REFUND, VOID)), key=lambda r: (r["txn_date"], r["txn_time"])):
        free = [p for p in payments if p["appr_code"] not in out]
        target = next((p for p in free if refund["appr_old"] and p["appr_code"] == refund["appr_old"]), None) \
            or next((p for p in free if refund["trans_ref"] and p["trans_ref"] == refund["trans_ref"]), None)
        if target is None:
            same = [p for p in free if p["cid"] == refund["cid"] and abs(float(p["amount"]) + float(refund["amount"])) < 0.005
                    and (p["txn_date"], p["txn_time"]) <= (refund["txn_date"], refund["txn_time"])]
            target = same[-1] if same else None
        if target is not None:
            out[target["appr_code"]] = refund["appr_code"]
    return out


def _seconds(value) -> int | None:
    text = str(value or "")
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text.split(".")[0]) if text else None
    if not match:
        return None
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + int(s or 0)


def assign(payments: list[dict], visits: list[dict], pttypes: tuple[str, ...]) -> dict[str, dict]:
    """Approve Code -> visit. A payment belongs to a visit of the same patient on its transaction date: under the
    approve right first, then the one whose cost equals the amount, then the nearest in time. A visit takes one
    payment only.

    visits: dicts with cid, vn, date (yyyy-mm-dd), time, income, pttype."""
    by_day: dict[tuple[str, str], list[dict]] = {}
    for v in visits:
        if v.get("vn"):
            by_day.setdefault((str(v["cid"]), str(v["date"])[:10]), []).append(v)
    used: set[str] = set()
    out: dict[str, dict] = {}
    for p in sorted(payments, key=lambda r: (r["txn_date"], r["txn_time"], r["appr_code"])):
        free = [v for v in by_day.get((p["cid"], p["txn_date"]), []) if str(v["vn"]) not in used]
        if not free:
            continue
        at = _seconds(p["txn_time"])

        def rank(v: dict):
            gap = _seconds(v.get("time"))
            try:
                same_amount = abs(float(v.get("income") or 0) - float(p["amount"])) < 0.005
            except ValueError:
                same_amount = False
            return (str(v.get("pttype") or "") not in pttypes, not same_amount,
                    abs(at - gap) if at is not None and gap is not None else 10 ** 6, str(v["vn"]))

        best = min(free, key=rank)
        used.add(str(best["vn"]))
        out[p["appr_code"]] = best
    return out
