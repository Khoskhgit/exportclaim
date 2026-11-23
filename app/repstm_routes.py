"""The REP/STM page: import the SSO reply ZIPs and look at what came back."""
from __future__ import annotations

import hashlib
import logging
import re
import time

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import auth, eclaim_codes, eclaim_web, fdh_claims
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


def register(app: FastAPI, settings: Settings, local: Store, templates: Jinja2Templates,
             eclaim: eclaim_web.EclaimWeb | None = None, eclaim_pause=lambda: time.sleep(1)) -> RepStmStore:
    results = RepStmStore(local)
    shared: dict = {"client": eclaim}

    def eclaim_client() -> eclaim_web.EclaimWeb:
        """One e-Claim session for the app, kept between pulls (login again only when it runs out)."""
        if shared["client"] is None:
            shared["client"] = eclaim_web.EclaimWeb(settings.eclaim_user, settings.eclaim_password,
                                                    settings.eclaim_totp_secret, settings.hcode)
        return shared["client"]

    claims = fdh_claims.FdhClaimStore(local)
    folder = local.path.parent / "rep_stm"

    @app.get("/repstm", response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse(request, "repstm.html", {"hname": settings.hname})

    def import_bytes(name: str, data: bytes, created_by: str) -> dict:
        """Import one REP/STM (or FDH ClaimDetail) file: the drop zone, the userscript and the e-Claim download."""
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
            results.log_upload(name, hashlib.sha256(data).hexdigest(), summary, created_by)
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
        results.save(parsed, name, digest, saved.name, summary, created_by=created_by)
        log.info("repstm import %s reps=%s stms=%s", name, len(parsed.reps), len(parsed.stms))
        return {
            "name": name, "summary": summary, "skipped": parsed.skipped,
            "again": f"เคยนำเข้าไฟล์นี้แล้วเมื่อ {before['at'].replace('T', ' ')} ข้อมูลถูกแทนที่ด้วยชุดเดิม" if before else "",
            "reps": [r.rep_no for r in parsed.reps.values()], "stms": [s.stm_no for s in parsed.stms.values()],
        }

    @app.post("/api/repstm/import")
    async def import_file(request: Request, name: str = ""):
        return import_bytes(name, await request.body(), auth.user_label(request))

    @app.get("/api/repstm/eclaim")
    def eclaim_ready():
        return {"ready": bool(settings.eclaim_user and settings.eclaim_password and settings.eclaim_totp_secret),
                "user": settings.eclaim_user}

    @app.post("/api/repstm/eclaim")
    def eclaim_pull(request: Request, body: dict = Body(default={})):
        """Log in to e-Claim (password + TOTP), list the REPs of the chosen funds and months and import those not
        imported yet. Runs one at a time; the files are fetched one by one with a short pause."""
        funds = [f for f in (body.get("funds") or list(eclaim_web.FUNDS)) if f in eclaim_web.FUNDS]
        try:
            months = max(1, min(int(body.get("months") or 2), 12))
        except (TypeError, ValueError):
            raise HTTPException(400, "จำนวนเดือนไม่ถูกต้อง") from None
        if not funds:
            raise HTTPException(400, "เลือกสิทธิอย่างน้อย 1 สิทธิ")
        client = eclaim_client()
        if not client.lock.acquire(blocking=False):
            raise HTTPException(409, "กำลังดึง REP อยู่ รอให้เสร็จก่อน")
        lines: list[dict] = []
        imported = failed = 0
        try:
            found: dict[str, eclaim_web.RepLink] = {}
            for fund in funds:
                for month, year in eclaim_web.months_back(months):
                    links = client.list_reps(fund, month, year)
                    for link in links:
                        found.setdefault(link.rep_no, link)
                    lines.append({"ok": True, "text": f"{fund.upper()} {month}/{year}: {len(links)} REP"})
            known = set(results.known_reps(found))
            todo = [link for no, link in found.items() if no not in known]
            lines.append({"ok": True, "text": f"พบ {len(found)} REP นำเข้าแล้ว {len(known)} ต้องดึงใหม่ {len(todo)}"})
            who = f"{auth.user_label(request) or 'ระบบ'} · e-Claim"
            for link in todo:
                try:
                    done = import_bytes(link.name, client.download(link), who)
                    imported += 1
                    lines.append({"ok": True, "text": f"✓ {link.rep_no} {link.name}: {done['summary']}"})
                except (eclaim_web.EclaimWebError, HTTPException) as exc:
                    failed += 1
                    lines.append({"ok": False, "text": f"✕ {link.rep_no} {link.name}: {getattr(exc, 'detail', None) or exc}"})
                eclaim_pause()
        except eclaim_web.EclaimWebError as exc:
            lines.append({"ok": False, "text": f"✕ {exc}"})
            failed += 1
        finally:
            client.lock.release()
        log.info("eclaim pull funds=%s months=%s imported=%s failed=%s", funds, months, imported, failed)
        return {"imported": imported, "failed": failed, "lines": lines}

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
