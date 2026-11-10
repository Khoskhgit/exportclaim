"""The SSO dental page: import the submission and payment reports and show each claim against HOSxP."""
from __future__ import annotations

import hashlib
import logging
from datetime import date
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import auth
from .config import Settings
from .dental import PAYMENT, DentalError, parse_report
from .dental_store import DentalStore
from .repstm_routes import safe_name
from .store import Store
from .validate import text

log = logging.getLogger("ssop")
MAX_UPLOAD = 20 * 1024 * 1024
KIND_NAMES = {"submission": "รายงานการส่งเบิก", PAYMENT: "รายงานการจ่าย"}


def mask_cid(cid: str) -> str:
    return f"{'x' * 9}{cid[-4:]}" if len(cid) == 13 else cid


def pick_visit(visits: list[dict]) -> dict | None:
    """The visit a claim belongs to: one with dental work first, then the earliest of the day."""
    with_vn = [v for v in visits if v.get("vn")]
    if not with_vn:
        return None
    return sorted(with_vn, key=lambda v: (not v.get("has_dental"), str(v.get("vsttime") or ""), str(v["vn"])))[0]


def state_of(claim: dict) -> str:
    if claim["paid_amount"]:
        return "paid"
    status = claim["status"]
    if "ไม่อนุมัติ" in status:
        return "rejected"
    if "จ่ายแล้ว" in status:
        return "paid_no_detail"
    return "waiting" if status else "unknown"


def register(app: FastAPI, settings: Settings, hosxp: Any, local: Store, templates: Jinja2Templates) -> DentalStore:
    claims_db = DentalStore(local)
    folder = local.path.parent / "dental"

    @app.get("/dental", response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse(request, "dental.html", {"hname": settings.hname})

    @app.post("/api/dental/import")
    async def import_file(request: Request, name: str = ""):
        data = await request.body()
        if not data:
            raise HTTPException(400, "ไม่ได้เลือกไฟล์")
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "ไฟล์ใหญ่เกินไป")
        name = safe_name(name)
        try:
            report = parse_report(data)
        except DentalError as exc:
            raise HTTPException(400, f"{name}: {exc}") from None
        if report.hcode and settings.hcode and report.hcode != settings.hcode:
            raise HTTPException(400, f"{name}: เป็นรายงานของสถานพยาบาล {report.hcode} ไม่ใช่ {settings.hcode}")
        digest = hashlib.sha256(data).hexdigest()
        before = claims_db.seen(digest)
        folder.mkdir(parents=True, exist_ok=True)
        saved = folder / f"{digest[:12]}_{name}"
        if not saved.exists():
            saved.write_bytes(data)
        result = claims_db.save(report, name, digest, saved.name, created_by=auth.user_label(request))
        log.info("dental import %s kind=%s rows=%s", name, report.kind, len(report.rows))
        return {
            "name": name, "kind": KIND_NAMES[report.kind], "rows": len(report.rows), "total": report.total,
            "period_start": report.period_start, "period_end": report.period_end, **result,
            "again": f"เคยนำเข้าไฟล์นี้แล้วเมื่อ {before['at'].replace('T', ' ')}" if before else "",
        }

    @app.post("/api/dental/list")
    def listing(body: dict = Body(...)):
        start, end = text(body.get("start")), text(body.get("end"))
        for value in (start, end):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise HTTPException(400, "วันที่ไม่ถูกต้อง") from None
        claims = claims_db.claims(start, end)
        found: dict[tuple[str, str], list[dict]] = {}
        names: dict[str, dict] = {}
        db_error = ""
        if claims:
            days = [c["service_date"] for c in claims]
            try:
                for v in hosxp.dental_visits({c["cid"] for c in claims}, min(days), max(days)):
                    names[text(v["cid"])] = v
                    if v.get("vstdate"):
                        found.setdefault((text(v["cid"]), str(v["vstdate"])[:10]), []).append(v)
            except Exception as exc:  # HOSxP down: still show what was imported
                db_error = f"จับคู่กับ HOSxP ไม่ได้: {exc}"
        matches, rows = {}, []
        for claim in claims:
            visit = pick_visit(found.get((claim["cid"], claim["service_date"]), []))
            person = names.get(claim["cid"], {})
            if visit:
                matches[(claim["cid"], claim["service_date"])] = (text(visit["hn"]), text(visit["vn"]))
            rows.append({
                "cid": mask_cid(claim["cid"]), "service_date": claim["service_date"], "service_type": claim["service_type"],
                "claim_date": claim["claim_date"], "claim_amount": claim["claim_amount"], "status": claim["status"],
                "paid_amount": claim["paid_amount"], "voucher": claim["voucher"], "transfer_date": claim["transfer_date"],
                "state": state_of(claim),
                "hn": text(visit["hn"]) if visit else text(person.get("hn")) or claim["hn"],
                "vn": text(visit["vn"]) if visit else (claim["vn"] if db_error else ""),
                "ptname": text(person.get("ptname")), "pttype": text(visit.get("pttype_name")) if visit else "",
                "has_dental": bool(visit and visit.get("has_dental")),
                "visits_that_day": len(found.get((claim["cid"], claim["service_date"]), [])),
                "in_hosxp": bool(person),
            })
        if matches:
            claims_db.set_match(matches)
        return {"claims": rows, "uploads": claims_db.uploads(), "db_error": db_error}

    return claims_db
