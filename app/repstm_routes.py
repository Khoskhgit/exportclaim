"""The REP/STM page: import the SSO reply ZIPs and look at what came back."""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import date, timedelta

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import aipn_web, auth, eclaim_codes, eclaim_web, fdh_claims
from .config import Settings
from .pull_job import PullJob
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
             eclaim: eclaim_web.EclaimWeb | None = None, eclaim_pause=lambda: time.sleep(1),
             aipn: aipn_web.AipnWeb | None = None) -> RepStmStore:
    results = RepStmStore(local)
    shared: dict = {"client": eclaim, "aipn": aipn}

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
            "sent": sum(r.sent for r in parsed.reps.values()), "failed": sum(r.failed for r in parsed.reps.values()),
        }

    @app.post("/api/repstm/import")
    async def import_file(request: Request, name: str = ""):
        return import_bytes(name, await request.body(), auth.user_label(request))

    eclaim_job = PullJob("eclaim-pull")

    def run_pull(job: PullJob, funds: list[str], months: int, who: str) -> None:
        """The e-Claim pull, in the job's thread: progress goes to the job, read by GET /api/repstm/eclaim."""
        imported = failed = 0
        try:
            client = eclaim_client()
            found: dict[str, eclaim_web.RepLink] = {}
            job.update(phase="กำลังเชื่อมต่อ e-Claim")
            for fund in funds:
                for month, year in eclaim_web.months_back(months):
                    job.update(phase=f"อ่านรายการ REP {fund.upper()} {month}/{year}")
                    links = client.list_reps(fund, month, year)
                    for link in links:
                        found.setdefault(link.rep_no, link)
                    job.update({"kind": "list", "text": f"{fund.upper()} {month}/{year}: {len(links)} REP"})
            known = set(results.known_reps(found))
            todo = [link for no, link in found.items() if no not in known]
            job.update({"kind": "list", "text": f"พบ {len(found)} REP นำเข้าแล้ว {len(known)} ต้องดึงใหม่ {len(todo)}"},
                       total=len(todo), found=len(found), known=len(known))
            for n, link in enumerate(todo, 1):
                job.update(phase=f"ดาวน์โหลดและนำเข้า REP {link.rep_no} ({n}/{len(todo)})")
                try:
                    done = import_bytes(link.name, client.download(link), who)
                    imported += 1
                    job.update({"kind": "rep", "ok": True, "rep_no": link.rep_no, "fund": link.fund, "name": link.name,
                                "sent": done["sent"], "failed": done["failed"], "text": done["summary"]},
                               done=n, imported=imported)
                except (eclaim_web.EclaimWebError, HTTPException) as exc:
                    failed += 1
                    job.update({"kind": "rep", "ok": False, "rep_no": link.rep_no, "fund": link.fund, "name": link.name,
                                "text": str(getattr(exc, "detail", None) or exc)}, done=n, errors=failed)
                eclaim_pause()
        except eclaim_web.EclaimWebError as exc:
            failed += 1
            job.update({"kind": "error", "ok": False, "text": str(exc)}, errors=failed)

    def ready() -> bool:
        return bool(settings.eclaim_user and settings.eclaim_password and settings.eclaim_totp_secret)

    @app.get("/api/repstm/eclaim")
    def eclaim_status():
        return {"ready": ready(), "user": settings.eclaim_user, "job": eclaim_job.view()}

    def start_eclaim(who: str, funds: list[str], months: int) -> dict:
        """Start the e-Claim pull; {} if one is already running."""
        return eclaim_job.start(who, lambda job: run_pull(job, funds, months, f"{who} · e-Claim"), funds=funds, months=months)

    app.state.eclaim_job, app.state.start_eclaim = eclaim_job, start_eclaim

    @app.post("/api/repstm/eclaim")
    def eclaim_pull(request: Request, body: dict = Body(default={})):
        """Start pulling e-Claim REPs in the background (one pull at a time); poll GET /api/repstm/eclaim."""
        funds = [f for f in (body.get("funds") or list(eclaim_web.FUNDS)) if f in eclaim_web.FUNDS]
        try:
            months = max(1, min(int(body.get("months") or 2), 12))
        except (TypeError, ValueError):
            raise HTTPException(400, "จำนวนเดือนไม่ถูกต้อง") from None
        if not funds:
            raise HTTPException(400, "เลือกสิทธิอย่างน้อย 1 สิทธิ")
        who = auth.user_label(request) or "ระบบ"
        started = start_eclaim(who, funds, months)
        if not started:
            raise HTTPException(409, f"กำลังดึง REP อยู่ (เริ่มโดย {eclaim_job.busy_by()}) รอให้เสร็จก่อน")
        return {"job": started}

    # ---------------------------------------------------------------- AIPN (สกส.) REP RP1 + Statement AIPN
    aipn_job = PullJob("aipn-pull")

    def aipn_client() -> aipn_web.AipnWeb:
        if shared["aipn"] is None:
            shared["aipn"] = aipn_web.AipnWeb(settings.aipn_hcode, settings.aipn_user, settings.aipn_password)
        return shared["aipn"]

    def run_aipn(job: PullJob, months: int, who: str) -> None:
        """List REP and STM on the AIPN web, then download and import the ones issued in the last `months` months
        that aren't imported yet."""
        imported = failed = 0
        since = (date.today().replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1).isoformat()
        try:
            client = aipn_client()
            job.update(phase="กำลังเข้า AIPN")
            listed = []
            for kind, label in (("rep", "REP (RP1)"), ("stm", "Statement AIPN")):
                job.update(phase=f"อ่านรายการ {label}")
                items = [i for i in client.list_files(kind) if i.issued_at[:10] >= since]
                job.update({"kind": "list", "text": f"{label} ออกตั้งแต่ {since}: {len(items)} ไฟล์"})
                listed += items
            stm_no = {i.no: f"{settings.aipn_hcode}ST{i.no}" for i in listed if i.kind == "stm"}  # SIGNSTM {period} = STM {hcode}ST{period}
            have_rep = set(results.known_reps(i.no for i in listed if i.kind == "rep"))
            have_stm = set(results.known_stms(stm_no.values()))
            todo = [i for i in listed if not (i.no in have_rep if i.kind == "rep" else stm_no[i.no] in have_stm)]
            job.update({"kind": "list", "text": f"พบ {len(listed)} ไฟล์ นำเข้าแล้ว {len(listed) - len(todo)} ต้องดึงใหม่ {len(todo)}"},
                       total=len(todo), found=len(listed), known=len(listed) - len(todo))
            for n, item in enumerate(todo, 1):
                label = f"{'REP' if item.kind == 'rep' else 'STM'} {item.no}"
                job.update(phase=f"ดาวน์โหลดและนำเข้า {label} ({n}/{len(todo)})")
                try:
                    name, data = client.download(item)
                    done = import_bytes(name, data, who)
                    imported += 1
                    job.update({"kind": "aipn", "ok": True, "type": item.kind, "rep_no": item.no, "name": name,
                                "sent": done.get("sent", 0), "failed": done.get("failed", 0), "text": done["summary"]},
                               done=n, imported=imported)
                except (aipn_web.AipnWebError, HTTPException) as exc:
                    failed += 1
                    job.update({"kind": "aipn", "ok": False, "type": item.kind, "rep_no": item.no,
                                "text": str(getattr(exc, "detail", None) or exc)}, done=n, errors=failed)
                eclaim_pause()
        except aipn_web.AipnWebError as exc:
            failed += 1
            job.update({"kind": "error", "ok": False, "text": str(exc)}, errors=failed)

    def start_aipn(who: str, months: int) -> dict:
        return aipn_job.start(who, lambda job: run_aipn(job, months, f"{who} · AIPN"), months=months)

    app.state.aipn_job, app.state.start_aipn = aipn_job, start_aipn

    @app.get("/api/repstm/aipn")
    def aipn_status():
        return {"ready": bool(settings.aipn_user and settings.aipn_password), "user": settings.aipn_user, "job": aipn_job.view()}

    @app.post("/api/repstm/aipn")
    def aipn_pull(request: Request, body: dict = Body(default={})):
        try:
            months = max(1, min(int(body.get("months") or 2), 36))
        except (TypeError, ValueError):
            raise HTTPException(400, "จำนวนเดือนไม่ถูกต้อง") from None
        who = auth.user_label(request) or "ระบบ"
        started = start_aipn(who, months)
        if not started:
            raise HTTPException(409, f"กำลังดึง AIPN อยู่ (เริ่มโดย {aipn_job.busy_by()}) รอให้เสร็จก่อน")
        return {"job": started}

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
