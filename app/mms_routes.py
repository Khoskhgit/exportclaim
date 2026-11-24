"""Pull the SSO REP / STM reports (dental, disabled, emergency 72 h) from MMS (mms_web) in the background, per month."""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from . import auth, mms_web
from .config import Settings
from .pull_job import PullJob
from .store import Store

log = logging.getLogger("ssop.mms")


def register(app: FastAPI, settings: Settings, local: Store, client: Callable | None = None,
             today: Callable[[], date] = date.today) -> mms_web.MmsStore:
    store = mms_web.MmsStore(local)
    folder = local.path.parent / "mms"
    job = PullJob("mms-pull")
    make = client or (lambda: mms_web.MmsWeb(settings.mms_user, settings.mms_password, settings.mms_hosp_code))

    def ready() -> bool:
        return bool(settings.mms_user and settings.mms_password)

    def run(job: PullJob, months: int, programs: list[str], who: str) -> None:
        ranges = mms_web.month_ranges(today(), months)
        todo = [(kind, start, end) for start, end in ranges for program in programs for kind in mms_web.KINDS[program]]
        names = " / ".join(mms_web.PROGRAMS[p] for p in programs)
        job.update({"kind": "list", "text": f"ดึงรายงาน {names} {len(ranges)} เดือน (REP + STM) {len(todo)} รายงาน"},
                   total=len(todo), found=len(todo))
        imported = failed = 0
        job.update(phase="กำลังเข้า MMS ประกันสังคม")
        try:
            with make() as web:
                web.login()
                for n, (kind, start, end) in enumerate(todo, 1):
                    month = start.strftime("%Y-%m")
                    label = f"{mms_web.LABELS[kind]} {start.month:02d}/{start.year + 543}"
                    job.update(phase=f"ดึง {label} ({n}/{len(todo)})")
                    try:
                        report = web.report(kind, start, end)
                        parsed = mms_web.parse_report(report.data)
                        if parsed["kind"] != kind:
                            raise mms_web.MmsWebError(f"ได้รายงานผิดชนิด ({parsed['kind']})")
                        folder.mkdir(parents=True, exist_ok=True)
                        saved = folder / f"{mms_web.REPORTS[kind]}_{start:%Y%m%d}_{end:%Y%m%d}.xlsx"
                        saved.write_bytes(report.data)
                        store.save(parsed, month, saved.name, who)
                        imported += 1
                        money = f"จ่าย {parsed['paid']:,.2f}" if mms_web.side(kind) == "stm" else f"ขอเบิก {parsed['claim']:,.2f}"
                        job.update({"kind": "mms", "ok": True, "type": kind, "rep_no": label, "month": month,
                                    "rows": len(parsed["rows"]), "claim": parsed["claim"], "paid": parsed["paid"],
                                    "text": f"{len(parsed['rows'])} รายการ {money} บาท"}, done=n, imported=imported)
                    except mms_web.MmsWebError as exc:
                        failed += 1
                        job.update({"kind": "mms", "ok": False, "type": kind, "rep_no": label, "text": str(exc)},
                                   done=n, errors=failed)
        except mms_web.MmsWebError as exc:
            failed += 1
            job.update({"kind": "error", "ok": False, "text": str(exc)}, errors=failed)

    def start(who: str, months: int, programs: list[str] | None = None) -> dict:
        chosen = [p for p in mms_web.PROGRAMS if p in (programs or mms_web.PROGRAMS)]
        return job.start(who, lambda j: run(j, months, chosen, f"{who} · MMS"), months=months, programs=chosen)

    app.state.mms_job, app.state.start_mms, app.state.mms_ready = job, start, ready

    @app.get("/api/repstm/mms")
    def status():
        return {"ready": ready(), "user": settings.mms_user, "job": job.view(), "months": store.months(),
                "programs": mms_web.PROGRAMS}

    @app.post("/api/repstm/mms")
    def pull(request: Request, body: dict = Body(default={})):
        if not ready():
            raise HTTPException(400, "ยังไม่ได้ตั้งค่า MMS_USER / MMS_PASSWORD ใน .env")
        try:
            months = max(1, min(int(body.get("months") or 2), 12))
        except (TypeError, ValueError):
            raise HTTPException(400, "จำนวนเดือนไม่ถูกต้อง") from None
        programs = body.get("programs") or list(mms_web.PROGRAMS)
        if not isinstance(programs, list) or not set(programs) <= set(mms_web.PROGRAMS):
            raise HTTPException(400, "เลือกรายงานไม่ถูกต้อง")
        started = start(auth.user_label(request) or "ระบบ", months, programs)
        if not started:
            raise HTTPException(409, f"กำลังดึงรายงาน MMS อยู่ (เริ่มโดย {job.busy_by()}) รอให้เสร็จก่อน")
        return {"job": started}

    @app.get("/api/repstm/mms/file")
    def file(kind: str, month: str):
        name = store.file(kind, month)
        path = folder / name
        if not name or not path.is_file():
            raise HTTPException(404, "ไม่มีไฟล์ของเดือนนี้")
        return FileResponse(path, filename=name)

    return store
