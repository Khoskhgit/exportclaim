"""AIPN pages and API: search admissions, review, choose, and build the AIPN ZIP."""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import aipn, aipn_service, auth
from .aipn_db import AipnDb
from .config import Settings
from .store import Store
from .validate import text

log = logging.getLogger("ssop.aipn")


def _date(value: Any, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(400, f"{label} ไม่ถูกต้อง") from None


def _mask(value: Any) -> str:
    cid = text(value)
    return f"{'x' * (len(cid) - 4)}{cid[-4:]}" if len(cid) > 4 else cid


def register(app: FastAPI, settings: Settings, db: AipnDb, local: Store, templates: Jinja2Templates) -> None:
    @app.exception_handler(aipn.AipnError)
    async def aipn_error(_: Request, exc: aipn.AipnError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def reviews(admissions: list[dict]) -> tuple[aipn_service.Bundle, dict[str, dict]]:
        ans = [text(a["an"]) for a in admissions]
        bundle = aipn_service.fetch(db, admissions, local.aipn_prices(ans))
        sent = local.aipn_sent(ans)
        excluded, included = local.aipn_item_choices(ans)
        care = local.aipn_care_as(ans)
        out = {}
        for adm in admissions:
            an = text(adm["an"])
            items = bundle.items.get(an, [])
            rows = aipn_service.rows_for(bundle, adm, excluded.get(an, set()), included.get(an, set()))
            default_care = aipn.default_care_as(aipn.hmain(adm), settings.hcode, settings.hosp_main)
            out[an] = {
                "an": an, "hn": text(adm.get("hn")), "cid": _mask(adm.get("cid")),
                "ptname": text(adm.get("ptname")),
                "admit_at": aipn.iso(aipn.admit_at(adm)).replace("T", " "),
                "discharge_at": aipn.iso(aipn.discharge_at(adm)).replace("T", " "),
                "pdx": text(adm.get("pdx")), "pttype": f"{text(adm.get('pttype'))}: {text(adm.get('pttype_name'))}",
                "pay_plan": aipn.pay_plan(adm), "hmain": aipn.hmain(adm),
                "care_as": care.get(an) or default_care, "care_as_default": default_care,
                "auth_code": text(adm.get("auth_code")),
                "income": aipn.money(adm.get("income")),
                "excluded_count": len({aipn.item_key(i) for i in items if aipn.exported(i) and aipn.is_excluded(
                    i, excluded.get(an, set()), included.get(an, set()))}),
                **aipn.amounts(rows),
                **aipn.review(adm, items, bundle.ops.get(an, []), settings.hosp_main, an in sent, an in bundle.missing_debt,
                              excluded.get(an, set()), included.get(an, set())),
            }
        return bundle, out

    @app.get("/aipn", response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse(request, "aipn.html", {"hname": settings.hname})

    @app.get("/api/aipn/pttypes")
    def pttypes():
        return db.pttypes()

    @app.post("/api/aipn/search")
    def search(body: dict = Body(...)):
        admissions = db.search(
            _date(body.get("start"), "วันที่เริ่ม"), _date(body.get("end"), "วันที่สิ้นสุด"),
            body.get("pttypes") or [], text(body.get("an")),
        )
        _, out = reviews(admissions)
        return {"admissions": list(out.values()), "next_session": local.next_aipn_session()}

    @app.post("/api/aipn/detail")
    def detail(body: dict = Body(...)):
        an = text(body.get("an"))
        admissions = db.by_an([an]) if an else []
        if not admissions:
            raise HTTPException(404, "ไม่พบ AN นี้")
        adm = admissions[0]
        bundle = aipn_service.fetch(db, admissions, local.aipn_prices([an]))
        items = bundle.items.get(an, [])
        excluded, included = local.aipn_item_choices([an])
        ex, inc = excluded.get(an, set()), included.get(an, set())
        everything = aipn.bill_rows(items, bundle.catalog, text(adm.get("pdx")).upper()[:4],
                                    excluded=set(), included={aipn.item_key(i) for i in items})
        by_key = {aipn.item_key(i): i for i in items}
        rows = [{
            "key": r.key, "icode": r.icode, "name": r.name, "billgroup": r.billgroup, "claimcat": r.claimcat,
            "std": f"{r.codesys} {r.stdcode}".strip() or r.cscode, "qty": aipn.number(r.qty),
            "amount": aipn.money(r.amount), "servdate": f"{r.servdate:%Y-%m-%d}",
            "excluded": aipn.is_excluded(by_key[r.key], ex, inc),
            "hosxp_excluded": bool(by_key[r.key].get("hosxp_noexport")),
            "unitprice": aipn.money(r.unitprice),
            "hosxp_unitprice": aipn.money(abs(aipn.dec(by_key[r.key].get("hosxp_unitprice")))),
            "chi_price": None if aipn.chi_price(by_key[r.key]) is None else aipn.money(aipn.chi_price(by_key[r.key])),
            "price_edited": bool(by_key[r.key].get("price_edited")),
            "price_wrong": aipn.price_wrong(by_key[r.key]),
        } for r in everything]
        skipped = sorted({text(i.get("icode")) for i in items if not aipn.exported(i)})
        claim = aipn_service.claims(db, bundle, settings, excluded, included, local.aipn_care_as([an]))[0]
        return {
            "items": rows, "skipped": skipped,
            "dx": [{"type": text(d.get("subclass")), "code": text(d.get("code")), "name": text(d.get("name"))} for d in claim.dx],
            "ops": [{"line": line.rstrip("\r\n")} for line in aipn.op_lines(claim.ops)],
        }

    @app.post("/api/aipn/item")
    def item(body: dict = Body(...)):
        an, key = text(body.get("an")), text(body.get("item"))
        if not an or not key:
            raise HTTPException(400, "ข้อมูลไม่ครบ")
        admissions = db.by_an([an])
        if not admissions:
            raise HTTPException(404, "ไม่พบ AN นี้")
        items = db.items([an])
        matches = [i for i in items if aipn.item_key(i) == key]
        if not matches:
            raise HTTPException(404, "ไม่พบรายการนี้")
        in_hosxp = bool(matches[0].get("hosxp_noexport"))
        want_excluded = bool(body.get("excluded"))
        local.set_aipn_item(an, key, None if want_excluded == in_hosxp else not want_excluded)
        _, out = reviews(admissions)
        return out[an]

    @app.post("/api/aipn/price")
    def price(body: dict = Body(...)):
        """Set the unit price sent for one item. Empty or null goes back to the HOSxP price."""
        an, key = text(body.get("an")), text(body.get("item"))
        if not an or not key:
            raise HTTPException(400, "ข้อมูลไม่ครบ")
        raw = body.get("unitprice")
        value = None
        if raw is not None and text(raw) != "":
            try:
                value = Decimal(text(raw).replace(",", ""))
            except InvalidOperation:
                raise HTTPException(400, "ราคาต้องเป็นตัวเลข") from None
            if not value.is_finite() or value < 0 or value >= Decimal("10000000") or value != value.quantize(Decimal("0.01")):
                raise HTTPException(400, "ราคาต้องเป็นตัวเลขไม่ติดลบ และมีทศนิยมไม่เกิน 2 ตำแหน่ง")
        admissions = db.by_an([an])
        if not admissions:
            raise HTTPException(404, "ไม่พบ AN นี้")
        matches = [i for i in db.items([an]) if aipn.item_key(i) == key]
        if not matches:
            raise HTTPException(404, "ไม่พบรายการนี้")
        if value is not None and value == abs(aipn.dec(matches[0].get("unitprice"))):
            value = None  # same as HOSxP: nothing to override
        local.set_aipn_price(an, key, value)
        _, out = reviews(admissions)
        return out[an]

    @app.post("/api/aipn/careas")
    def care_as(body: dict = Body(...)):
        an, value = text(body.get("an")), text(body.get("care_as")).upper()
        if not an or (value and value not in aipn.CARE_AS):
            raise HTTPException(400, "CareAs ต้องเป็น M, B, A หรือ X")
        local.set_aipn_care_as(an, value or None)
        return {"ok": True}

    @app.post("/api/aipn/export")
    def export(request: Request, body: dict = Body(...)):
        ans = list(dict.fromkeys(text(a) for a in body.get("ans") or [] if text(a)))
        appeal = text(body.get("appeal")).upper()
        if not ans:
            raise HTTPException(400, "ยังไม่ได้เลือกรายการ")
        if appeal not in aipn.APPEALS:
            raise HTTPException(400, "ประเภทการส่งไม่ถูกต้อง")
        aipn.normalise_session(text(body.get("session")))
        # Re-check on the server with fresh data: the screen may be out of date.
        admissions = db.by_an(ans)
        missing = set(ans) - {text(a["an"]) for a in admissions}
        if missing:
            raise HTTPException(400, f"ไม่พบ AN {len(missing)} รายการ")
        bundle, out = reviews(admissions)
        blocked = [
            {"an": an, "reason": r["special"] or next(i["message"] for i in r["issues"] if i["level"] == "error")}
            for an, r in out.items() if not r["selectable"]
        ]
        if blocked:
            return JSONResponse({"detail": "มีรายการที่ส่งไม่ได้", "blocked": blocked}, status_code=409)
        resend = [an for an, r in out.items() if r["status"]]
        if resend and not body.get("confirm_resend"):
            return JSONResponse({"detail": "ต้องยืนยันการส่งซ้ำ", "resend": resend, "needs_confirm": True}, status_code=409)
        excluded, included = local.aipn_item_choices(ans)
        claims = aipn_service.claims(db, bundle, settings, excluded, included, local.aipn_care_as(ans))
        result = aipn.build_zip(claims, settings.hcode, settings.hname, text(body.get("session")), appeal,
                                settings.export_dir)
        export_id = local.record_aipn_export(text(body.get("session")), appeal, result.zip_path, ans, result.total,
                                             created_by=auth.user_label(request))
        log.info("aipn export id=%s admissions=%s", export_id, len(ans))
        return {"id": export_id, "file": result.zip_path.name, "count": result.count, "total": aipn.money(result.total)}

    @app.get("/api/aipn/exports")
    def exports():
        return [{**row, "file": Path(row.pop("zip_path")).name} for row in local.aipn_history()]

    @app.get("/api/aipn/exports/{export_id}/download")
    def download(export_id: int):
        path = local.aipn_export_path(export_id)
        if path is None or not path.is_file():
            raise HTTPException(404, "ไม่พบไฟล์")
        return FileResponse(path, media_type="application/zip", filename=path.name)

