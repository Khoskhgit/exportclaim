"""The รายการ PPFS page: PP Fee Schedule items for every right, checked and built into an 18-file ZIP for e-Claim.

The ZIP carries only the items being claimed: every other item of the visit is left out (CHT/CHA add up to the PP
items alone) and the DRU, LAB and AER files are empty, so a visit's other charges are not claimed from สปสช.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import (auth, fdh, nhso18, nhso18_service as svc, ppfs, ppfs_anemia, ppfs_fluoride, ppfs_fp, ppfs_iron, ppfs_ironchild,
               ppfs_anemiachild, ppfs_dmrem,
               ppfs_ncd, ppfs_pregtest)
from . import dmrem_registry
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
CREATE TABLE IF NOT EXISTS ppfs_items (
  service TEXT NOT NULL, vn TEXT NOT NULL, icode TEXT NOT NULL, include INTEGER NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (service, vn, icode)
);
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

    def choices(self, service: str, vns: list[str]) -> dict[tuple[str, str], bool]:
        """Items the user ticked or unticked by hand in the cost tab: (vn, icode) -> send."""
        out: dict[tuple[str, str], bool] = {}
        with self._db() as db:
            for start in range(0, len(vns), 500):
                part = vns[start:start + 500]
                for r in db.execute(f"SELECT vn, icode, include FROM ppfs_items WHERE service=? AND vn IN ({','.join('?' * len(part))})",
                                    [service, *part]):
                    out[(r["vn"], r["icode"])] = bool(r["include"])
        return out

    def set_choice(self, service: str, vn: str, icode: str, include: bool) -> None:
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO ppfs_items (service, vn, icode, include, updated_at) VALUES (?,?,?,?,?)",
                       (service, vn, icode, int(include), datetime.now().isoformat(timespec="seconds")))

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
    if not rate and row.amount:  # a code set only in .env (6): the row knows what it is
        name, rate = row.item_name, row.amount
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
    registry = dmrem_registry.RegistryStore(local)

    def q(sql: str) -> list[dict]:
        return hosxp._select(sql, [])

    def send_plan(service: ppfs.Service, claimed: dict[str, set[str]]) -> dict[str, dict[str, dict]]:
        """vn -> icode -> {"send", "locked", "default"}: what goes into the ZIP for each billed item of the visits.

        The claimed fee is always sent (locked). Everything else of the visit is ticked by default too, as the user asked;
        the user unticks in the cost tab what should stay out."""
        vns = sorted(claimed)
        items = ppfs.visit_items(q, vns)
        special = {"ironchild": ppfs_ironchild, "anemiachild": ppfs_anemiachild}.get(service.key)
        fee_icodes = set(special.all_icodes(special.bands(settings))) if special else set()
        chosen = store.choices(service.key, vns)
        plan: dict[str, dict[str, dict]] = {}
        for i in items:
            vn, icode = text(i["vn"]), text(i["icode"])
            fee = icode in fee_icodes if special else (text(i.get("code")) in claimed.get(vn, set()) and not i.get("is_drug"))
            default = service.key != "dmrem"  # 6) sends the HbA1c visits without charges: they are billed on the 18-file page
            send = True if fee else chosen.get((vn, icode), default)
            plan.setdefault(vn, {})[icode] = {"send": send, "locked": fee, "default": default}
        return plan

    def check(service: ppfs.Service, start: date, end: date) -> list[ppfs.Row]:
        if service.key == "dmrem":
            summary = registry.summary()
            imported = summary["n"] > 0
            first = summary.get("first")
            data = ppfs_dmrem.fetch(q, start, end, ppfs_dmrem.window_start(first) if first else None)
            vns = sorted({text(r["vn"]) for r in data["tests"]})
            cids = {text(r.get("cid")) for r in data["tests"]}
            return ppfs_dmrem.check(data, start, end, store.sent(vns), registry.by_pid(cids) if imported else None)
        if service.key == "anemiachild":
            band_list = ppfs_anemiachild.bands(settings)
            data = ppfs_anemiachild.fetch(q, start, end, band_list)
            return ppfs_anemiachild.check(data, band_list, store.sent(sorted({text(r["vn"]) for r in data["items"]})))
        if service.key == "ironchild":
            data = ppfs_ironchild.fetch(q, start, end, ppfs_ironchild.bands(settings))
            return ppfs_ironchild.check(data, ppfs_ironchild.bands(settings), ppfs_ironchild.form_codes(settings),
                                        store.sent(sorted({text(r["vn"]) for r in data["items"]})))
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
        out = _detail(body)
        service, vn = _service(body.get("service")), text(body.get("vn"))
        codes = {text(c) for c in body.get("codes") or []}
        plan = send_plan(service, {vn: codes}).get(vn, {})
        services = item_services([(i["icode"], i.get("adp", "")) for i in out.get("items", [])])
        for i in out.get("items", []):
            p = plan.get(i["icode"], {"send": False, "locked": False, "default": False})
            found = services.get(i["icode"], [])
            i.update(send=p["send"], locked=p["locked"], default=p["default"],
                     services=[{"key": k, "label": ppfs.SERVICES[k].label} for k in found], this_service=service.key in found)
        return out

    def item_services(items: list[tuple[str, str]]) -> dict[str, list[str]]:
        """icode -> the PPFS services (keys) the item is claimed under: by ADP code, or by icode for 26) and 27) whose
        fees sit under HOSxP's placeholder 10000, and the HbA1c lab for 6)."""
        by_icode = {i: "anemiachild" for i in ppfs_anemiachild.all_icodes(ppfs_anemiachild.bands(settings))}
        by_icode |= {i: "ironchild" for i in ppfs_ironchild.all_icodes(ppfs_ironchild.bands(settings))}
        hba1c = {text(r["icode"]) for r in q(f"SELECT icode FROM lab_items WHERE lab_items_code={ppfs_dmrem.HBA1C_LAB}")}
        out: dict[str, list[str]] = {}
        for icode, code in items:
            found = [k for k, s in ppfs.SERVICES.items() if code and code in s.codes and k not in ("anemiachild", "ironchild", "dmrem")]
            if icode in by_icode:
                found = [by_icode[icode]]
            if icode in hba1c:
                found.append("dmrem")
            out[icode] = found
        return out

    def _detail(body: dict) -> dict:
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
        if _service(body.get("service")).key == "dmrem":
            d = ppfs_dmrem.detail(q, vn)
            if not d:
                raise HTTPException(404, "ไม่พบ visit นี้ใน HOSxP")
            return {**common(d),
                    "tests": [{"date": iso(t["vstdate"]), "vn": text(t["vn"]), "result": text(t["result"])} for t in d["tests"]],
                    "drugs": [{"date": iso(x["vstdate"]), "name": text(x["name"]), "qty": f"{float(x['qty'] or 0):g}"} for x in d["drugs"]]}
        if _service(body.get("service")).key == "anemiachild":
            band_list = ppfs_anemiachild.bands(settings)
            d = ppfs_anemiachild.detail(q, vn, band_list)
            if not d:
                raise HTTPException(404, "ไม่พบ visit นี้ใน HOSxP")
            sent = store.sent(sorted({text(c["vn"]) for c in d["claims"]}))
            return {**common(d), "send_icodes": list(ppfs_anemiachild.all_icodes(band_list)), "claims": [
                {"date": iso(c["vstdate"]), "vn": text(c["vn"]), "months": ppfs_ironchild.months_old(c),
                 "qty": f"{float(c['qty'] or 0):g}", "amount": money(c["amount"]), "band": c["band"], "adp": c["adp"],
                 "labs": {k: d["labs"].get(text(c["vn"]), {}).get(k, "") for k in ("Hb", "Hct")},
                 "sent": sent.get((text(c["vn"]), c["adp"]), "")} for c in d["claims"]]}
        if _service(body.get("service")).key == "ironchild":
            d = ppfs_ironchild.detail(q, vn, ppfs_ironchild.bands(settings), ppfs_ironchild.form_codes(settings))
            if not d:
                raise HTTPException(404, "ไม่พบ visit นี้ใน HOSxP")
            sent = store.sent(sorted({text(c["vn"]) for c in d["claims"]}))
            return {**common(d), "send_icodes": list(ppfs_ironchild.all_icodes(ppfs_ironchild.bands(settings))), "claims": [
                {"date": iso(c["vstdate"]), "vn": text(c["vn"]), "year": ppfs.fiscal_year(c["vstdate"]) + 543,
                 "months": ppfs_ironchild.months_old(c), "qty": f"{float(c['qty'] or 0):g}", "amount": money(c["amount"]),
                 "drug": d["iron"].get(text(c["vn"]), {}).get("name", ""),
                 "band": c["band"], "form": c["form"], "adp": c["adp"], "sent": sent.get((text(c["vn"]), c["adp"]), "")} for c in d["claims"]]}
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

    # ------------------------------------------------------------ 6) DM Remission registry
    @app.get("/api/ppfs/dmrem/registry")
    def registry_summary():
        return registry.summary()

    @app.post("/api/ppfs/dmrem/registry")
    async def registry_import(request: Request, name: str = ""):
        data = await request.body()
        if not data:
            raise HTTPException(400, "ไม่ได้เลือกไฟล์")
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(413, "ไฟล์ใหญ่เกินไป")
        try:
            rows = dmrem_registry.parse(name, data)
        except dmrem_registry.RegistryError as exc:
            raise HTTPException(400, str(exc)) from None
        units = {r.unit.split("-")[0] for r in rows if r.unit}
        if settings.hcode and units and settings.hcode not in units:
            raise HTTPException(400, f"เป็นทะเบียนของหน่วยบริการ {', '.join(sorted(units))} ไม่ใช่ {settings.hcode}")
        result = registry.save(rows, name, auth.user_label(request))
        log.info("dmrem registry import %s rows=%s", name, len(rows))
        return {**result, "rows": len(rows), "people": len({r.pid for r in rows}), "summary": registry.summary()}

    @app.post("/api/ppfs/dmrem/follow-up")
    def registry_follow_up():
        """Every registered patient: the HbA1c so far, the stage reached and when the next test is due."""
        regs = registry.by_pid()
        if not regs:
            return {"rows": [], "summary": registry.summary()}
        today = date.today()
        patients = {text(r["cid"]): r for r in q(f"""
            SELECT p.cid, p.hn, CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, TIMESTAMPDIFF(YEAR,p.birthday,CURDATE()) age
            FROM patient p WHERE p.cid IN ({nhso18.lit_list(regs)})""")}
        hns = sorted({text(p["hn"]) for p in patients.values()})
        tests: dict[str, list] = {}
        drugs: dict[str, list] = {}
        if hns:
            for r in q(f"""
                SELECT o.hn, o.vstdate, lo.lab_order_result result FROM lab_head lh
                JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number JOIN ovst o ON o.vn=lh.vn
                WHERE lo.lab_items_code={ppfs_dmrem.HBA1C_LAB} AND o.hn IN ({nhso18.lit_list(hns)})
                  AND IFNULL(lo.lab_order_result,'')<>''"""):
                v = ppfs_dmrem.value(r["result"])
                if v is not None and r["vstdate"]:
                    tests.setdefault(text(r["hn"]), {})[r["vstdate"]] = v  # one result a day
            seen = set()
            for r in ppfs_dmrem.drug_days(q, ppfs_dmrem.drug_icodes(q), date(1900, 1, 1), today, set(hns)):
                if (r["hn"], r["vstdate"]) not in seen:
                    seen.add((r["hn"], r["vstdate"]))
                    drugs.setdefault(r["hn"], []).append(r["vstdate"])
        iso = lambda d: d.isoformat() if d else ""
        out = []
        for pid, entries in regs.items():
            reg = entries[0]
            p = patients.get(pid)
            hn = text(p["hn"]) if p else ""
            series = sorted(tests.get(hn, {}).items())
            since = ppfs_dmrem.window_start(reg["reg_date"]) if reg["reg_date"] else None
            f = ppfs_dmrem.follow_up(series, drugs.get(hn, []), today, since, ppfs_dmrem.stop_day(entries, today))
            due = f["next_from"]
            overdue = bool(f.get("next_to") and f["next_to"] < today and f["state"] == "wait3")
            out.append({
                "pid": f"{'x' * 9}{pid[-4:]}", "hn": hn, "ptname": text(p["ptname"]) if p else "", "age": p["age"] if p else None,
                "in_hosxp": bool(p), "reg_date": reg["reg_date"], "inscl": reg["inscl"], "status": reg["status"],
                "state": f["state"], "stage": f["stage"], "note": f.get("note", ""),
                "tests": [{"date": iso(d), "value": f"{v}"} for d, v in series[-4:]],
                "next_from": iso(due), "next_to": iso(f["next_to"]), "overdue": overdue,
                "cutoff": iso(ppfs_dmrem.cutoff(max(due, today))) if due else "",
                "last_drug": iso(f["last_drug"]),
            })
        order = {"wait3": 0, "wait2": 1, "relapse": 2, "not_yet": 3, "no_test": 4, "done": 5}
        out.sort(key=lambda r: (order.get(r["state"], 9), r["next_from"] or "9999", r["hn"]))
        return {"rows": out, "summary": registry.summary(), "today": today.isoformat()}

    @app.post("/api/ppfs/item")
    def item_choice(body: dict = Body(...)):
        service = _service(body.get("service"))
        vn, icode = text(body.get("vn")), text(body.get("icode"))
        if not vn or not icode:
            raise HTTPException(400, "ข้อมูลไม่ครบ")
        store.set_choice(service.key, vn, icode, bool(body.get("include")))
        return {"ok": True}

    def build(body: dict):
        """Re-check the picked rows with fresh data and build the 18 files (shared by the ZIP and FDH).

        Returns (service, picked rows, vns, data) or a JSONResponse asking to fix or confirm."""
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
        if service.key == "dmrem":  # 6) sends the HbA1c visits of each stage, with no fee
            for vn in ppfs_dmrem.stage_visits(picked):
                claimed.setdefault(vn, set())
        else:
            for r in picked:
                claimed.setdefault(r.vn, set()).add(r.code)
        vns = list(claimed)
        # Leave out every other item of these visits, so CHT/CHA add up to the claimed PP items only.
        # Only the claimed fees and what the user ticked in the cost tab go into the ZIP (CHT/CHA add up to those).
        plan = send_plan(service, claimed)
        excluded = {(vn, icode) for vn, entries in plan.items() for icode, p in entries.items() if not p["send"]}
        items = ppfs.visit_items(q, vns)
        sent_items = [i for i in items if plan.get(text(i["vn"]), {}).get(text(i["icode"]), {}).get("send")]
        # ADP codes of the other items sent; a drug goes to DRU only (DMPA carries its fee's code FP003_4).
        ticked: dict[str, set[str]] = {}
        drug_adp: set[tuple[str, str, Decimal]] = set()
        for i in sent_items:
            vn, code = text(i["vn"]), text(i.get("code"))
            if not code:
                continue
            if i.get("is_drug"):
                drug_adp.add((vn, code, Decimal(str(i.get("unitprice") or 0)).quantize(Decimal("0.01"))))
            elif not plan[vn][text(i["icode"])]["locked"]:
                ticked.setdefault(vn, set()).add(code)
        config, groups = svc.load_setup(q, settings.hcode)
        visits = nhso18.opd_contexts(q, vns, groups, config.zone)
        if not visits:
            raise HTTPException(400, "ไม่พบ visit ที่เลือก")
        data = nhso18.Builder(q, config, settings.rcm_db, ECLAIM_DB, excluded).run(visits)
        if service.key in ("ironchild", "anemiachild"):
            rate = ppfs_ironchild.RATE if service.key == "ironchild" else ppfs_anemiachild.RATE
            ppfs_ironchild.rewrite_adp(data, claimed, rate)  # 10000 -> the e-Claim code chosen for the visit
        data = ppfs.only_claimed(data, {vn: claimed[vn] | ticked.get(vn, set()) for vn in vns}, keep_lab=service.key == "dmrem")
        data["ADP"] = [r for r in data["ADP"]
                       if (text(r.get("SEQ")), text(r.get("CODE")), Decimal(str(r.get("RATE") or 0)).quantize(Decimal("0.01"))) not in drug_adp]
        if service.key == "dmrem":
            hba1c = ppfs_dmrem.lab_rows(q, vns, settings.hcode)
            done = {(r["SEQ"], r["LABTEST"]) for r in hba1c}
            data["LAB"] = sorted([r for r in data["LAB"] if (text(r.get("SEQ")), text(r.get("LABTEST"))) not in done] + hba1c,
                                 key=lambda r: (text(r.get("SEQ")), text(r.get("LABTEST"))))
            missing = [vn for vn in vns if not any(r["SEQ"] == vn for r in hba1c)]
            if missing:
                raise HTTPException(400, f"ไม่พบผล HbA1c ของ VN {missing[0]} แล้ว ให้ค้นหาใหม่")
        found = {(text(r["SEQ"]), text(r["CODE"])) for r in data["ADP"]}
        lost = [r.key for r in picked if service.key != "dmrem" and (r.vn, r.code) not in found]
        if lost:
            raise HTTPException(400, f"สร้างแฟ้ม ADP ของ {lost[0].replace('|', ' รหัส ')} ไม่ได้ ตรวจรายการค่าบริการใน HOSxP")
        return service, picked, vns, data

    @app.post("/api/ppfs/export")
    def export(request: Request, body: dict = Body(...)):
        built = build(body)
        if isinstance(built, JSONResponse):
            return built
        service, picked, vns, data = built
        result = nhso18.build_zip(data, settings.hcode, settings.export_dir)
        items = [{"vn": r.vn, "code": r.code, "amount": f"{r.amount:.2f}"} for r in picked]
        export_id = store.record(service.key, result.zip_path, items, auth.user_label(request))
        log.info("ppfs export id=%s service=%s items=%s", export_id, service.key, len(items))
        return {"id": export_id, "file": result.zip_path.name, "count": len(items), "visits": len(vns),
                "total": f"{sum(r.amount for r in picked):.2f}", "counts": result.counts}

    # ------------------------------------------------------------ FDH (the account, token test and history are the
    # 18-file page's: /api/nhso18/fdh/status, /account, /test, /history)
    def fdh_account(request: Request) -> tuple[str, str, str]:
        user = getattr(request.state, "user", None)
        login = text(user["user_id"]) if user else ""
        own = local.fdh_account(login) if login else None
        if own:
            return own["fdh_user"], own["password_hash"], "own"
        if settings.fdh_user and settings.fdh_password:
            return settings.fdh_user, "", "env"
        return "", "", ""

    def fdh_client(fdh_user: str, hashed: str) -> fdh.Client:
        password = settings.fdh_password if not hashed and fdh_user == settings.fdh_user else ""
        return fdh.Client(fdh_user, password, settings.hcode, settings.fdh_token_url, settings.fdh_hash_key,
                          settings.moph_user_agent, hashed=hashed or "")

    @app.post("/api/ppfs/fdh/send")
    def fdh_send(request: Request, body: dict = Body(...)):
        zone, prescreen = text(body.get("zone")) or "uat", bool(body.get("prescreen"))
        if zone not in fdh.ZONES:
            raise HTTPException(400, "ปลายทาง FDH ไม่ถูกต้อง")
        fdh_user, hashed, source = fdh_account(request)
        if not source:
            raise HTTPException(400, "ยังไม่ได้ตั้งบัญชี FDH (ตั้งที่ปุ่ม \"บัญชี FDH\" ในหน้า 18 แฟ้ม หรือ FDH_USER / FDH_PASSWORD ใน .env)")
        if prescreen:
            body = {**body, "confirm_resend": True}  # PreScreen only checks; it never needs the resend question.
        built = build(body)
        if isinstance(built, JSONResponse):
            return built
        service, picked, vns, data = built
        parts = fdh.files(fdh.fix_rows(data, q), with_phdb=bool(body.get("phdb", True)) and not prescreen)
        mode = "prescreen" if prescreen else "send"
        folder = settings.export_dir / f"FDH_PPFS_{zone}_{mode}_{datetime.now():%Y%m%d-%H%M%S}"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for name, content in parts.items():
                (folder / name).write_bytes(content)
        except OSError as exc:
            raise HTTPException(500, f"บันทึกสำเนาแฟ้มที่ส่งไม่สำเร็จ: {exc.__class__.__name__}") from None
        user = auth.user_label(request)
        try:
            reply = fdh_client(fdh_user, hashed).send(zone, parts, prescreen)
        except fdh.FdhError as exc:
            local.record_fdh_send(vns, zone, mode, False, None, str(exc), str(folder), user, fdh_user=fdh_user)
            raise HTTPException(502, str(exc)) from None
        local.record_fdh_send(vns, zone, mode, reply.ok, reply.http_status, f"PPFS {service.label}: {reply.message}",
                              str(folder), user, fdh_user=fdh_user)
        export_id = None
        if reply.ok and mode == "send" and zone == "prod":  # only a real send makes the rows "ส่งแล้ว"
            zip_result = nhso18.build_zip(data, settings.hcode, settings.export_dir)
            items = [{"vn": r.vn, "code": r.code, "amount": f"{r.amount:.2f}"} for r in picked]
            export_id = store.record(service.key, zip_result.zip_path, items, f"{user} (FDH)")
        log.info("ppfs fdh %s zone=%s service=%s items=%s ok=%s http=%s fdh_user=%s", mode, zone, service.key, len(picked),
                 reply.ok, reply.http_status, fdh_user)
        counts = {n.split(".")[0].upper(): max(c.count(b"\r\n") - 1, 0) for n, c in parts.items()}
        return {"ok": reply.ok, "zone": zone, "mode": mode, "fdh_user": fdh_user, "count": len(picked), "visits": len(vns),
                "http_status": reply.http_status, "status": reply.status, "message": reply.message, "counts": counts,
                "export_id": export_id}

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
