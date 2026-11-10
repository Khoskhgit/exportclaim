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


def rank_visits(visits: list[dict], pttypes: tuple[str, ...] = ()) -> list[dict]:
    """A day's visits, most likely dental claim first: under the dental right, then with dental work, then earliest."""
    return sorted([v for v in visits if v.get("vn")],
                  key=lambda v: (text(v.get("pttype")) not in pttypes, not v.get("has_dental"),
                                 str(v.get("vsttime") or ""), str(v["vn"])))


def pick_visit(visits: list[dict], pttypes: tuple[str, ...] = (), nth: int = 0) -> dict | None:
    """The visit for the nth claim of a person's day: several claims spread over the day's dental visits in time
    order of likelihood, and share the best one when there are fewer such visits than claims."""
    ranked = rank_visits(visits, pttypes)
    if not ranked:
        return None
    dental = [v for v in ranked if text(v.get("pttype")) in pttypes or v.get("has_dental")]
    return dental[nth] if nth < len(dental) else ranked[0]


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
        matches, rows, nth = {}, [], {}
        for claim in sorted(claims, key=lambda c: (c["service_date"], c["cid"], c["claim_date"], c["seq"])):
            day = (claim["cid"], claim["service_date"])
            nth[day] = nth.get(day, -1) + 1
            visit = pick_visit(found.get(day, []), settings.dental_pttypes, nth[day])
            person = names.get(claim["cid"], {})
            if visit:
                key = (claim["cid"], claim["service_date"], claim["service_type"], claim["claim_amount"], claim["seq"])
                matches[key] = (text(visit["hn"]), text(visit["vn"]))
            rows.append({
                "cid": mask_cid(claim["cid"]), "service_date": claim["service_date"], "service_type": claim["service_type"],
                "claim_date": claim["claim_date"], "claim_amount": claim["claim_amount"], "status": claim["status"],
                "paid_amount": claim["paid_amount"], "voucher": claim["voucher"], "transfer_date": claim["transfer_date"],
                "state": state_of(claim),
                "hn": text(visit["hn"]) if visit else text(person.get("hn")) or claim["hn"],
                "vn": text(visit["vn"]) if visit else (claim["vn"] if db_error else ""),
                "ptname": text(person.get("ptname")), "pttype": text(visit.get("pttype_name")) if visit else "",
                "has_dental": bool(visit and visit.get("has_dental")),
                "right_ok": bool(visit) and text(visit.get("pttype")) in settings.dental_pttypes,
                "visits_that_day": len(found.get((claim["cid"], claim["service_date"]), [])),
                "in_hosxp": bool(person),
            })
        if matches:
            claims_db.set_match(matches)
        # Visits under the dental right in the same period that no imported claim points to.
        unclaimed: list[dict] = []
        first, last = start or (min(c["service_date"] for c in claims) if claims else ""), \
            end or (max(c["service_date"] for c in claims) if claims else "")
        if first and last and not db_error:
            claimed = {vn for _, vn in matches.values()}
            try:
                for v in hosxp.visits_with_pttypes(settings.dental_pttypes, first, last):
                    if text(v["vn"]) in claimed:
                        continue
                    unclaimed.append({
                        "cid": mask_cid(text(v.get("cid"))), "service_date": str(v["vstdate"])[:10], "service_type": "",
                        "claim_date": "", "claim_amount": "", "status": "", "paid_amount": "", "voucher": "",
                        "transfer_date": "", "state": "unclaimed", "hn": text(v["hn"]), "vn": text(v["vn"]),
                        "ptname": text(v.get("ptname")), "pttype": text(v.get("pttype_name")),
                        "has_dental": bool(v.get("has_dental")), "right_ok": True, "visits_that_day": 1, "in_hosxp": True,
                    })
            except Exception as exc:
                db_error = f"หา visit ที่ยังไม่ส่งเบิกไม่ได้: {exc}"
        return {"claims": rows + unclaimed, "uploads": claims_db.uploads(), "db_error": db_error,
                "dental_pttypes": list(settings.dental_pttypes), "range": [first, last]}

    return claims_db
