"""The รายการ PPFS page: PP Fee Schedule items for every right, checked and built into an 18-file ZIP for e-Claim.

The ZIP carries only the items being claimed: every other item of the visit is left out (CHT/CHA add up to the PP
items alone) and the DRU, LAB and AER files are empty, so a visit's other charges are not claimed from สปสช.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import auth, nhso18, nhso18_service as svc, ppfs, ppfs_anemia, ppfs_fluoride, ppfs_fp, ppfs_iron, ppfs_ncd, ppfs_pregtest
from .config import Settings
from .store import Store
from .validate import text

log = logging.getLogger("ssop.ppfs")
ECLAIM_DB = "eclaimdb"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ppfs_exports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, service TEXT NOT NULL, zip_path TEXT NOT NULL,
  item_count INTEGER NOT NULL, total TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ppfs_export_items (
  export_id INTEGER NOT NULL, vn TEXT NOT NULL, code TEXT NOT NULL, amount TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ppfs_export_items_vn ON ppfs_export_items(vn, code);
"""


class PpfsStore:
    def __init__(self, store: Store):
        self._db = store._db
        with self._db() as db:
            db.executescript(SCHEMA)

    def record(self, service: str, zip_path: Path, items: list[dict], created_by: str = "") -> int:
        total = sum(float(i["amount"] or 0) for i in items)
        with self._db() as db:
            export_id = db.execute(
                "INSERT INTO ppfs_exports (created_at, service, zip_path, item_count, total, created_by) VALUES (?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), service, str(zip_path), len(items), f"{total:.2f}",
                 created_by)).lastrowid
            db.executemany("INSERT INTO ppfs_export_items (export_id, vn, code, amount) VALUES (?,?,?,?)",
                           [(export_id, i["vn"], i["code"], str(i["amount"])) for i in items])
        return export_id

    def sent(self, vns: list[str]) -> dict[tuple[str, str], str]:
        """(vn, code) -> when it was last put in a ZIP."""
        out: dict[tuple[str, str], str] = {}
        with self._db() as db:
            for start in range(0, len(vns), 500):
                part = vns[start:start + 500]
                for r in db.execute(
                        f"SELECT i.vn, i.code, MAX(e.created_at) at FROM ppfs_export_items i JOIN ppfs_exports e "
                        f"ON e.id=i.export_id WHERE i.vn IN ({','.join('?' * len(part))}) GROUP BY i.vn, i.code", part):
                    out[(r["vn"], r["code"])] = r["at"]
        return out

    def history(self, limit: int = 50) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, created_at, service, zip_path, item_count, total, created_by FROM ppfs_exports "
                "ORDER BY id DESC LIMIT ?", (limit,))]

    def path(self, export_id: int) -> Path | None:
        with self._db() as db:
            row = db.execute("SELECT zip_path FROM ppfs_exports WHERE id=?", (export_id,)).fetchone()
        return Path(row["zip_path"]) if row else None


def _date(value: Any, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(400, f"{label} ไม่ถูกต้อง") from None


def _service(value: Any) -> ppfs.Service:
    service = ppfs.SERVICES.get(text(value) or "postnatal")
    if service is None:
        raise HTTPException(400, "ไม่รู้จักประเภทบริการ")
    return service


def _out(row: ppfs.Row) -> dict:
    name, rate = ppfs.code_name(row.code)
    return {
        "key": row.key, "vn": row.vn, "code": row.code, "code_name": name, "rate": str(rate), "hn": row.hn,
        "date": row.date, "time": row.time, "ptname": row.ptname, "cid": row.cid[-4:].rjust(len(row.cid), "x") if row.cid else "",
        "pttype": row.pttype, "labor_date": row.labor_date, "days": row.days, "window": row.window,
        "window_label": ppfs.WINDOWS.get(row.window, "") if row.window else "", "count": row.count,
        "qty": f"{row.qty:g}", "amount": f"{row.amount:.2f}", "item_name": row.item_name, "care_no": row.care_no,
        "care_date": row.care_date, "sent": row.sent, "extra": row.extra, "errors": row.errors, "warnings": row.warnings,
    }


def register(app: FastAPI, settings: Settings, hosxp: Any, local: Store, templates: Jinja2Templates) -> None:
    store = PpfsStore(local)

    def q(sql: str) -> list[dict]:
        return hosxp._select(sql, [])

    def check(service: ppfs.Service, start: date, end: date) -> list[ppfs.Row]:
        if service.key == "fluoride":
            data = ppfs_fluoride.fetch(q, start, end)
            return ppfs_fluoride.check(data, store.sent(sorted({text(r["vn"]) for r in data["items"] + data["unbilled"]})))
        if service.key == "iron":
            data = ppfs_iron.fetch(q, start, end)
            return ppfs_iron.check(data, store.sent(sorted({text(r["vn"]) for r in data["items"]})))
        if service.key == "anemia":
            data = ppfs_anemia.fetch(q, start, end)
            return ppfs_anemia.check(data, store.sent(sorted({text(r["vn"]) for r in data["items"]})))
        if service.key == "ncd":
            data = ppfs_ncd.fetch(q, start, end)
            return ppfs_ncd.check(data, store.sent(sorted({text(r["vn"]) for r in data["items"]})))
        if service.key == "fp":
            data = ppfs_fp.fetch(q, start, end)
            return ppfs_fp.check(data, store.sent(sorted({text(r["vn"]) for r in data["items"]})))
        if service.key == "pregtest":
            data = ppfs_pregtest.fetch(q, start, end)
            vns = sorted({text(r["vn"]) for r in data["items"] + data["unbilled"]})
            return ppfs_pregtest.check(data, store.sent(vns))
        data = ppfs.fetch(q, start, end)
        vns = sorted({text(r["vn"]) for r in data["items"] + data["cares"]})
        return ppfs.postnatal(data, store.sent(vns))

    @app.get("/ppfs", response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse(request, "ppfs.html", {"hname": settings.hname})

    @app.get("/api/ppfs/services")
    def services():
        return [{"key": s.key, "label": s.label, "rules": list(s.rules),
                 "codes": [{"code": c, "name": n, "rate": str(r)} for c, (n, r) in s.codes.items()]}
                for s in ppfs.SERVICES.values()]

    @app.post("/api/ppfs/search")
    def search(body: dict = Body(...)):
        service = _service(body.get("service"))
        start, end = _date(body.get("start"), "วันที่เริ่ม"), _date(body.get("end"), "วันที่สิ้นสุด")
        if end < start:
            raise HTTPException(400, "วันที่สิ้นสุดต้องไม่ก่อนวันที่เริ่ม")
        if (end - start).days > 366:
            raise HTTPException(400, "ค้นได้ครั้งละไม่เกิน 1 ปี")
        return {"service": service.key, "rows": [_out(r) for r in check(service, start, end)]}

    @app.post("/api/ppfs/detail")
    def detail(body: dict = Body(...)):
        vn = text(body.get("vn"))
        if not vn:
            raise HTTPException(400, "ไม่ได้ระบุ VN")
        iso = lambda v: v.isoformat()[:10] if hasattr(v, "isoformat") else text(v)
        money = lambda v: f"{float(v or 0):.2f}"
        common = lambda d: {
            "items": [{"icode": text(i["icode"]), "name": text(i["name"]), "adp": text(i["adp"]), "qty": f"{float(i['qty'] or 0):g}",
                       "amount": money(i["amount"])} for i in d["items"]],
            "dx": [{"type": text(x["diagtype"]), "code": text(x["icd10"]), "name": text(x["name"])} for x in d["dx"]],
            "nationality": f"{text(d['visit'].get('nationality'))} {text(d['visit'].get('nationality_name'))}".strip()}
        if _service(body.get("service")).key == "fluoride":
            d = ppfs_fluoride.detail(q, vn)
            if not d:
                raise HTTPException(404, "ไม่พบ visit นี้ใน HOSxP")
            sent = store.sent(sorted({text(c["vn"]) for c in d["claims"]}))
            return {**common(d), "dental": d["dental"].get(vn, []), "claims": [
                {"date": iso(c["vstdate"]), "vn": text(c["vn"]), "year": ppfs.fiscal_year(c["vstdate"]) + 543,
                 "qty": f"{float(c['qty'] or 0):g}", "amount": money(c["amount"]), "dental": d["dental"].get(text(c["vn"]), []),
                 "sent": sent.get((text(c["vn"]), ppfs_fluoride.CODE), "")} for c in d["claims"]]}
        if _service(body.get("service")).key == "iron":
            d = ppfs_iron.detail(q, vn)
            if not d:
                raise HTTPException(404, "ไม่พบ visit นี้ใน HOSxP")
            sent = store.sent(sorted({text(c["vn"]) for c in d["claims"]}))
            return {**common(d), "drug_icodes": [text(i["icode"]) for i in d["items"] if i.get("is_drug")], "claims": [
                {"date": iso(c["vstdate"]), "vn": text(c["vn"]), "year": ppfs.fiscal_year(c["vstdate"]) + 543,
                 "qty": f"{float(c['qty'] or 0):g}", "amount": money(c["amount"]),
                 "drug": d["iron"].get(text(c["vn"]), {}).get("name", ""),
                 "tablets": f"{d['iron'][text(c['vn'])]['qty']:g}" if text(c["vn"]) in d["iron"] else "",
                 "sent": sent.get((text(c["vn"]), ppfs_iron.CODE), "")} for c in d["claims"]]}
        if _service(body.get("service")).key == "anemia":
            d = ppfs_anemia.detail(q, vn)
            if not d:
                raise HTTPException(404, "ไม่พบ visit นี้ใน HOSxP")
            sent = store.sent(sorted({text(c["vn"]) for c in d["claims"]}))
            return {**common(d), "claims": [
                {"date": iso(c["vstdate"]), "vn": text(c["vn"]), "qty": f"{float(c['qty'] or 0):g}", "amount": money(c["amount"]),
                 "labs": {k: d["labs"].get(text(c["vn"]), {}).get(k, "") for k in ppfs_anemia.LABS.values()},
                 "sent": sent.get((text(c["vn"]), ppfs_anemia.CODE), "")} for c in d["claims"]]}
        if _service(body.get("service")).key == "ncd":
            d = ppfs_ncd.detail(q, vn)
            if not d:
                raise HTTPException(404, "ไม่พบ visit นี้ใน HOSxP")
            sent = store.sent(sorted({text(c["vn"]) for c in d["claims"]}))
            drug = d["first_drug"]
            return {**common(d),
                    "first": {k: iso(v) if v else "" for k, v in d["first"].items()},
                    "first_drug": {"date": iso(drug["vstdate"]), "name": text(drug["name"])} if drug else None,
                    "claims": [{"date": iso(c["vstdate"]), "vn": text(c["vn"]), "code": text(c["code"]),
                                "name": ppfs_ncd.CODES.get(text(c["code"]), (text(c["code"]),))[0],
                                "qty": f"{float(c['qty'] or 0):g}", "amount": money(c["amount"]),
                                "labs": {k: d["labs"].get(text(c["vn"]), {}).get(k, "")
                                         for k in ppfs_ncd.LABS.get(text(c["code"]), {}).values()},
                                "sent": sent.get((text(c["vn"]), text(c["code"])), "")} for c in d["claims"]]}
        if _service(body.get("service")).key == "fp":
            d = ppfs_fp.detail(q, vn)
            if not d:
                raise HTTPException(404, "ไม่พบ visit นี้ใน HOSxP")
            sent = store.sent(sorted({text(h["vn"]) for h in d["history"]}))
            return {**common(d), "year": d["year"],
                    "drug_icodes": [text(i["icode"]) for i in d["items"] if i.get("is_drug")],
                    "history": [{"date": iso(h["vstdate"]), "vn": text(h["vn"]), "code": text(h["code"]),
                                 "name": ppfs_fp.CODES.get(text(h["code"]), (text(h["code"]),))[0],
                                 "unit": ppfs_fp.CODES.get(text(h["code"]), ("", 0, ""))[2],
                                 "fee_qty": f"{float(h['fee_qty'] or 0):g}", "drug_qty": f"{float(h['drug_qty'] or 0):g}",
                                 "amount": money(h["amount"]), "sent": sent.get((text(h["vn"]), text(h["code"])), "")}
                                for h in d["history"]],
                    "caps": [{"codes": list(c), "names": [ppfs_fp.CODES[x][0] for x in c], "cap": n, "unit": u}
                             for c, n, u in ppfs_fp.YEAR_CAPS]}
        if _service(body.get("service")).key == "pregtest":
            d = ppfs_pregtest.detail(q, vn)
            if not d:
                raise HTTPException(404, "ไม่พบ visit นี้ใน HOSxP")
            sent = store.sent(sorted({text(t["vn"]) for t in d["tests"]}))
            return {**common(d), "year": d["year"], "upt": d["upt"].get(vn, ""),
                    "tests": [{"date": iso(t["vstdate"]), "vn": text(t["vn"]), "qty": f"{float(t['qty'] or 0):g}",
                               "amount": money(t["amount"]), "upt": d["upt"].get(text(t["vn"]), ""),
                               "sent": sent.get((text(t["vn"]), ppfs.PREGTEST), "")} for t in d["tests"]]}
        d = ppfs.detail(q, vn)
        if not d:
            raise HTTPException(404, "ไม่พบ visit นี้ใน HOSxP")
        claim_vns = sorted({text(c["vn"]) for c in d["claims"]})
        sent = store.sent(claim_vns)
        birth = d["birth"]
        start = birth and birth.get("labor_date")
        return {
            **common(d),
            "birth": {"preg_no": text(birth.get("preg_no")), "lmp": iso(birth.get("lmp")), "labor_date": iso(start),
                      "labour_type": text(birth.get("labour_type")), "hospcode": text(birth.get("labour_hospcode"))} if birth else None,
            "cares": [{"date": iso(c["care_date"]), "care_no": c["care_no"], "vn": text(c["vn"]), "place": text(c["place"]),
                       "days": (c["care_date"] - start).days if start and c["care_date"] else None} for c in d["cares"]],
            "claims": [{"date": iso(c["vstdate"]), "vn": text(c["vn"]), "code": text(c["code"]), "qty": f"{float(c['qty'] or 0):g}",
                        "amount": money(c["amount"]), "days": (c["vstdate"] - start).days if start else None,
                        "window": ppfs.WINDOWS.get(ppfs.window((c["vstdate"] - start).days), "") if start else "",
                        "sent": sent.get((text(c["vn"]), text(c["code"])), "")} for c in d["claims"]],
            "iron": [{"name": text(i["name"]), "qty": f"{float(i['qty'] or 0):g}"} for i in d["iron"]],
        }

    @app.post("/api/ppfs/export")
    def export(request: Request, body: dict = Body(...)):
        service = _service(body.get("service"))
        keys = list(dict.fromkeys(text(k) for k in body.get("keys") or [] if "|" in text(k)))
        if not keys:
            raise HTTPException(400, "ยังไม่ได้เลือกรายการ")
        # Check again with fresh data over the days of the picked visits.
        days = sorted({_date(text(d), "วันที่") for d in body.get("dates") or []})
        if not days:
            raise HTTPException(400, "ข้อมูลไม่ครบ")
        rows = {r.key: r for r in check(service, days[0], days[-1])}
        picked = [rows.get(k) for k in keys]
        missing = [k for k, r in zip(keys, picked) if r is None]
        if missing:
            raise HTTPException(400, f"ไม่พบรายการ {missing[0].split('|')[0]} แล้ว ให้ค้นหาใหม่")
        blocked = [{"key": r.key, "reason": r.errors[0]["message"]} for r in picked if r.errors]
        if blocked:
            return JSONResponse({"detail": "มีรายการที่ส่งไม่ได้", "blocked": blocked}, status_code=409)
        resend = [r.key for r in picked if r.sent]
        if resend and not body.get("confirm_resend"):
            return JSONResponse({"detail": "ต้องยืนยันการส่งซ้ำ", "resend": resend, "needs_confirm": True}, status_code=409)

        claimed: dict[str, set[str]] = {}
        for r in picked:
            claimed.setdefault(r.vn, set()).add(r.code)
        vns = list(claimed)
        # Leave out every other item of these visits, so CHT/CHA add up to the claimed PP items only.
        excluded = {(text(i["vn"]), text(i["icode"])) for i in ppfs.visit_items(q, vns)
                    if text(i.get("code")) not in claimed.get(text(i["vn"]), set()) or i.get("is_drug")}
        config, groups = svc.load_setup(q, settings.hcode)
        visits = nhso18.opd_contexts(q, vns, groups, config.zone)
        if not visits:
            raise HTTPException(400, "ไม่พบ visit ที่เลือก")
        data = nhso18.Builder(q, config, settings.rcm_db, ECLAIM_DB, excluded).run(visits)
        data = ppfs.only_claimed(data, claimed)
        found = {(text(r["SEQ"]), text(r["CODE"])) for r in data["ADP"]}
        lost = [r.key for r in picked if (r.vn, r.code) not in found]
        if lost:
            raise HTTPException(400, f"สร้างแฟ้ม ADP ของ {lost[0].replace('|', ' รหัส ')} ไม่ได้ ตรวจรายการค่าบริการใน HOSxP")
        result = nhso18.build_zip(data, settings.hcode, settings.export_dir)
        items = [{"vn": r.vn, "code": r.code, "amount": f"{r.amount:.2f}"} for r in picked]
        export_id = store.record(service.key, result.zip_path, items, auth.user_label(request))
        log.info("ppfs export id=%s service=%s items=%s", export_id, service.key, len(items))
        return {"id": export_id, "file": result.zip_path.name, "count": len(items), "visits": len(vns),
                "total": f"{sum(r.amount for r in picked):.2f}", "counts": result.counts}

    @app.get("/api/ppfs/exports")
    def exports():
        return [{**row, "file": Path(row.pop("zip_path")).name,
                 "service": ppfs.SERVICES[row["service"]].label if row["service"] in ppfs.SERVICES else row["service"]}
                for row in store.history()]

    @app.get("/api/ppfs/exports/{export_id}/download")
    def download(export_id: int):
        path = store.path(export_id)
        if path is None or not path.is_file():
            raise HTTPException(404, "ไม่พบไฟล์")
        return FileResponse(path, media_type="application/zip", filename=path.name)
