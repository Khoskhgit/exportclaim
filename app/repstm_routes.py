"""The REP/STM page: import the SSO reply ZIPs and look at what came back."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from . import auth, eclaim_codes, fdh_claims
from .config import Settings
from .repstm import RepStmError, parse_upload
from .repstm_store import RepStmStore
from .store import Store
from .validate import text

log = logging.getLogger("ssop")
MAX_UPLOAD = 30 * 1024 * 1024
USERSCRIPT = Path(__file__).with_name("eclaim_rep.user.js")


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
    claims = fdh_claims.FdhClaimStore(local)
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
        if fdh_claims.is_claim_detail(name, data):  # FDH รายละเอียดรายการเคลม (ClaimDetail.xlsx)
            try:
                rows = fdh_claims.parse(data)
            except fdh_claims.ClaimDetailError as exc:
                raise HTTPException(400, f"{name}: {exc}") from None
            saved = claims.save(rows, name)
            funds = sorted({r["fund"] for r in rows if r["fund"]})
            summary = (f"FDH ClaimDetail {'/'.join(funds) or '-'} {saved['rows']} รายการ "
                       f"(ใหม่ {saved['added']}, อัปเดต {saved['updated']})")
            results.log_upload(name, hashlib.sha256(data).hexdigest(), summary, auth.user_label(request))
            log.info("fdh claim detail import %s rows=%s", name, saved["rows"])
            return {"name": name, "summary": summary, "skipped": [], "again": "", "reps": [], "stms": []}
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

    @app.post("/api/repstm/known")
    def known(body: dict = Body(...)):
        rep_nos = body.get("rep_nos") or []
        if not isinstance(rep_nos, list):
            raise HTTPException(400, "rep_nos ต้องเป็นรายการ")
        return {"known": results.known_reps(rep_nos)}

    @app.get("/repstm/eclaim-rep.user.js")
    def userscript(request: Request):
        """The Tampermonkey script that pulls e-Claim REP Excel files into this app, with a new token of the
        logged-in user built in (only its hash is stored)."""
        user = request.state.user or {"user_id": "local", "name": "local"}
        token = secrets.token_urlsafe(32)
        local.create_script_token(auth.token_hash(token), user["user_id"], user["name"])
        base = str(request.base_url).rstrip("/")
        host = request.url.hostname or "localhost"
        script = (USERSCRIPT.read_text(encoding="utf-8")
                  .replace("__APP_URL__", json.dumps(base)[1:-1]).replace("__APP_HOST__", host)
                  .replace("__TOKEN__", token).replace("__HCODE__", settings.hcode))
        log.info("eclaim userscript token for %s", user["user_id"])
        return Response(script, media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/repstm/list")
    def listing():
        return {"reps": results.reps(), "stms": results.stms(), "uploads": results.uploads(), "fdh": claims.summary()}

    @app.post("/api/repstm/rep")
    def rep_detail(body: dict = Body(...)):
        found = results.rep_detail(text(body.get("rep_no")))
        if not found:
            raise HTTPException(404, "ไม่พบ REP นี้")
        codes = [b["codes"] for b in found["bills"]] + [d["codes"] for d in found["disps"]] + [
            g["codes"] for d in found["disps"] for g in d["drugs"]]
        return {**found, "code_names": results.code_names(), "c_codes": c_codes(codes) if eclaim_codes.is_eclaim(found.get("account")) else {}}

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
        history = results.vn_history(vn)
        codes = [b["codes"] for b in history["reps"] if eclaim_codes.is_eclaim(b.get("account"))]
        codes += [d["codes"] for d in history["disps"] if eclaim_codes.is_eclaim(d.get("account"))]
        return {**history, "code_names": results.code_names(), "c_codes": c_codes(codes), "fdh": claims.for_key(vn)}

    @app.get("/api/repstm/c-codes")
    def c_code_lookup(q: str = "", limit: int = 100):
        """The e-Claim C code lookup (สปสช.): search by code, detail or fix."""
        rows = eclaim_codes.search(q, max(1, min(limit, 600)))
        return {"rows": rows, "total": len(eclaim_codes.table()), "source": eclaim_codes.SOURCE}

    return results


def c_codes(code_lists) -> dict[str, dict]:
    """The e-Claim C code detail and fix for the codes of e-Claim REP lines (one comma list per line)."""
    return eclaim_codes.describe(c for codes in code_lists for c in eclaim_codes.split(codes))
