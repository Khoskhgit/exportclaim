"""Daily automatic REP pull: e-Claim REPs (every fund), Seamless for DMIS (this fiscal year) and AIPN REP/STM (when
AIPN_USER is set), then a summary to LINE: through MOPH Alert (a LINE group) when its keys are set, else the LINE
Messaging API.

AUTO_PULL_TIME=06:00 turns it on (Asia/Bangkok). It reuses the page's background pulls, so a pull someone started by
hand is waited for, and the page shows the automatic pull's progress like any other.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException

from . import eclaim_web, line_push, moph_alert
from .config import Settings

log = logging.getLogger("ssop.autopull")
TZ = ZoneInfo("Asia/Bangkok")
WHO = "ดึงอัตโนมัติ"


def fiscal_year_be(day: date) -> int:
    """ปีงบประมาณ (พ.ศ.): 1 Oct 2026 starts 2570."""
    return day.year + 543 + (1 if day.month >= 10 else 0)


def parse_time(value: str) -> tuple[int, int] | None:
    found = re.fullmatch(r"(\d{1,2})[:.](\d{2})", value.strip())
    if not found or int(found[1]) > 23 or int(found[2]) > 59:
        return None
    return int(found[1]), int(found[2])


def next_run(now: datetime, at: tuple[int, int]) -> datetime:
    run = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    return run if run > now else run + timedelta(days=1)


def money(value) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value or "0")


def summary(eclaim: dict | None, seamless: dict | None, started: datetime, year_be: int, aipn: dict | None = None) -> str:
    """The LINE message: what each pull found, what was new, and what failed."""
    out = [f"สรุปดึง REP อัตโนมัติ {started:%d/%m/}{started.year + 543} {started:%H:%M}"]
    if eclaim is None:
        out.append("\n[e-Claim REP] ไม่ได้ดึง")
    else:
        ok = [l for l in eclaim["lines"] if l.get("kind") == "rep" and l.get("ok")]
        out.append(f"\n[e-Claim REP {', '.join(f.upper() for f in eclaim.get('funds') or [])} ย้อนหลัง {eclaim.get('months', '')} เดือน]")
        out.append(f"พบ {eclaim.get('found', 0)} REP · ใหม่ {eclaim.get('total', 0)} · นำเข้าแล้ว {eclaim.get('imported', 0)}"
                   f" · ผิดพลาด {eclaim.get('errors', 0)}")
        for l in ok[:20]:
            out.append(f"- {l['rep_no']} ({str(l.get('fund', '')).upper()}) ผ่าน {l.get('sent', 0) - l.get('failed', 0)}"
                       f" ไม่ผ่าน {l.get('failed', 0)}")
        if len(ok) > 20:
            out.append(f"… และอีก {len(ok) - 20} REP")
        if ok:
            out.append(f"รวมไม่ผ่าน {sum(l.get('failed', 0) for l in ok)} รายการ")
    if seamless is None:
        out.append(f"\n[Seamless for DMIS] ไม่ได้ดึง")
    else:
        ok = [l for l in seamless["lines"] if l.get("kind") == "seamless" and l.get("ok")]
        out.append(f"\n[Seamless for DMIS ปีงบ {year_be}]")
        out.append(f"พบ {seamless.get('found', 0)} REP · ใหม่ {seamless.get('total', 0)} · ดาวน์โหลดแล้ว {seamless.get('imported', 0)}"
                   f" · ผิดพลาด {seamless.get('errors', 0)}")
        for l in ok[:20]:
            out.append(f"- {l['rep_no']} {l.get('project', '')} ชดเชยสุทธิ {money(l.get('net'))} ปฏิเสธ {money(l.get('denied'))}")
        if len(ok) > 20:
            out.append(f"… และอีก {len(ok) - 20} REP")
        if ok:
            out.append(f"รวมชดเชยสุทธิ {money(sum(float(l.get('net') or 0) for l in ok))} บาท")
    if aipn is not None:
        ok = [l for l in aipn["lines"] if l.get("kind") == "aipn" and l.get("ok")]
        out.append(f"\n[AIPN ผู้ป่วยใน ปกส. ย้อนหลัง {aipn.get('months', '')} เดือน]")
        out.append(f"พบ {aipn.get('found', 0)} ไฟล์ · ใหม่ {aipn.get('total', 0)} · นำเข้าแล้ว {aipn.get('imported', 0)}"
                   f" · ผิดพลาด {aipn.get('errors', 0)}")
        for l in ok[:20]:
            out.append(f"- {'REP' if l.get('type') == 'rep' else 'STM'} {l['rep_no']}: {l.get('text', '')}"[:200])
    errors = [l for job in (eclaim, seamless, aipn) if job for l in job["lines"] if l.get("ok") is False]
    if errors:
        out.append("\nข้อผิดพลาด")
        out += [f"- {(l.get('rep_no') + ': ') if l.get('rep_no') else ''}{l.get('text', '')}"[:200] for l in errors[:8]]
        if len(errors) > 8:
            out.append(f"… และอีก {len(errors) - 8} รายการ")
    return "\n".join(out)


class AutoPull:
    def __init__(self, app: FastAPI, settings: Settings, send: Callable[[str], None] | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(TZ), poll: float = 5.0):
        self.app, self.settings, self.clock, self.poll = app, settings, clock, poll
        self.moph = bool(settings.moph_alert_client_key and settings.moph_alert_secret_key)
        self.send = send or (self._moph_send if self.moph else
                             lambda text: line_push.send(settings.line_channel_token, settings.line_to, text))
        self.at = parse_time(settings.auto_pull_time)
        self._lock = threading.Lock()
        self.running = False
        self.last: dict = {}
        self.steps: list[dict] = []  # this run's steps, shown live on the page
        self.next_at: datetime | None = None
        self._stop = threading.Event()

    def _moph_send(self, text: str) -> None:
        s = self.settings
        moph_alert.send(s.moph_alert_client_key, s.moph_alert_secret_key, text, s.moph_alert_url or moph_alert.URL)

    def _wait(self, job) -> dict:
        while job.view()["running"]:
            time.sleep(self.poll)
        return job.view()

    def _start(self, job, start: Callable[[], dict], key: str = "") -> dict | None:
        """Start a pull, waiting out one someone started by hand; None if it still can't start."""
        self._mark(key, "running")
        for _ in range(3):
            if start():
                done = self._wait(job)
                self._mark(key, "error" if done.get("errors") else "done", done)
                return done
            self._wait(job)
        self._mark(key, "error", {"text": "เริ่มไม่ได้ (มีการดึงอื่นค้างอยู่)"})
        return None

    def _mark(self, key: str, state: str, job: dict | None = None) -> None:
        for step in self.steps:
            if step["key"] == key:
                step["state"] = state
                if job:
                    step.update({k: job.get(k, step.get(k, 0)) for k in ("found", "total", "imported", "errors", "text")})

    def run_once(self) -> dict:
        with self._lock:
            if self.running:
                return {}
            self.running = True
        started = self.clock()
        year_be = fiscal_year_be(started.date())
        state = self.app.state
        eclaim = seamless = aipn = None
        use_aipn = bool(self.settings.aipn_user and self.settings.aipn_password)
        self.steps = [{"key": k, "label": label, "state": "wait" if on else "skip"} for k, label, on in (
            ("eclaim", f"REP e-Claim (UCS/OFC/LGO ย้อนหลัง {self.settings.auto_pull_months} เดือน)", True),
            ("seamless", f"Seamless for DMIS ปีงบ {year_be}", True),
            ("aipn", f"REP / STM AIPN (ย้อนหลัง {self.settings.auto_pull_months} เดือน)", use_aipn),
            ("line", "ส่งสรุปทาง LINE", True))]
        self.last = {"started_at": started.isoformat(timespec="seconds")}
        try:
            eclaim = self._start(state.eclaim_job, lambda: state.start_eclaim(WHO, list(eclaim_web.FUNDS),
                                                                              self.settings.auto_pull_months), "eclaim")
            seamless = self._start(state.seamless_job, lambda: state.start_seamless(WHO, year_be), "seamless")
            if use_aipn:
                aipn = self._start(state.aipn_job, lambda: state.start_aipn(WHO, self.settings.auto_pull_months), "aipn")
            text = summary(eclaim, seamless, started, year_be, aipn)
            self._mark("line", "running")
            try:
                self.send(text)
                sent = "ส่ง LINE แล้ว"
                self._mark("line", "done", {"text": sent})
            except line_push.LineError as exc:
                sent = str(exc)
                self._mark("line", "error", {"text": sent})
                log.warning("auto pull: %s", exc)
            self.last = {"started_at": started.isoformat(timespec="seconds"),
                         "finished_at": self.clock().isoformat(timespec="seconds"), "line": sent, "text": text}
            log.info("auto pull done: %s", sent)
            return self.last
        finally:
            with self._lock:
                self.running = False

    def loop(self) -> None:
        while not self._stop.is_set():
            self.next_at = next_run(self.clock(), self.at)
            while not self._stop.is_set() and self.clock() < self.next_at:
                self._stop.wait(min(60.0, max(1.0, (self.next_at - self.clock()).total_seconds())))
            if self._stop.is_set():
                return
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - keep the schedule alive for tomorrow
                log.exception("auto pull failed")

    def start(self) -> None:
        if self.at:
            threading.Thread(target=self.loop, daemon=True, name="auto-pull").start()
            log.info("auto pull every day at %02d:%02d", *self.at)
        elif self.settings.auto_pull_time:
            log.warning("AUTO_PULL_TIME=%r is not HH:MM; auto pull is off", self.settings.auto_pull_time)

    def stop(self) -> None:
        self._stop.set()

    def view(self) -> dict:
        return {"time": self.settings.auto_pull_time if self.at else "", "running": self.running,
                "next_at": self.next_at.isoformat(timespec="minutes") if self.next_at else "",
                "line_ready": self.moph or bool(self.settings.line_channel_token),
                "line_to": "กลุ่ม LINE ของ MOPH Alert" if self.moph else
                           "ผู้ใช้ที่ตั้งไว้" if self.settings.line_to else "ทุกคนที่เป็นเพื่อนกับบัญชี (broadcast)",
                "last": self.last, "steps": self._live_steps()}

    def _live_steps(self) -> list[dict]:
        """The steps, with the running pull's phase and counts read from its job."""
        jobs = {"eclaim": "eclaim_job", "seamless": "seamless_job", "aipn": "aipn_job"}
        out = []
        for step in self.steps:
            step = dict(step)
            job = getattr(self.app.state, jobs.get(step["key"], ""), None) if step["state"] == "running" else None
            if job is not None:
                live = job.view()
                step.update({k: live.get(k, 0) for k in ("phase", "done", "total", "found", "imported", "errors")})
            out.append(step)
        return out


def register(app: FastAPI, settings: Settings, send: Callable[[str], None] | None = None, **kw) -> AutoPull:
    auto = AutoPull(app, settings, send, **kw)
    app.router.on_startup.append(auto.start)
    app.router.on_shutdown.append(auto.stop)

    @app.get("/api/autopull")
    def status():
        return auto.view()

    @app.post("/api/autopull/run")
    def run_now():
        """Run the daily pull now (in the background) and send its summary to LINE."""
        if auto.running:
            raise HTTPException(409, "กำลังดึงอัตโนมัติอยู่")
        threading.Thread(target=auto.run_once, daemon=True, name="auto-pull-now").start()
        return {"started": True}

    @app.post("/api/autopull/line-test")
    def line_test():
        try:
            auto.send(f"ทดสอบส่ง LINE จาก {settings.hname or 'KHH Claim Sure'} {datetime.now(TZ):%d/%m %H:%M}")
        except line_push.LineError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"sent": True}

    return auto
