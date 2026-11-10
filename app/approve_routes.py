"""The Approve Code page (เบิกจ่ายตรงข้าราชการ, Krungthai): import the bank's daily TXT and show each code against HOSxP.

The 18-file export uses the same matching (Approvals.for_visits) to put the Approve Code in INS.PERMITNO of visits
under the approve rights (OFC_APPROVE_PTTYPES), in place of the claim code HOSxP holds. HOSxP is not changed.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date
from typing import Any, Iterable

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import auth
from .approve import PAYMENT, ApproveError, assign, cancelled, parse_upload
from .approve_store import ApproveStore
from .config import Settings
from .dental_routes import mask_cid
from .repstm_routes import safe_name
from .store import Store
from .validate import text

log = logging.getLogger("ssop")
MAX_UPLOAD = 20 * 1024 * 1024


def _visit(v: dict) -> dict:
    """A HOSxP visit in the shape assign() reads."""
    return {**v, "cid": text(v.get("cid")), "vn": text(v.get("vn")), "date": str(v.get("vstdate") or "")[:10],
            "time": str(v.get("vsttime") or ""), "income": v.get("income"), "pttype": text(v.get("pttype"))}


class Approvals:
    def __init__(self, store: ApproveStore, hosxp: Any, pttypes: tuple[str, ...]):
        self.store, self.hosxp, self.pttypes = store, hosxp, pttypes

    def match(self, rows: list[dict], visits: list[dict]) -> tuple[dict[str, dict], dict[str, str]]:
        """(Approve Code -> visit, payment Approve Code -> the refund that took it back)."""
        taken = cancelled(rows)
        payments = [r for r in rows if r["kind"] == PAYMENT and r["appr_code"] not in taken]
        return assign(payments, [_visit(v) for v in visits], self.pttypes), taken

    def for_visits(self, visits: Iterable[dict]) -> dict[str, dict]:
        """VN -> the payment that belongs to it, for OPD visits (cid, vn, date) under the approve rights."""
        mine = [v for v in visits if text(v.get("pttype")) in self.pttypes and text(v.get("vn")) and text(v.get("cid"))]
        if not mine:
            return {}
        rows = self.store.of_cids(text(v["cid"]) for v in mine)
        if not rows:
            return {}
        days = [str(v.get("date") or v.get("vstdate"))[:10] for v in mine]
        others = self.hosxp.ofc_visits(min(days), max(days), cids={text(v["cid"]) for v in mine})
        found, _ = self.match(rows, others)
        return {text(v["vn"]): next(r for r in rows if r["appr_code"] == code) for code, v in found.items()}


def register(app: FastAPI, settings: Settings, hosxp: Any, local: Store, templates: Jinja2Templates) -> Approvals:
    store = ApproveStore(local)
    approvals = Approvals(store, hosxp, settings.ofc_approve_pttypes)
    folder = local.path.parent / "approve"

    @app.get("/approve", response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse(request, "approve.html", {"hname": settings.hname})

    @app.post("/api/approve/import")
    async def import_file(request: Request, name: str = ""):
        data = await request.body()
        if not data:
            raise HTTPException(400, "ไม่ได้เลือกไฟล์")
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "ไฟล์ใหญ่เกินไป")
        name = safe_name(name)
        try:
            files, skipped = parse_upload(name, data)
        except ApproveError as exc:
            raise HTTPException(400, f"{name}: {exc}") from None
        for member, parsed in files:
            if settings.hcode and parsed.hcode and parsed.hcode != settings.hcode:  # an EDC-only CSV names no hospital
                raise HTTPException(400, f"{member}: เป็นไฟล์ของสถานพยาบาล {parsed.hcode} ไม่ใช่ {settings.hcode}")
        digest = hashlib.sha256(data).hexdigest()
        before = store.seen(digest)
        folder.mkdir(parents=True, exist_ok=True)
        saved = folder / f"{digest[:12]}_{name}"
        if not saved.exists():
            saved.write_bytes(data)
        user, results = auth.user_label(request), []
        for member, parsed in files:
            result = store.save(parsed, member, digest, saved.name, created_by=user)
            payments = [r for r in parsed.rows if r.kind == PAYMENT]
            results.append({"name": member, "file_date": parsed.file_date, "rows": len(parsed.rows),
                            "payments": len(payments), "refunds": len(parsed.rows) - len(payments), "total": parsed.total,
                            **result})
            log.info("approve import %s rows=%s", member, len(parsed.rows))
        return {"name": name, "files": results, "skipped": skipped,
                "again": f"เคยนำเข้าไฟล์นี้แล้วเมื่อ {before['at'].replace('T', ' ')}" if before else ""}

    @app.post("/api/approve/list")
    def listing(body: dict = Body(...)):
        start, end = text(body.get("start")), text(body.get("end"))
        for value in (start, end):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise HTTPException(400, "วันที่ไม่ถูกต้อง") from None
        rows = store.rows(start, end)
        # Refunds in a later file, or payments a refund in this range takes back, need the patient's whole history.
        history = store.of_cids({r["cid"] for r in rows}) if rows else []
        first = start or (min(r["txn_date"] for r in rows) if rows else "")
        last = end or (max(r["txn_date"] for r in rows) if rows else "")
        visits: list[dict] = []
        db_error = ""
        if first and last:
            try:
                visits = hosxp.ofc_visits(first, last, cids={r["cid"] for r in rows}, pttypes=settings.ofc_approve_pttypes)
            except Exception as exc:  # HOSxP down: still show what was imported
                db_error = f"จับคู่กับ HOSxP ไม่ได้: {exc}"
        found, taken = approvals.match(history, visits)
        names = {text(v["cid"]): v for v in visits}
        by_code = {r["appr_code"]: r for r in history}
        out = []
        for r in rows:
            visit = found.get(r["appr_code"]) if r["kind"] == PAYMENT else None
            person = names.get(r["cid"], {})
            refund_of = next((p for p, f in taken.items() if f == r["appr_code"]), "")
            if r["kind"] != PAYMENT:
                state = "refund"
            elif r["appr_code"] in taken:
                state = "refunded"
            elif not visit:
                state = "no_visit"
            elif visit["pttype"] not in settings.ofc_approve_pttypes:
                state = "wrong_right"
            else:
                state = "matched"
            income = visit.get("income") if visit else None
            out.append({
                "appr_code": r["appr_code"], "trans_ref": r["trans_ref"], "kind": r["kind"], "state": state,
                "cid": mask_cid(r["cid"]), "txn_date": r["txn_date"], "txn_time": r["txn_time"],
                "post_date": r["post_date"], "amount": r["amount"], "channel": r["channel"], "terminal": r["terminal"],
                "reason": r["reason"], "refunded_by": taken.get(r["appr_code"], ""),
                "refund_of": refund_of, "refund_of_date": by_code.get(refund_of, {}).get("txn_date", ""),
                "vn": visit["vn"] if visit else "", "hn": text((visit or person).get("hn")),
                "ptname": text(person.get("ptname")), "pttype": text(visit.get("pttype_name")) if visit else "",
                "visit_date": visit["date"] if visit else "", "visit_time": visit["time"] if visit else "",
                "income": f"{float(income):.2f}" if income is not None else "",
                "claim_code": text(visit.get("claim_code")) if visit else "", "in_hosxp": bool(person),
            })
        # A visit still waiting for its code counts only on a day whose bank file is in: other days are not known yet.
        used, covered = {v["vn"] for v in found.values()}, store.file_dates()
        for v in visits:
            if text(v.get("pttype")) not in settings.ofc_approve_pttypes or text(v["vn"]) in used \
                    or str(v["vstdate"])[:10] not in covered:
                continue
            income = v.get("income")
            out.append({
                "appr_code": "", "trans_ref": "", "kind": "", "state": "missing", "cid": mask_cid(text(v.get("cid"))),
                "txn_date": str(v["vstdate"])[:10], "txn_time": "", "post_date": "", "amount": "", "channel": "",
                "terminal": "", "reason": "", "refunded_by": "", "refund_of": "", "refund_of_date": "",
                "vn": text(v["vn"]), "hn": text(v["hn"]), "ptname": text(v.get("ptname")), "pttype": text(v.get("pttype_name")),
                "visit_date": str(v["vstdate"])[:10], "visit_time": str(v.get("vsttime") or ""),
                "income": f"{float(income):.2f}" if income is not None else "", "claim_code": text(v.get("claim_code")),
                "in_hosxp": True,
            })
        return {"rows": out, "uploads": store.uploads(), "db_error": db_error, "range": [first, last],
                "covered": sorted(covered),
                "pttypes": list(settings.ofc_approve_pttypes)}

    return approvals
