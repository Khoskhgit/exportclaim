"""Seamless for DMIS on the REP/STM page: pull every project's REP Excel for a fiscal year, list them, serve the files."""
from __future__ import annotations

import logging
import time

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from . import auth
from .config import Settings
from .eclaim_web import EclaimWeb, EclaimWebError
from .pull_job import PullJob
from .seamless_web import SeamlessError, SeamlessStore, SeamlessWeb
from .store import Store

log = logging.getLogger("ssop.seamless")
YEARS = (2570, 2569, 2568, 2567, 2566)  # what the Seamless year drop-down offers (พ.ศ.)


def register(app: FastAPI, settings: Settings, local: Store, client: SeamlessWeb | None = None,
             pause=lambda: time.sleep(1)) -> SeamlessStore:
    store = SeamlessStore(local)
    job = PullJob("seamless-pull")
    shared: dict = {"client": client}

    def seamless() -> SeamlessWeb:
        """Its own NHSO session (separate from the e-Claim pull, so both can run)."""
        if shared["client"] is None:
            shared["client"] = SeamlessWeb(EclaimWeb(settings.eclaim_user, settings.eclaim_password,
                                                     settings.eclaim_totp_secret, settings.hcode))
        return shared["client"]

    def run(job: PullJob, year_be: int, who: str) -> None:
        web = seamless()
        year = year_be - 543
        imported = errors = 0
        try:
            job.update(phase="กำลังเข้า Seamless for DMIS")
            projects = web.enter()
            offered = getattr(web, "years", None)  # the site's own year drop-down (ค.ศ.)
            if offered and year not in offered:
                listed = ", ".join(str(y + 543) for y in sorted(offered, reverse=True))
                if year < max(offered):
                    raise SeamlessError(f"เว็บ Seamless ไม่มีปีงบ {year_be} ให้เลือก (มี {listed})")
                # A new fiscal year (from 1 October) the site has not opened yet: its REPs are still the year before's.
                year = max(offered)
                job.update({"kind": "list", "text": f"เว็บ Seamless ยังไม่เปิดปีงบ {year_be} ดึงปีงบ {year + 543} แทน"})
                year_be = year + 543
            todo_by_project = {}
            found = known = 0
            for p in projects:  # 1) list every project's REPs
                job.update(phase=f"ค้นหา REP {p.name}")
                try:
                    rows = web.reps(p, year)
                except SeamlessError as exc:
                    errors += 1
                    job.update({"kind": "error", "ok": False, "text": f"{p.name}: {exc}"}, errors=errors)
                    continue
                have = store.known(r.rep_no for r in rows)
                store.refresh(r for r in rows if r.rep_no in have)
                new = [r for r in rows if r.rep_no not in have]
                found, known = found + len(rows), known + len(have)
                if rows:
                    job.update({"kind": "list", "text": f"{p.name}: {len(rows)} REP (ใหม่ {len(new)})"})
                if new:
                    todo_by_project[p.id] = (p, {r.rep_no for r in new})
            total = sum(len(nos) for _, nos in todo_by_project.values())
            job.update({"kind": "list", "text": f"พบ {found} REP ดาวน์โหลดไว้แล้ว {known} ต้องดึงใหม่ {total}"},
                       found=found, known=known, total=total)
            done = 0
            for p, nos in todo_by_project.values():  # 2) search the project again and download its new REPs
                job.update(phase=f"เปิดตาราง REP {p.name}")
                try:
                    rows = [r for r in web.reps(p, year) if r.rep_no in nos]
                except SeamlessError as exc:
                    errors += 1
                    done += len(nos)
                    job.update({"kind": "error", "ok": False, "text": f"{p.name}: {exc}"}, errors=errors, done=done)
                    continue
                for row in rows:
                    done += 1
                    job.update(phase=f"ดาวน์โหลด REP {row.rep_no} ({done}/{total})")
                    try:
                        name, data = web.download(row)
                        store.save(row, name, data, year_be, who)
                        imported += 1
                        job.update({"kind": "seamless", "ok": True, "rep_no": row.rep_no, "project": p.name, "period": row.period,
                                    "rep_date": row.rep_date, "net": row.net, "paid": row.amounts.get("paid", 0),
                                    "denied": row.amounts.get("denied", 0), "name": name}, done=done, imported=imported)
                    except SeamlessError as exc:
                        errors += 1
                        job.update({"kind": "seamless", "ok": False, "rep_no": row.rep_no, "project": p.name, "text": str(exc)},
                                   done=done, errors=errors)
                    pause()
        except (SeamlessError, EclaimWebError) as exc:
            errors += 1
            job.update({"kind": "error", "ok": False, "text": str(exc)}, errors=errors)

    def start_seamless(who: str, year: int) -> dict:
        """Start the Seamless pull for a fiscal year (พ.ศ.); {} if one is already running."""
        return job.start(who, lambda j: run(j, year, f"{who} · Seamless"), year=year)

    app.state.seamless_job, app.state.start_seamless = job, start_seamless

    @app.get("/api/seamless")
    def status():
        return {"ready": bool(settings.eclaim_user and settings.eclaim_password and settings.eclaim_totp_secret),
                "years": YEARS, "job": job.view(), "reps": store.listing()}

    @app.post("/api/seamless")
    def pull(request: Request, body: dict = Body(default={})):
        try:
            year = int(body.get("year") or YEARS[0])
        except (TypeError, ValueError):
            raise HTTPException(400, "ปีงบไม่ถูกต้อง") from None
        if year not in YEARS:
            raise HTTPException(400, "ปีงบไม่ถูกต้อง")
        who = auth.user_label(request) or "ระบบ"
        started = start_seamless(who, year)
        if not started:
            raise HTTPException(409, f"กำลังดึง Seamless อยู่ (เริ่มโดย {job.busy_by()}) รอให้เสร็จก่อน")
        return {"job": started}

    @app.get("/api/seamless/file/{rep_no}")
    def file(rep_no: str):
        path = store.file(rep_no)
        if path is None:
            raise HTTPException(404, "ไม่พบไฟล์ของ REP นี้")
        return FileResponse(path, filename=path.name,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    return store
