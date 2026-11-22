"""The SSO disability page (ค่าบริการทางการแพทย์ผู้ทุพพลภาพ): import the send / payment reports and check each claim
against HOSxP, and list the visits under the disability right that no imported claim points to (not sent yet)."""
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
from .dental_routes import mask_cid
from .disabled import PAYMENT, DisabledError, parse_report
from .disabled_store import DisabledStore
from .repstm_routes import safe_name
from .store import Store
from .validate import text

log = logging.getLogger("ssop")
MAX_UPLOAD = 20 * 1024 * 1024
KIND_NAMES = {"submission": "รายงานการส่งเบิก", PAYMENT: "รายงานการจ่าย"}


def state_of(claim: dict) -> str:
    if claim["paid_amount"]:
        return "paid"
    status = claim["status"]
    if "ไม่อนุมัติ" in status or "ปฏิเสธ" in status:
        return "rejected"
    if "จ่ายแล้ว" in status:
        return "paid_no_detail"
    return "waiting" if status else "unknown"


def pick_visit(visits: list[dict], pttypes: tuple[str, ...], nth: int = 0) -> dict | None:
    """The visit for the nth claim of a person's day and kind: under the disability right first, then in time order."""
    ranked = sorted(visits, key=lambda v: (text(v.get("pttype")) not in pttypes, str(v.get("vsttime") or ""), text(v.get("vn"))))
    if not ranked:
        return None
    return ranked[nth] if nth < len(ranked) else ranked[0]


def _money(value) -> str:
    try:
        return f"{float(value or 0):.2f}"
    except (TypeError, ValueError):
        return ""


def register(app: FastAPI, settings: Settings, hosxp: Any, local: Store, templates: Jinja2Templates) -> DisabledStore:
    claims_db = DisabledStore(local)
    folder = local.path.parent / "disabled"

    @app.get("/disabled", response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse(request, "disabled.html", {"hname": settings.hname})

    @app.post("/api/disabled/import")
    async def import_file(request: Request, name: str = ""):
        data = await request.body()
        if not data:
            raise HTTPException(400, "ไม่ได้เลือกไฟล์")
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "ไฟล์ใหญ่เกินไป")
        name = safe_name(name)
        try:
            report = parse_report(data)
        except DisabledError as exc:
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
        log.info("disabled import %s kind=%s rows=%s", name, report.kind, len(report.rows))
        return {
            "name": name, "kind": KIND_NAMES[report.kind], "rows": len(report.rows), "total": report.total,
            "period_start": report.period_start, "period_end": report.period_end, **result,
            "again": f"เคยนำเข้าไฟล์นี้แล้วเมื่อ {before['at'].replace('T', ' ')}" if before else "",
        }

    @app.post("/api/disabled/list")
    def listing(body: dict = Body(...)):
        start, end = text(body.get("start")), text(body.get("end"))
        for value in (start, end):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise HTTPException(400, "วันที่ไม่ถูกต้อง") from None
        claims = claims_db.claims(start, end)
        days = [c["admit_date"] for c in claims]
        first = start or (min(days) if days else "")
        last = end or (max(days) if days else "")
        visits: list[dict] = []
        db_error = ""
        if first and last:
            try:
                visits = hosxp.disabled_visits(min([first, *days]), max([last, *days]), settings.disabled_pttypes,
                                               {c["cid"] for c in claims})
            except Exception as exc:  # HOSxP down: still show what was imported
                db_error = f"จับคู่กับ HOSxP ไม่ได้: {exc}"
        found: dict[tuple[str, str, str], list[dict]] = {}
        people: dict[str, dict] = {}
        for v in visits:
            people[text(v["cid"])] = v
            found.setdefault((text(v["cid"]), str(v["admit_date"])[:10], text(v["kind"])), []).append(v)
        matches, rows, nth = {}, [], {}
        used: set[tuple[str, str]] = set()

        def row_of(v: dict | None, person: dict, claim: dict | None) -> dict:
            right = _money(v.get("right_money")) if v else ""
            return {
                "cid": mask_cid(text((claim or v or {}).get("cid"))),
                "admit_date": claim["admit_date"] if claim else str(v["admit_date"])[:10],
                "discharge_date": (claim["discharge_date"] if claim else "") or (str(v["discharge_date"] or "")[:10] if v else ""),
                "kind": claim["kind"] if claim else text(v["kind"]),
                "claim_date": claim["claim_date"] if claim else "", "claim_amount": claim["claim_amount"] if claim else "",
                "status": claim["status"] if claim else "", "paid_amount": claim["paid_amount"] if claim else "",
                "voucher": claim["voucher"] if claim else "", "transfer_date": claim["transfer_date"] if claim else "",
                "state": state_of(claim) if claim else "unclaimed",
                "hn": text(v["hn"]) if v else text(person.get("hn")) or (claim or {}).get("hn", ""),
                "vn": text(v["vn"]) if v else "", "an": text(v["an"]) if v else "",
                "ptname": text(person.get("ptname")), "pttype": text(v.get("pttype_name")) if v else "",
                "right_ok": bool(v) and text(v.get("pttype")) in settings.disabled_pttypes,
                "income": _money(v.get("income")) if v else "", "right_money": right,
                "visits_that_day": len(found.get((text((claim or v)["cid"]), (claim or {}).get("admit_date") or str(v["admit_date"])[:10],
                                                  (claim or {}).get("kind") or text(v["kind"])), [])),
                "in_hosxp": bool(person),
            }

        for claim in sorted(claims, key=lambda c: (c["admit_date"], c["cid"], c["claim_date"], c["seq"])):
            day = (claim["cid"], claim["admit_date"], claim["kind"])
            nth[day] = nth.get(day, -1) + 1
            visit = pick_visit(found.get(day, []), settings.disabled_pttypes, nth[day])
            if visit:
                used.add((text(visit["kind"]), text(visit["an"]) or text(visit["vn"])))
                key = (claim["cid"], claim["admit_date"], claim["kind"], claim["claim_amount"], claim["seq"])
                matches[key] = (text(visit["hn"]), text(visit["vn"]), text(visit["an"]))
            rows.append(row_of(visit, people.get(claim["cid"], {}), claim))
        if matches:
            claims_db.set_match(matches)
        unclaimed = [row_of(v, v, None) for v in visits
                     if text(v.get("pttype")) in settings.disabled_pttypes
                     and (text(v["kind"]), text(v["an"]) or text(v["vn"])) not in used
                     and (not first or str(v["admit_date"])[:10] >= first) and (not last or str(v["admit_date"])[:10] <= last)]
        return {"claims": rows + unclaimed, "uploads": claims_db.uploads(), "db_error": db_error,
                "pttypes": list(settings.disabled_pttypes), "range": [first, last]}

    return claims_db
