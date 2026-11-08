"""NHSO 18-file page and API: search, review, leave items out, build the ZIP for New e-Claim upload."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import auth, nhso18, nhso18_service as svc
from .config import Settings
from .store import Store
from .validate import text

log = logging.getLogger("ssop.nhso18")
ECLAIM_DB = "eclaimdb"


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


def register(app: FastAPI, settings: Settings, hosxp: Any, local: Store, templates: Jinja2Templates) -> None:
    def q(sql: str) -> list[dict]:
        return hosxp._select(sql, [])

    @app.exception_handler(nhso18.Nhso18Error)
    async def nhso18_error(_: Request, exc: nhso18.Nhso18Error):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def review(rows: list[dict]) -> list[dict]:
        keys = [svc.key_of(r) for r in rows]
        found = svc.checks(q, settings.rcm_db, rows)
        sent = local.nhso18_sent(keys)
        excluded = local.nhso18_excluded(keys)
        out = []
        for r in rows:
            k = svc.key_of(r)
            f = found[k]
            st = svc.status(r, k in sent)
            moment = r.get("date")
            out.append({
                "key": k, "kind": r["kind"], "vn": text(r.get("vn")), "an": text(r.get("an")), "hn": text(r.get("hn")),
                "cid": _mask(r.get("cid")), "ptname": text(r.get("ptname")),
                "date": f"{moment:%Y-%m-%d}" if moment else "", "pttype": f"{text(r.get('pttype'))}: {text(r.get('pttype_name'))}",
                "maininscl": text(r.get("maininscl")), "pdx": text(r.get("pdx")), "claimcode": text(r.get("claimcode")),
                "money": svc.money(r.get("money_hosxp")), "fdh_amount": svc.money(r.get("fdh_amount")) if r.get("fdh_amount") else "",
                "errors": f["errors"], "warns": f["warns"], "status": st,
                "excluded_count": sum(1 for s, _ in excluded if s == k),
                "selectable": not f["errors"], "has_error": bool(f["errors"]), "has_warn": bool(f["warns"]),
                "already_sent": bool(st), "default_selected": not f["errors"] and not st,
            })
        return out

    @app.get("/nhso18", response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse(request, "nhso18.html", {"hname": settings.hname})

    @app.get("/api/nhso18/pttypes")
    def pttypes():
        return svc.pttypes(q)

    @app.post("/api/nhso18/search")
    def search(body: dict = Body(...)):
        opd, ipd = bool(body.get("opd", True)), bool(body.get("ipd", False))
        vn, an, hn = text(body.get("vn")), text(body.get("an")), text(body.get("hn"))
        start, end = _date(body.get("start"), "วันที่เริ่ม"), _date(body.get("end"), "วันที่สิ้นสุด")
        rights = [text(p) for p in body.get("pttypes") or [] if text(p)]
        if not (vn or an or hn) and not (start and end and rights):
            raise HTTPException(400, "ต้องระบุช่วงวันที่และสิทธิ หรือค้นด้วย VN / AN / HN")
        if not opd and not ipd and not (vn or an):
            raise HTTPException(400, "ต้องเลือก OPD หรือ IPD อย่างน้อย 1 อย่าง")
        rows = svc.search(q, settings.rcm_db, start, end, rights, opd or bool(vn), ipd or bool(an), vn, an, hn)
        return {"rows": review(rows)}

    @app.post("/api/nhso18/detail")
    def detail(body: dict = Body(...)):
        key, kind = text(body.get("key")), text(body.get("kind"))
        if not key:
            raise HTTPException(400, "ไม่ได้ระบุรายการ")
        col = "an" if kind == "IPD" else "vn"
        excluded = local.nhso18_excluded([key])
        items = q(f"""
        SELECT oo.icode, CONCAT_WS(' ',d.name,d.strength) name, inc.drg_chrgitem_id billmaud, d.nhso_adp_code adp,
          SUM(oo.qty) qty, SUM(oo.sum_price) amount,
          MAX(EXISTS(SELECT 1 FROM opitemrece_noexport no WHERE no.seq=oo.{col} AND no.icode=oo.icode)) hosxp_noexport
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode LEFT JOIN income inc ON inc.income=oo.income
        WHERE oo.{col}={nhso18.lit(key)}
        GROUP BY oo.icode ORDER BY inc.drg_chrgitem_id, oo.icode""")
        table = "iptdiag" if kind == "IPD" else "ovstdiag"
        dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM {table} WHERE {col}={nhso18.lit(key)} ORDER BY diagtype, icd10")
        return {
            "items": [{"icode": text(i["icode"]), "name": text(i["name"]), "billmaud": text(i["billmaud"]), "adp": text(i["adp"]),
                       "qty": nhso18.fmt(i["qty"], "C", 12), "amount": svc.money(i["amount"]),
                       "hosxp_excluded": bool(i["hosxp_noexport"]), "excluded": (key, text(i["icode"])) in excluded}
                      for i in items],
            "dx": [{"type": text(d["diagtype"]), "code": text(d["icd10"]), "name": text(d["name"])} for d in dx],
        }

    @app.post("/api/nhso18/item")
    def item(body: dict = Body(...)):
        key, icode = text(body.get("key")), text(body.get("icode"))
        if not key or not icode:
            raise HTTPException(400, "ข้อมูลไม่ครบ")
        local.set_nhso18_item(key, icode, bool(body.get("excluded")))
        return {"excluded_count": len(local.nhso18_excluded([key]))}

    @app.post("/api/nhso18/export")
    def export(request: Request, body: dict = Body(...)):
        picked = [p for p in body.get("rows") or [] if text(p.get("key"))]
        if not picked:
            raise HTTPException(400, "ยังไม่ได้เลือกรายการ")
        vns = list(dict.fromkeys(text(p["key"]) for p in picked if p.get("kind") != "IPD"))
        ans = list(dict.fromkeys(text(p["key"]) for p in picked if p.get("kind") == "IPD"))
        # Re-check with fresh data before building the files.
        rows = svc.search(q, settings.rcm_db, None, None, [], bool(vns), bool(ans), vn_list=vns, an_list=ans)
        reviewed = review(rows)
        blocked = [{"key": r["key"], "reason": r["errors"][0]["message"]} for r in reviewed if r["errors"]]
        if blocked:
            return JSONResponse({"detail": "มีรายการที่ส่งไม่ได้", "blocked": blocked}, status_code=409)
        resend = [r["key"] for r in reviewed if r["status"]]
        if resend and not body.get("confirm_resend"):
            return JSONResponse({"detail": "ต้องยืนยันการส่งซ้ำ", "resend": resend, "needs_confirm": True}, status_code=409)
        config, groups = svc.load_setup(q, settings.hcode)
        odsmis = svc._csv_quoted((q("SELECT sys_value FROM sys_var WHERE sys_name REGEXP 'ODSMIS' LIMIT 1") or [{}])[0].get("sys_value"))
        visits = nhso18.opd_contexts(q, vns, groups, config.zone) + svc.ipd_contexts(q, ans, groups, odsmis)
        if not visits:
            raise HTTPException(400, "ไม่พบรายการที่เลือก")
        excluded = local.nhso18_excluded(vns + ans)
        data = nhso18.Builder(q, config, settings.rcm_db, ECLAIM_DB, excluded).run(visits)
        result = nhso18.build_zip(data, settings.hcode, settings.export_dir)
        export_id = local.record_nhso18_export(result.zip_path, vns + ans, auth.user_label(request))
        log.info("nhso18 export id=%s visits=%s", export_id, len(visits))
        return {"id": export_id, "file": result.zip_path.name, "count": len(visits), "counts": result.counts}

    @app.get("/api/nhso18/exports")
    def exports():
        return [{**row, "file": Path(row.pop("zip_path")).name} for row in local.nhso18_history()]

    @app.get("/api/nhso18/exports/{export_id}/download")
    def download(export_id: int):
        path = local.nhso18_path(export_id)
        if path is None or not path.is_file():
            raise HTTPException(404, "ไม่พบไฟล์")
        return FileResponse(path, media_type="application/zip", filename=path.name)
