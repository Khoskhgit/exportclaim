"""NTIP menu: the Z11.1 screening visits exported for NTIP Plus, and the TB labs checked against the payment rules.

What the page picks per visit (risk group, symptom screen, CXR result, …) and the culture technique per lab order are
kept in the app's SQLite; patient names are never stored. HOSxP is only read."""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from . import auth, ntip
from .config import Settings
from .store import Store
from .validate import text

log = logging.getLogger(__name__)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ntip_choices (vn TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (vn, name));
CREATE TABLE IF NOT EXISTS ntip_lab_choices (lab_key TEXT PRIMARY KEY, technique TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ntip_exported (vn TEXT PRIMARY KEY, exported_at TEXT NOT NULL, exported_by TEXT NOT NULL DEFAULT '');
"""


class NtipStore:
    def __init__(self, store: Store):
        self._db = store._db
        with self._db() as db:
            db.executescript(SCHEMA)

    def _chunks(self, keys: list[str]):
        for start in range(0, len(keys), 500):
            yield keys[start:start + 500]

    def choices(self, vns: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        with self._db() as db:
            for part in self._chunks(vns):
                for r in db.execute(f"SELECT vn, name, value FROM ntip_choices WHERE vn IN ({','.join('?' * len(part))})", part):
                    out.setdefault(r["vn"], {})[r["name"]] = r["value"]
        return out

    def set_choice(self, vn: str, name: str, value: str) -> None:
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO ntip_choices (vn, name, value, updated_at) VALUES (?,?,?,?)",
                       (vn, name, value, datetime.now().isoformat(timespec="seconds")))

    def reset(self, vn: str, name: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM ntip_choices WHERE vn=? AND name=?", (vn, name))

    def techniques(self) -> dict[str, str]:
        with self._db() as db:
            return {r["lab_key"]: r["technique"] for r in db.execute("SELECT lab_key, technique FROM ntip_lab_choices")}

    def set_technique(self, key: str, technique: str) -> None:
        with self._db() as db:
            if technique:
                db.execute("INSERT OR REPLACE INTO ntip_lab_choices (lab_key, technique, updated_at) VALUES (?,?,?)",
                           (key, technique, datetime.now().isoformat(timespec="seconds")))
            else:
                db.execute("DELETE FROM ntip_lab_choices WHERE lab_key=?", (key,))

    def exported(self, vns: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        with self._db() as db:
            for part in self._chunks(vns):
                for r in db.execute(f"SELECT vn, exported_at FROM ntip_exported WHERE vn IN ({','.join('?' * len(part))})", part):
                    out[r["vn"]] = r["exported_at"]
        return out

    def mark_exported(self, vns: list[str], by: str) -> None:
        at = datetime.now().isoformat(timespec="seconds")
        with self._db() as db:
            db.executemany("INSERT OR REPLACE INTO ntip_exported (vn, exported_at, exported_by) VALUES (?,?,?)",
                           [(vn, at, by) for vn in vns])


def _mask(cid: str) -> str:
    return f"{'x' * (len(cid) - 4)}{cid[-4:]}" if len(cid) > 4 else cid


def _screen_out(s: ntip.Screen) -> dict:
    v = dict(s.values)
    for k in ("FNAME", "LNAME", "CID", "ADDR"):
        v.pop(k, None)
    return {"key": s.vn, "vn": s.vn, "hn": s.hn, "date": s.date, "ptname": s.ptname, "cid": _mask(s.cid), "pttype": s.pttype,
            "nth": s.nth, "values": v, "picked": s.picked, "guess": s.guess, "dx": s.dx, "cxr": s.cxr, "exported": s.exported,
            "errors": s.errors, "warnings": s.warnings}


def _lab_out(r: ntip.LabRow) -> dict:
    return {"key": r.key, "vn": r.vn, "hn": r.hn, "date": r.date, "code": r.code, "name": r.name, "result": r.result,
            "method": r.method, "group": r.group, "group_label": ntip.GROUP_LABEL[r.group], "label": r.label, "nth": r.nth,
            "rate": f"{r.rate:.2f}" if r.rate is not None else "", "technique": r.technique, "claimable": r.claimable,
            "ptname": r.ptname, "cid": _mask(r.cid), "pttype": r.pttype, "kind": r.kind, "errors": r.errors, "warnings": r.warnings}


def register(app: FastAPI, settings: Settings, hosxp: Any, local: Store, templates: Jinja2Templates) -> None:
    store = NtipStore(local)

    def q(sql: str) -> list[dict]:
        return hosxp._select(sql, [])

    def period(body: dict) -> tuple[date, date]:
        try:
            start, end = date.fromisoformat(str(body.get("start"))), date.fromisoformat(str(body.get("end")))
        except ValueError:
            raise HTTPException(400, "กรุณาเลือกช่วงวันที่") from None
        if start > end:
            raise HTTPException(400, "วันที่เริ่มต้องไม่เกินวันที่สิ้นสุด")
        if (end - start).days > 366:
            raise HTTPException(400, "เลือกช่วงวันที่ได้ไม่เกิน 1 ปี")
        return start, end

    def screening(start: date, end: date) -> list[ntip.Screen]:
        data = ntip.fetch_screening(q, start, end)
        vns = [text(v["vn"]) for v in data["visits"]]
        return ntip.check_screening(data, store.choices(vns), store.exported(vns))

    def labs(start: date, end: date) -> list[ntip.LabRow]:
        return ntip.check_labs(ntip.fetch_labs(q, start, end), start, store.techniques())

    def download(data: bytes, name: str) -> Response:
        return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    @app.get("/ntip", response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse(request, "ntip.html", {"hname": settings.hname})

    @app.get("/api/ntip/options")
    def options():
        pairs = lambda d: [{"code": k, "name": v} for k, v in d.items()]
        return {"risks": pairs(ntip.RISKS), "symptoms": pairs(ntip.SYMPTOMS), "cxr_results": pairs(ntip.CXR_RESULTS),
                "cxr_abnormal": pairs(ntip.CXR_ABNORMAL), "dx": pairs(ntip.DXS),
                "culture": [{"code": k, "name": f"{n} {r:.0f} บาท"} for k, (n, r) in ntip.CULTURE.items()],
                "contact_risks": sorted(ntip.CONTACT_RISKS), "prison_risks": sorted(ntip.PRISON_RISKS),
                "methods": [{"key": m.key, "group": ntip.GROUP_LABEL[m.group], "label": m.label,
                             "rate": f"{m.rate:.0f}" if m.rate is not None else "200/300"} for m in ntip.METHODS]}

    @app.post("/api/ntip/screen")
    def screen(body: dict = Body(...)):
        start, end = period(body)
        return {"rows": [_screen_out(s) for s in screening(start, end)]}

    @app.post("/api/ntip/choice")
    def choice(body: dict = Body(...)):
        vn, name = text(body.get("vn")), text(body.get("name"))
        if not vn:
            raise HTTPException(400, "ไม่พบ VN")
        if body.get("reset"):
            store.reset(vn, name)
            return {"ok": True}
        try:
            value = ntip.valid_choice(name, body.get("value"))
        except ntip.ChoiceError as exc:
            raise HTTPException(400, str(exc)) from None
        store.set_choice(vn, name, value)
        return {"ok": True, "value": value}

    @app.post("/api/ntip/export")
    def export(request: Request, body: dict = Body(...)):
        start, end = period(body)
        wanted = {text(v) for v in body.get("vns") or []}
        if not wanted:
            raise HTTPException(400, "ยังไม่ได้เลือกรายการ")
        rows = [s for s in screening(start, end) if s.vn in wanted]
        ready = [s for s in rows if not s.errors]
        if not ready:
            raise HTTPException(400, "ไม่มีรายการที่พร้อมส่งออก (แก้รายการที่มี Error ก่อน)")
        store.mark_exported([s.vn for s in ready], auth.user_label(request))
        log.info("ntip export rows=%s skipped=%s", len(ready), len(rows) - len(ready))
        return download(ntip.screening_xlsx(ready), f"NTIPPlus_{start:%Y%m%d}_{end:%Y%m%d}.xlsx")

    @app.post("/api/ntip/detail")
    def detail(body: dict = Body(...)):
        vn = text(body.get("vn"))
        visit = (q(f"SELECT o.vn, o.hn, o.vstdate FROM ovst o WHERE o.vn={ntip.lit(vn)}") or [None])[0]
        if not visit:
            raise HTTPException(404, "ไม่พบ visit นี้ใน HOSxP")
        day = date.fromisoformat(str(visit["vstdate"])[:10])
        dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={ntip.lit(vn)} ORDER BY diagtype, icd10")
        tb = [r for r in labs(ntip.fiscal_start(day), max(day, date.today())) if r.hn == text(visit["hn"])]
        return {"dx": [{"type": text(d["diagtype"]), "code": text(d["icd10"]), "name": text(d["name"])} for d in dx],
                "labs": [_lab_out(r) for r in tb]}

    @app.post("/api/ntip/labs")
    def lab_list(body: dict = Body(...)):
        start, end = period(body)
        return {"rows": [_lab_out(r) for r in labs(start, end)]}

    @app.post("/api/ntip/lab-choice")
    def lab_choice(body: dict = Body(...)):
        key, technique = text(body.get("key")), text(body.get("technique"))
        if ":" not in key or technique not in ("", *ntip.CULTURE):
            raise HTTPException(400, "ข้อมูลไม่ถูกต้อง")
        store.set_technique(key, technique)
        return {"ok": True}

    @app.post("/api/ntip/labs/excel")
    def lab_excel(body: dict = Body(...)):
        start, end = period(body)
        return download(ntip.labs_xlsx(labs(start, end), start, end, settings.hname), f"NTIP_Lab_{start:%Y%m%d}_{end:%Y%m%d}.xlsx")
