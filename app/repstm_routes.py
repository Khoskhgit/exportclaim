"""The REP/STM page: import the SSO reply ZIPs and look at what came back."""
from __future__ import annotations

import hashlib
import logging
import re

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import auth
from .config import Settings
from .repstm import RepStmError, parse_upload
from .repstm_store import RepStmStore
from .store import Store
from .validate import text

log = logging.getLogger("ssop")
MAX_UPLOAD = 30 * 1024 * 1024


def safe_name(name: str) -> str:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"[^\w.-]", "_", base)[:120] or "upload"


def summary_text(parsed) -> str:
    parts = [f"REP {r.rep_no} ({len(r.bills)} รายการ, ไม่ผ่าน {sum(b.stat != 'A' for b in r.bills)})" if r.has_bil
             else f"REP {r.rep_no} (ยา {len(r.disps)} ใบ)" for r in parsed.reps.values()]
    parts += [f"STM {s.stm_no} ({len(s.bills)} รายการ {s.amount} บาท)" for s in parsed.stms.values()]
    return ", ".join(parts)


def register(app: FastAPI, settings: Settings, local: Store, templates: Jinja2Templates) -> RepStmStore:
    results = RepStmStore(local)
    folder = local.path.parent / "rep_stm"

    @app.get("/repstm", response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse(request, "repstm.html", {"hname": settings.hname})

    @app.post("/api/repstm/import")
    async def import_file(request: Request, name: str = ""):
        data = await request.body()
        if not data:
            raise HTTPException(400, "ไม่ได้เลือกไฟล์")
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "ไฟล์ใหญ่เกินไป")
        name = safe_name(name)
        try:
            parsed = parse_upload(name, data)
        except RepStmError as exc:
            raise HTTPException(400, str(exc)) from None
        digest = hashlib.sha256(data).hexdigest()
        before = results.seen(digest)
        folder.mkdir(parents=True, exist_ok=True)
        saved = folder / f"{digest[:12]}_{name}"
        if not saved.exists():
            saved.write_bytes(data)
        summary = summary_text(parsed)
        results.save(parsed, name, digest, saved.name, summary, created_by=auth.user_label(request))
        log.info("repstm import %s reps=%s stms=%s", name, len(parsed.reps), len(parsed.stms))
        return {
            "name": name, "summary": summary, "skipped": parsed.skipped,
            "again": f"เคยนำเข้าไฟล์นี้แล้วเมื่อ {before['at'].replace('T', ' ')} ข้อมูลถูกแทนที่ด้วยชุดเดิม" if before else "",
            "reps": [r.rep_no for r in parsed.reps.values()], "stms": [s.stm_no for s in parsed.stms.values()],
        }

    @app.get("/api/repstm/list")
    def listing():
        return {"reps": results.reps(), "stms": results.stms(), "uploads": results.uploads()}

    @app.post("/api/repstm/rep")
    def rep_detail(body: dict = Body(...)):
        found = results.rep_detail(text(body.get("rep_no")))
        if not found:
            raise HTTPException(404, "ไม่พบ REP นี้")
        return {**found, "code_names": results.code_names()}

    @app.post("/api/repstm/stm")
    def stm_detail(body: dict = Body(...)):
        found = results.stm_detail(text(body.get("stm_no")))
        if not found:
            raise HTTPException(404, "ไม่พบ STM นี้")
        return found

    @app.post("/api/repstm/vn")
    def vn_history(body: dict = Body(...)):
        vn = text(body.get("vn"))
        if not vn:
            raise HTTPException(400, "ไม่ได้ระบุ VN")
        return {**results.vn_history(vn), "code_names": results.code_names()}

    return results
