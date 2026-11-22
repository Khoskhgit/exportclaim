"""The home page: an overview of every menu from what the app keeps itself (REP/STM imports, export and send
history). It never reads HOSxP, so it opens at once; the unsent counts are on each menu's own page.

REP lines are counted once per visit (InvNo) and account: the newest line, skipping a resend refused only as a
duplicate after the visit was accepted (as REP / STM does). The period is the visit date of the REP line (DTTRAN) and
the date of the export or send (a REP/STM line without DTTRAN, as AIPN's, uses the file's date)."""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import eclaim_codes
from .config import Settings
from .repstm_store import is_duplicate_of_accepted
from .store import Store

ACCOUNTS = {"UCS": "บัตรทอง (UCS)", "OFC": "ข้าราชการ (OFC)", "LGO": "อปท. (LGO)", "SOCD": "ประกันสังคม OPD (SSOP)",
            "SIGN": "ประกันสังคม IPD (AIPN)"}
ORDER = list(ACCOUNTS)


# Each menu's export / send history: the table, the date it is counted by, and what it adds up.
MENUS = [
    {"key": "ssop", "label": "SSOP ผู้ป่วยนอก", "href": "/ssop", "unit": "บิล", "what": "ไฟล์ส่งออก", "table": "exports",
     "date": "created_at", "last": "created_at", "items": "SUM(bill_count)", "amount": "IFNULL(SUM(CAST(claim_total AS REAL)),0)"},
    {"key": "aipn", "label": "AIPN ผู้ป่วยใน", "href": "/aipn", "unit": "AN", "what": "ไฟล์ส่งออก", "table": "aipn_exports",
     "date": "created_at", "last": "created_at", "items": "SUM(an_count)", "amount": "IFNULL(SUM(CAST(total AS REAL)),0)"},
    {"key": "dental", "label": "ทันตกรรม ปกส.", "href": "/dental", "unit": "รายการ", "what": "รายการขอเบิก", "table": "dental_claims",
     "date": "service_date", "last": "updated_at", "items": "COUNT(*)", "amount": "IFNULL(SUM(CAST(claim_amount AS REAL)),0)",
     "paid": "SUM(CAST(NULLIF(paid_amount,'') AS REAL))"},
    {"key": "nhso18", "label": "18 แฟ้ม สปสช. (ZIP)", "href": "/nhso18", "unit": "visit", "what": "ไฟล์ ZIP", "table": "nhso18_exports",
     "date": "created_at", "last": "created_at", "items": "SUM(visit_count)", "amount": "NULL"},
    {"key": "fdh", "label": "ส่ง FDH (prod)", "href": "/nhso18", "unit": "visit", "what": "ครั้งที่ส่งสำเร็จ", "table": "fdh_sends",
     "where": "ok=1 AND mode='send' AND zone='prod'", "date": "created_at", "last": "created_at", "items": "SUM(visit_count)",
     "amount": "NULL"},
    {"key": "eclaim", "label": "ส่ง e-Claim API", "href": "/nhso18", "unit": "visit", "what": "ครั้งที่ส่งสำเร็จ", "table": "eclaim_sends",
     "where": "ok=1", "date": "created_at", "last": "created_at", "items": "SUM(visit_count)", "amount": "NULL"},
    {"key": "ppfs", "label": "รายการ PPFS", "href": "/ppfs/summary", "unit": "รายการ", "what": "ไฟล์ / การส่ง", "table": "ppfs_exports",
     "date": "created_at", "last": "created_at", "items": "SUM(item_count)", "amount": "IFNULL(SUM(CAST(total AS REAL)),0)"},
    {"key": "ntip", "label": "NTIP (ไฟล์ NTIP Plus)", "href": "/ntip", "unit": "visit", "what": "visit ที่ส่งออก", "table": "ntip_exported",
     "date": "exported_at", "last": "exported_at", "items": "COUNT(*)", "amount": "NULL"},
]


def month_keys(start: date, end: date) -> list[str]:
    out, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def fill_months(start: date, end: date, rows: list[dict]) -> list[dict]:
    """Every month of the period, 0 where nothing was sent (long periods keep their last 24 months)."""
    found = {r["month"]: r["items"] or 0 for r in rows}
    return [{"month": m, "items": found.get(m, 0)} for m in month_keys(start, end)[-24:]]


def fiscal_start(today: date) -> date:
    return date(today.year if today.month >= 10 else today.year - 1, 10, 1)


def _num(value) -> float:
    try:
        return float(str(value or "0").replace(",", "") or 0)
    except ValueError:
        return 0.0


def latest_lines(lines: list[dict]) -> dict[tuple[str, str], dict]:
    """The newest REP line per (account, InvNo), skipping a resend refused only as a duplicate after an A."""
    latest: dict[tuple[str, str], dict] = {}
    accepted: set[tuple[str, str]] = set()
    for row in sorted(lines, key=lambda r: (r["issued_at"], r["rep_no"], r["line"])):
        key = (row["account"], row["invno"])
        if is_duplicate_of_accepted(row["stat"], row["codes"], key in accepted):
            continue
        if row["stat"] == "A":
            accepted.add(key)
        latest[key] = row
    return latest


def rep_status(row: dict) -> str:
    return "passed" if row["stat"] == "A" else "deny" if "DENY" in eclaim_codes.split(row["codes"]) else "c"


def rep_months(lines: list[dict], start: date, end: date) -> list[dict]:
    """Visits per month of the visit date (the file's date when a line has none) by REP result, every month of
    the period (the last 24 of a long one)."""
    found: dict[str, dict] = {}
    for row in latest_lines(lines).values():
        month = found.setdefault(str(row.get("day") or "")[:7], {"passed": 0, "c": 0, "deny": 0})
        month[rep_status(row)] += 1
    return [{"month": m, **found.get(m, {"passed": 0, "c": 0, "deny": 0})} for m in month_keys(start, end)[-24:]]


def rep_summary(lines: list[dict], stm_paid: dict[tuple[str, str], float]) -> list[dict]:
    """lines: REP lines (account, invno, stat, codes, claimamt, paid, issued_at, rep_no, line), any order.
    stm_paid: (account, invno) -> paid by the STM."""
    latest = latest_lines(lines)
    out: dict[str, dict] = {}
    for (account, invno), row in latest.items():
        a = out.setdefault(account, {"account": account, "label": ACCOUNTS.get(account, account), "codes": Counter(),
                                     **{k: {"n": 0, "amount": 0.0} for k in ("sent", "passed", "c", "deny")},
                                     "paid": 0.0, "stm_paid": 0.0, "has_paid": False})
        codes = eclaim_codes.split(row["codes"])
        status = rep_status(row)
        claim = _num(row["claimamt"])
        for part in ("sent", status):
            a[part]["n"] += 1
            a[part]["amount"] += claim
        if row.get("paid") not in (None, ""):
            a["has_paid"] = True
            a["paid"] += _num(row["paid"])
        a["stm_paid"] += stm_paid.get((account, invno), 0.0)
        if status != "passed":
            a["codes"].update(codes)
    result = []
    for account in sorted(out, key=lambda k: (ORDER.index(k) if k in ORDER else len(ORDER), k)):
        a = out[account]
        a["codes"] = [{"code": c, "n": n} for c, n in a["codes"].most_common(8)]
        result.append(a)
    return result


def code_meanings(rows: list[dict], names: dict[str, str]) -> None:
    """Fill each code's meaning: the NHSO document for e-Claim accounts, the REP's own names otherwise."""
    for a in rows:
        for c in a["codes"]:
            if c["code"] == "DENY":
                c["name"] = "ปฏิเสธการจ่าย (Deny) ดูเหตุผลในแผ่นงาน Data DENY ของไฟล์ REP"
            elif eclaim_codes.is_eclaim(a["account"]):
                c["name"] = eclaim_codes.explain(c["code"])["detail"]
            else:
                c["name"] = names.get(c["code"], "")


class DashboardStore:
    def __init__(self, store: Store):
        self._db = store._db

    def _all(self, db, sql: str, args=()) -> list[dict]:
        try:
            return [dict(r) for r in db.execute(sql, args)]
        except sqlite3.OperationalError:  # a table of a menu that has never been used
            return []

    def overview(self, start: date, end: date) -> dict:
        lo, hi = start.isoformat(), end.isoformat() + "T99"
        with self._db() as db:
            lines = self._all(db, """SELECT f.account, b.invno, b.stat, b.codes, b.claimamt, b.paid, f.issued_at, b.rep_no, b.line,
                IFNULL(NULLIF(b.dttran,''), f.issued_at) day
                FROM rep_bills b JOIN rep_files f ON f.rep_no=b.rep_no
                WHERE IFNULL(NULLIF(b.dttran,''), f.issued_at) >= ? AND IFNULL(NULLIF(b.dttran,''), f.issued_at) <= ?""", (lo, hi))
            paid = {(r["account"], r["invno"]): _num(r["paid"]) for r in self._all(db, """SELECT f.account, b.invno,
                SUM(CAST(b.total AS REAL)) paid FROM stm_bills b JOIN stm_files f ON f.stm_no=b.stm_no
                WHERE IFNULL(NULLIF(b.dttran,''), f.date_issue) >= ? AND IFNULL(NULLIF(b.dttran,''), f.date_issue) <= ?
                GROUP BY f.account, b.invno""", (lo, hi))}
            names = {r["code"]: r["name"] for r in self._all(db, "SELECT code, name FROM rep_codes")}

            menus = []
            for m in MENUS:
                where = f"{m['where']} AND " if m.get("where") else ""
                date_col = m["date"]
                head = self._all(db, f"SELECT COUNT(*) n, IFNULL({m['items']},0) items, {m['amount']} amount, "
                                     f"MAX({m['last']}) last FROM {m['table']} WHERE {where}{date_col} >= ? AND {date_col} <= ?", (lo, hi))
                months = self._all(db, f"SELECT substr({date_col},1,7) month, IFNULL({m['items']},0) items FROM {m['table']} "
                                       f"WHERE {where}{date_col} >= ? AND {date_col} <= ? GROUP BY month ORDER BY month", (lo, hi))
                row = {k: v for k, v in m.items() if k in ("key", "label", "href", "unit", "what")}
                row.update(head[0] if head else {"n": 0, "items": 0, "amount": None, "last": None})
                row["months"] = fill_months(start, end, months)
                if m.get("paid"):
                    paid_row = self._all(db, f"SELECT IFNULL({m['paid']},0) p FROM {m['table']} WHERE {date_col} >= ? AND {date_col} <= ?", (lo, hi))
                    row["paid"] = paid_row[0]["p"] if paid_row else 0
                menus.append(row)
            uploads = self._all(db, "SELECT at, name, summary FROM repstm_uploads ORDER BY id DESC LIMIT 6")
        reps = rep_summary(lines, paid)
        code_meanings(reps, names)
        return {"reps": reps, "rep_months": rep_months(lines, start, end), "menus": menus, "uploads": uploads}


def register(app: FastAPI, settings: Settings, local: Store, templates: Jinja2Templates) -> None:
    store = DashboardStore(local)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return templates.TemplateResponse(request, "dashboard.html", {"hname": settings.hname})

    @app.get("/api/dashboard")
    def overview(start: str = "", end: str = ""):
        try:
            first = date.fromisoformat(start) if start else fiscal_start(date.today())
            last = date.fromisoformat(end) if end else date.today()
        except ValueError:
            raise HTTPException(400, "วันที่ไม่ถูกต้อง") from None
        if last < first:
            raise HTTPException(400, "วันที่สิ้นสุดต้องไม่ก่อนวันที่เริ่ม")
        return {"start": first.isoformat(), "end": last.isoformat(), **store.overview(first, last)}
