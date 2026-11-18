"""FDH "รายละเอียดรายการเคลมของหน่วยบริการ" (ClaimDetail.xlsx), downloaded from the FDH web, e.g. for the STP fund.

The sheet "ClaimDetail" has a title, then a header row: ลำดับที่, รหัสการเคลม, HN, รหัสบริการ (SEQ), รหัสผู้ป่วยใน (AN),
ประเภทผู้ป่วย, วันเข้ารับบริการ, วันที่รับการรักษา, วันจำหน่ายออก, การใช้สิทธิ, วันที่ส่งเคลม, สิทธิ, สถานะรายการเคลม.
SEQ is our VN (AN for an admission). There are no names and no amounts. A row is kept by its claim ID, so importing a
newer report again updates the status. HOSxP is never written to.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Iterable

from .store import Store
from .validate import text

COLUMNS = {
    "claim_id": "รหัสการเคลม", "hn": "HN", "seq": "รหัสบริการ (SEQ)", "an": "รหัสผู้ป่วยใน (AN)", "kind": "ประเภทผู้ป่วย",
    "service_at": "วันเข้ารับบริการ", "admit_at": "วันที่รับการรักษา", "discharge_at": "วันจำหน่ายออก", "uuc": "การใช้สิทธิ",
    "sent_at": "วันที่ส่งเคลม", "fund": "สิทธิ", "status": "สถานะรายการเคลม",
}
FIELDS = list(COLUMNS)


class ClaimDetailError(ValueError):
    pass


def _norm(value) -> str:
    return re.sub(r"\s+", "", text(value))


def _when(value) -> str:
    """'02-07-2026 06:53' (or a datetime) to '2026-07-02 06:53'; '' when empty."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    match = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})(?:\s+(\d{1,2}):(\d{2}))?", text(value))
    if not match:
        return text(value)
    d, m, y, hh, mm = match.groups()
    year = int(y) - 543 if int(y) > 2400 else int(y)
    return f"{year:04d}-{int(m):02d}-{int(d):02d}" + (f" {int(hh):02d}:{mm}" if hh else "")


def is_claim_detail(name: str, data: bytes) -> bool:
    return name.lower().endswith(".xlsx") and data[:2] == b"PK"


def parse(data: bytes) -> list[dict]:
    from openpyxl import load_workbook
    try:
        book = load_workbook(io.BytesIO(data), data_only=True)  # not read_only: FDH's file has no dimension tag
    except Exception as exc:
        raise ClaimDetailError(f"เปิดไฟล์ Excel ไม่ได้: {exc.__class__.__name__}") from None
    sheet = book["ClaimDetail"] if "ClaimDetail" in book.sheetnames else book.worksheets[0]
    rows = list(sheet.iter_rows(values_only=True))
    wanted = {_norm(v): k for k, v in COLUMNS.items()}
    for at, row in enumerate(rows[:30]):
        where = {wanted[_norm(c)]: i for i, c in enumerate(row) if _norm(c) in wanted}
        if {"claim_id", "seq", "status"} <= set(where):
            break
    else:
        raise ClaimDetailError("ไม่ใช่ไฟล์รายละเอียดรายการเคลมจาก FDH (ไม่พบหัวคอลัมน์ รหัสการเคลม / รหัสบริการ (SEQ) / สถานะรายการเคลม)")
    out = []
    for row in rows[at + 1:]:
        get = lambda k: row[where[k]] if k in where and where[k] < len(row) else None
        claim_id, seq = text(get("claim_id")), text(get("seq"))
        if not claim_id or not seq:
            continue
        item = {k: text(get(k)) for k in FIELDS}
        for k in ("service_at", "admit_at", "discharge_at", "sent_at"):
            item[k] = _when(get(k))
        item["kind"] = "IPD" if item["kind"].upper().startswith("IP") or item["an"] else "OPD"
        out.append(item)
    if not out:
        raise ClaimDetailError("ไม่พบรายการเคลมในไฟล์")
    return out


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS fdh_claims (
  claim_id TEXT PRIMARY KEY, {', '.join(f"{f} TEXT NOT NULL DEFAULT ''" for f in FIELDS if f != 'claim_id')},
  file TEXT NOT NULL DEFAULT '', imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fdh_claims_seq ON fdh_claims(seq);
CREATE INDEX IF NOT EXISTS fdh_claims_an ON fdh_claims(an);
"""


class FdhClaimStore:
    def __init__(self, store: Store):
        self._db = store._db
        with self._db() as db:
            db.executescript(SCHEMA)

    def save(self, rows: list[dict], file: str) -> dict:
        at = datetime.now().isoformat(timespec="seconds")
        added = 0
        with self._db() as db:
            for r in rows:
                added += db.execute("SELECT 1 FROM fdh_claims WHERE claim_id=?", (r["claim_id"],)).fetchone() is None
                db.execute(f"INSERT OR REPLACE INTO fdh_claims ({', '.join(FIELDS)}, file, imported_at) "
                           f"VALUES ({','.join('?' * (len(FIELDS) + 2))})", (*(r[f] for f in FIELDS), file, at))
        return {"rows": len(rows), "added": added, "updated": len(rows) - added}

    def latest(self, keys: Iterable[str]) -> dict[str, dict]:
        """VN (or AN) -> its newest claim (the latest sent)."""
        keys = [k for k in dict.fromkeys(keys) if k]
        found: dict[str, dict] = {}
        with self._db() as db:
            for start in range(0, len(keys), 500):
                part = keys[start:start + 500]
                marks = ",".join("?" * len(part))
                for r in db.execute(f"SELECT * FROM fdh_claims WHERE seq IN ({marks}) OR an IN ({marks}) ORDER BY sent_at",
                                    [*part, *part]):
                    key = r["an"] if r["an"] and r["an"] in part else r["seq"]
                    found[key] = dict(r)
        return found

    def summary(self) -> list[dict]:
        """Per fund and status: how many claims, the service dates and the last import."""
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT fund, status, COUNT(*) n, MIN(service_at) first, MAX(service_at) last, MAX(imported_at) imported_at "
                "FROM fdh_claims GROUP BY fund, status ORDER BY fund, n DESC")]

    def for_key(self, key: str) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM fdh_claims WHERE seq=? OR an=? ORDER BY sent_at", (key, key))]
