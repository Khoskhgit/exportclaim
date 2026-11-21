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


def fiscal_start(today: date) -> date:
    return date(today.year if today.month >= 10 else today.year - 1, 10, 1)


def _num(value) -> float:
    try:
        return float(str(value or "0").replace(",", "") or 0)
    except ValueError:
        return 0.0


def rep_summary(lines: list[dict], stm_paid: dict[tuple[str, str], float]) -> list[dict]:
    """lines: REP lines (account, invno, stat, codes, claimamt, paid, issued_at, rep_no, line), any order.
    stm_paid: (account, invno) -> paid by the STM."""
    latest: dict[tuple[str, str], dict] = {}
    accepted: set[tuple[str, str]] = set()
    for row in sorted(lines, key=lambda r: (r["issued_at"], r["rep_no"], r["line"])):
        key = (row["account"], row["invno"])
        if is_duplicate_of_accepted(row["stat"], row["codes"], key in accepted):
            continue
        if row["stat"] == "A":
            accepted.add(key)
        latest[key] = row
    out: dict[str, dict] = {}
    for (account, invno), row in latest.items():
        a = out.setdefault(account, {"account": account, "label": ACCOUNTS.get(account, account), "codes": Counter(),
                                     **{k: {"n": 0, "amount": 0.0} for k in ("sent", "passed", "c", "deny")},
                                     "paid": 0.0, "stm_paid": 0.0, "has_paid": False})
        codes = eclaim_codes.split(row["codes"])
        status = "passed" if row["stat"] == "A" else "deny" if "DENY" in codes else "c"
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
            lines = self._all(db, """SELECT f.account, b.invno, b.stat, b.codes, b.claimamt, b.paid, f.issued_at, b.rep_no, b.line
                FROM rep_bills b JOIN rep_files f ON f.rep_no=b.rep_no
                WHERE IFNULL(NULLIF(b.dttran,''), f.issued_at) >= ? AND IFNULL(NULLIF(b.dttran,''), f.issued_at) <= ?""", (lo, hi))
            paid = {(r["account"], r["invno"]): _num(r["paid"]) for r in self._all(db, """SELECT f.account, b.invno,
                SUM(CAST(b.total AS REAL)) paid FROM stm_bills b JOIN stm_files f ON f.stm_no=b.stm_no
                WHERE IFNULL(NULLIF(b.dttran,''), f.date_issue) >= ? AND IFNULL(NULLIF(b.dttran,''), f.date_issue) <= ?
                GROUP BY f.account, b.invno""", (lo, hi))}
            names = {r["code"]: r["name"] for r in self._all(db, "SELECT code, name FROM rep_codes")}

            def one(sql: str) -> dict:
                rows = self._all(db, sql, (lo, hi))
                return rows[0] if rows else {"n": 0, "items": 0, "amount": 0, "last": None}

            menus = [
                {"key": "ssop", "label": "SSOP ผู้ป่วยนอก", "href": "/ssop", "unit": "บิล", "what": "ไฟล์ส่งออก",
                 **one("SELECT COUNT(*) n, IFNULL(SUM(bill_count),0) items, IFNULL(SUM(CAST(claim_total AS REAL)),0) amount, "
                       "MAX(created_at) last FROM exports WHERE created_at >= ? AND created_at <= ?")},
                {"key": "aipn", "label": "AIPN ผู้ป่วยใน", "href": "/aipn", "unit": "AN", "what": "ไฟล์ส่งออก",
                 **one("SELECT COUNT(*) n, IFNULL(SUM(an_count),0) items, IFNULL(SUM(CAST(total AS REAL)),0) amount, "
                       "MAX(created_at) last FROM aipn_exports WHERE created_at >= ? AND created_at <= ?")},
                {"key": "nhso18", "label": "18 แฟ้ม สปสช. (ZIP)", "href": "/nhso18", "unit": "visit", "what": "ไฟล์ ZIP",
                 **one("SELECT COUNT(*) n, IFNULL(SUM(visit_count),0) items, NULL amount, MAX(created_at) last "
                       "FROM nhso18_exports WHERE created_at >= ? AND created_at <= ?")},
                {"key": "fdh", "label": "ส่ง FDH (prod)", "href": "/nhso18", "unit": "visit", "what": "ครั้งที่ส่งสำเร็จ",
                 **one("SELECT COUNT(*) n, IFNULL(SUM(visit_count),0) items, NULL amount, MAX(created_at) last FROM fdh_sends "
                       "WHERE ok=1 AND mode='send' AND zone='prod' AND created_at >= ? AND created_at <= ?")},
                {"key": "eclaim", "label": "ส่ง e-Claim API", "href": "/nhso18", "unit": "visit", "what": "ครั้งที่ส่งสำเร็จ",
                 **one("SELECT COUNT(*) n, IFNULL(SUM(visit_count),0) items, NULL amount, MAX(created_at) last FROM eclaim_sends "
                       "WHERE ok=1 AND created_at >= ? AND created_at <= ?")},
                {"key": "ppfs", "label": "รายการ PPFS", "href": "/ppfs/summary", "unit": "รายการ", "what": "ไฟล์ / การส่ง",
                 **one("SELECT COUNT(*) n, IFNULL(SUM(item_count),0) items, IFNULL(SUM(CAST(total AS REAL)),0) amount, "
                       "MAX(created_at) last FROM ppfs_exports WHERE created_at >= ? AND created_at <= ?")},
                {"key": "dental", "label": "ทันตกรรม ปกส.", "href": "/dental", "unit": "รายการ", "what": "รายการขอเบิก",
                 **one("SELECT COUNT(*) n, COUNT(*) items, IFNULL(SUM(CAST(claim_amount AS REAL)),0) amount, MAX(updated_at) last "
                       "FROM dental_claims WHERE service_date >= ? AND service_date <= ?"),
                 "paid": (self._all(db, "SELECT IFNULL(SUM(CAST(NULLIF(paid_amount,'') AS REAL)),0) p FROM dental_claims "
                                        "WHERE service_date >= ? AND service_date <= ?", (lo, hi)) or [{"p": 0}])[0]["p"]},
                {"key": "ntip", "label": "NTIP (ไฟล์ NTIP Plus)", "href": "/ntip", "unit": "visit", "what": "visit ที่ส่งออก",
                 **one("SELECT COUNT(*) n, COUNT(*) items, NULL amount, MAX(exported_at) last FROM ntip_exported "
                       "WHERE exported_at >= ? AND exported_at <= ?")},
            ]
            uploads = self._all(db, "SELECT at, name, summary FROM repstm_uploads ORDER BY id DESC LIMIT 6")
        reps = rep_summary(lines, paid)
        code_meanings(reps, names)
        return {"reps": reps, "menus": menus, "uploads": uploads}


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
