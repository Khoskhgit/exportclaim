"""The DM Remission registry kept in the e-Claim program ("รายงาน การลงทะเบียนผู้ป่วย T1DM รายใหม่", T1DM_report.xls).

The report is an Excel sheet with a title, then a header row: RUN NUMBER, ปีงบประมาณ, PID, ชื่อ-นามสกุล, สิทธิ, เขต,
จังหวัด, หน่วยบริการลงทะเบียน, ชนิดการลงทะเบียน (T2DM), วันที่ลงทะเบียน, วันที่เข้ารับบริการ, สถานะ (Y), วัน expire date,
เหตุผล. Dates are dd/mm/yyyy. One row per registration (RUN NUMBER); a patient may have more than one.
Names are not kept. Importing a newer report updates the rows by RUN NUMBER.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from .store import Store
from .validate import text

COLUMNS = {
    "RUN NUMBER": "run_no", "ปีงบประมาณ": "fiscal_year", "PID": "pid", "สิทธิ": "inscl", "หน่วยบริการลงทะเบียน": "unit",
    "ชนิดการลงทะเบียน": "kind", "วันที่ลงทะเบียน": "reg_date", "วันที่เข้ารับบริการ": "visit_date", "สถานะ": "status",
    "วัน expire date": "expire_date", "เหตุผล": "reason",
}
FIELDS = ("run_no", "fiscal_year", "pid", "inscl", "unit", "kind", "reg_date", "visit_date", "status", "expire_date", "reason")


class RegistryError(ValueError):
    pass


@dataclass
class Registration:
    run_no: str
    fiscal_year: str
    pid: str
    inscl: str
    unit: str
    kind: str
    reg_date: str
    visit_date: str
    status: str
    expire_date: str
    reason: str


def iso(value) -> str:
    """dd/mm/yyyy (Christian or Buddhist year), an Excel date or a datetime to yyyy-mm-dd; '' when empty."""
    if isinstance(value, (date, datetime)):
        return (value.date() if isinstance(value, datetime) else value).isoformat()
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text(value))
    if not match:
        return ""
    d, m, y = (int(g) for g in match.groups())
    if y > 2400:
        y -= 543
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return ""


def _cell(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return text(value)


def _rows(name: str, data: bytes) -> list[list]:
    lower = name.lower()
    if lower.endswith(".xlsx"):
        from openpyxl import load_workbook
        sheet = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
        return [list(r) for r in sheet.iter_rows(values_only=True)]
    if lower.endswith(".xls"):
        import xlrd
        try:
            book = xlrd.open_workbook(file_contents=data)
        except xlrd.XLRDError as exc:
            raise RegistryError(f"เปิดไฟล์ Excel ไม่ได้: {exc}") from None
        sheet = book.sheet_by_index(0)
        out = []
        for r in range(sheet.nrows):
            row = []
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    row.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
                else:
                    row.append(cell.value)
            out.append(row)
        return out
    raise RegistryError("รับเฉพาะไฟล์ .xls หรือ .xlsx ที่ดาวน์โหลดจากรายงานการลงทะเบียนในโปรแกรม e-Claim")


def parse(name: str, data: bytes) -> list[Registration]:
    rows = _rows(name, data)
    header_at = next((i for i, r in enumerate(rows) if any(_cell(v).upper() == "PID" for v in r)), None)
    if header_at is None:
        raise RegistryError("ไม่พบหัวตาราง PID ไม่ใช่รายงานการลงทะเบียนจาก e-Claim")
    header = [_cell(v) for v in rows[header_at]]
    index = {COLUMNS[h]: i for i, h in enumerate(header) if h in COLUMNS}
    if not {"run_no", "pid", "reg_date"} <= set(index):
        raise RegistryError("ไฟล์ไม่มีคอลัมน์ RUN NUMBER, PID หรือ วันที่ลงทะเบียน")
    out: list[Registration] = []
    for r in rows[header_at + 1:]:
        get = lambda key: r[index[key]] if key in index and index[key] < len(r) else ""
        pid = re.sub(r"\D", "", _cell(get("pid")))
        if len(pid) != 13:
            continue
        out.append(Registration(
            run_no=_cell(get("run_no")), fiscal_year=_cell(get("fiscal_year")), pid=pid, inscl=_cell(get("inscl")),
            unit=_cell(get("unit")), kind=_cell(get("kind")), reg_date=iso(get("reg_date")), visit_date=iso(get("visit_date")),
            status=_cell(get("status")).upper(), expire_date=iso(get("expire_date")), reason=_cell(get("reason"))))
    if not out:
        raise RegistryError("ไม่พบรายการลงทะเบียนในไฟล์")
    return out


SCHEMA = """
CREATE TABLE IF NOT EXISTS dmrem_uploads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, name TEXT NOT NULL, row_count INTEGER NOT NULL,
  created_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS dmrem_registry (
  run_no TEXT PRIMARY KEY, fiscal_year TEXT NOT NULL DEFAULT '', pid TEXT NOT NULL, inscl TEXT NOT NULL DEFAULT '',
  unit TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT '', reg_date TEXT NOT NULL DEFAULT '',
  visit_date TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', expire_date TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '', upload_id INTEGER, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS dmrem_registry_pid ON dmrem_registry(pid);
"""


class RegistryStore:
    def __init__(self, store: Store):
        self._db = store._db
        with self._db() as db:
            db.executescript(SCHEMA)

    def save(self, rows: list[Registration], name: str, created_by: str = "") -> dict:
        at = datetime.now().isoformat(timespec="seconds")
        added = updated = 0
        with self._db() as db:
            upload = db.execute("INSERT INTO dmrem_uploads (at, name, row_count, created_by) VALUES (?,?,?,?)",
                                (at, name, len(rows), created_by)).lastrowid
            for r in rows:
                values = [getattr(r, f) for f in FIELDS]
                if db.execute(f"UPDATE dmrem_registry SET {', '.join(f'{f}=?' for f in FIELDS[1:])}, upload_id=?, updated_at=? "
                              "WHERE run_no=?", (*values[1:], upload, at, r.run_no)).rowcount:
                    updated += 1
                else:
                    db.execute(f"INSERT INTO dmrem_registry ({', '.join(FIELDS)}, upload_id, updated_at) "
                               f"VALUES ({','.join('?' * (len(FIELDS) + 2))})", (*values, upload, at))
                    added += 1
        return {"added": added, "updated": updated, "upload_id": upload}

    def by_pid(self, pids: Iterable[str] | None = None) -> dict[str, list[dict]]:
        """PID -> its registrations, the earliest first."""
        out: dict[str, list[dict]] = {}
        with self._db() as db:
            if pids is None:
                found = db.execute("SELECT * FROM dmrem_registry ORDER BY reg_date")
                for r in found:
                    out.setdefault(r["pid"], []).append(dict(r))
            else:
                wanted = sorted(set(pids))
                for start in range(0, len(wanted), 500):
                    part = wanted[start:start + 500]
                    for r in db.execute(f"SELECT * FROM dmrem_registry WHERE pid IN ({','.join('?' * len(part))}) "
                                        "ORDER BY reg_date", part):
                        out.setdefault(r["pid"], []).append(dict(r))
        return out

    def summary(self) -> dict:
        with self._db() as db:
            row = db.execute("SELECT COUNT(*) n, COUNT(DISTINCT pid) people, MIN(reg_date) first, MAX(reg_date) last "
                             "FROM dmrem_registry").fetchone()
            upload = db.execute("SELECT at, name, row_count, created_by FROM dmrem_uploads ORDER BY id DESC LIMIT 1").fetchone()
        return {**dict(row), "last_upload": dict(upload) if upload else None}


def active(registrations: list[dict], day: date) -> dict | None:
    """The registration in force on a day: status Y, registered on or before it, not expired."""
    for r in registrations:
        if r["status"] != "Y" or not r["reg_date"]:
            continue
        if r["reg_date"] > day.isoformat():
            continue
        if r["expire_date"] and r["expire_date"] < day.isoformat():
            continue
        return r
    return None
