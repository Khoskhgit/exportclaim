"""Daily automatic REP pull: e-Claim REPs (every fund), Seamless for DMIS (this fiscal year), AIPN REP/STM (when
AIPN_USER is set) and the SSO REP/STM (dental, disabled, emergency 72 h) from MMS (when MMS_USER is set), then a summary to LINE: through MOPH Alert (a LINE group) when its keys are set, else the LINE
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


MMS_TITLE = "ประกันสังคม MMS (ทันตกรรม / ทุพพลภาพ / ฉุกเฉิน 72 ชม.)"


def _mms_lines(mms: dict) -> tuple[list[dict], int]:
    """The pulled MMS reports that have items, and how many came back empty (most months, for the smaller programs)."""
    ok = [l for l in mms["lines"] if l.get("kind") == "mms" and l.get("ok")]
    return [l for l in ok if l.get("rows", 1)], sum(1 for l in ok if not l.get("rows", 1))


def summary(eclaim: dict | None, seamless: dict | None, started: datetime, year_be: int, aipn: dict | None = None,
            mms: dict | None = None) -> str:
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
    if mms is not None:
        ok, empty = _mms_lines(mms)
        out.append(f"\n[{MMS_TITLE} ย้อนหลัง {mms.get('months', '')} เดือน]")
        out.append(f"ดึง {mms.get('imported', 0)}/{mms.get('total', 0)} รายงาน · ผิดพลาด {mms.get('errors', 0)}")
        for l in ok:
            out.append(f"- {l['rep_no']}: {l.get('text', '')}")
        if empty:
            out.append(f"- ไม่มีรายการ {empty} รายงาน")
    errors = [l for job in (eclaim, seamless, aipn, mms) if job for l in job["lines"] if l.get("ok") is False]
    if errors:
        out.append("\nข้อผิดพลาด")
        out += [f"- {(l.get('rep_no') + ': ') if l.get('rep_no') else ''}{l.get('text', '')}"[:200] for l in errors[:8]]
        if len(errors) > 8:
            out.append(f"… และอีก {len(errors) - 8} รายการ")
    return "\n".join(out)


GREEN, RED, GREY, INK = "#0E7C66", "#C62828", "#8A8A8A", "#222222"


def _text(text, **kw) -> dict:
    return {"type": "text", "text": str(text) if str(text) else "-", **kw}


def _stats(job: dict, done_label: str) -> dict:
    cells = (("พบ", job.get("found", 0), INK), ("ใหม่", job.get("total", 0), INK),
             (done_label, job.get("imported", 0), GREEN), ("ผิดพลาด", job.get("errors", 0), RED if job.get("errors") else INK))
    return {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
        {"type": "box", "layout": "vertical", "flex": 1, "contents": [
            _text(value, size="lg", weight="bold", align="center", color=color),
            _text(label, size="xxs", align="center", color=GREY)]} for label, value, color in cells]}


def _row(left: str, right: str, color: str = INK) -> dict:
    return {"type": "box", "layout": "baseline", "spacing": "sm", "contents": [
        _text(left, size="xs", color=INK, flex=5), _text(right, size="xs", color=color, flex=5, align="end")]}


def _section(title: str, job: dict | None, rows: list[dict], foot: str = "", done_label: str = "นำเข้า",
             show: int = 8) -> list[dict]:
    if job is None:
        return [_text(title, weight="bold", size="sm", color=INK), _text("ไม่ได้ดึง", size="xs", color=GREY)]
    icon = "⚠️" if job.get("errors") else "✅"
    out = [_text(f"{icon} {title}", weight="bold", size="sm", color=INK, wrap=True), _stats(job, done_label)]
    out += rows[:show]
    if len(rows) > show:
        out.append(_text(f"… และอีก {len(rows) - show} รายการ", size="xxs", color=GREY))
    if foot:
        out.append(_text(foot, size="xs", weight="bold", color=INK, align="end"))
    return out


def flex(eclaim: dict | None, seamless: dict | None, started: datetime, year_be: int, aipn: dict | None = None,
         mms: dict | None = None) -> dict:
    """The summary as a LINE flex bubble: a box per pull with its counts and the new REPs."""
    sections = []
    ok = [l for l in (eclaim or {}).get("lines", []) if l.get("kind") == "rep" and l.get("ok")]
    sections.append(_section(
        f"e-Claim REP {', '.join(f.upper() for f in (eclaim or {}).get('funds') or [])} ย้อนหลัง {(eclaim or {}).get('months', '')} เดือน",
        eclaim, [_row(f"{l['rep_no']} {str(l.get('fund', '')).upper()}",
                      f"ผ่าน {l.get('sent', 0) - l.get('failed', 0)} · ไม่ผ่าน {l.get('failed', 0)}",
                      RED if l.get("failed") else GREEN) for l in ok],
        f"รวมไม่ผ่าน {sum(l.get('failed', 0) for l in ok)} รายการ" if ok else ""))
    ok = [l for l in (seamless or {}).get("lines", []) if l.get("kind") == "seamless" and l.get("ok")]
    sections.append(_section(
        f"Seamless for DMIS ปีงบ {year_be}", seamless,
        [_row(f"{l['rep_no']} {l.get('project', '')}", f"{money(l.get('net'))} บาท") for l in ok],
        f"รวมชดเชยสุทธิ {money(sum(float(l.get('net') or 0) for l in ok))} บาท" if ok else "", "ดาวน์โหลด"))
    if aipn is not None:
        ok = [l for l in aipn["lines"] if l.get("kind") == "aipn" and l.get("ok")]
        sections.append(_section(
            f"AIPN ผู้ป่วยใน ปกส. ย้อนหลัง {aipn.get('months', '')} เดือน", aipn,
            [_row(f"{'REP' if l.get('type') == 'rep' else 'STM'} {l['rep_no']}", str(l.get("text", ""))[:60]) for l in ok]))
    if mms is not None:
        ok, empty = _mms_lines(mms)
        sections.append(_section(
            f"{MMS_TITLE} ย้อนหลัง {mms.get('months', '')} เดือน", {**mms, "found": mms.get("total", 0)},
            [_row(l["rep_no"], l.get("text", "")) for l in ok], f"ไม่มีรายการ {empty} รายงาน" if empty else "",
            "ดึงแล้ว"))
    errors = [l for job in (eclaim, seamless, aipn, mms) if job for l in job["lines"] if l.get("ok") is False]
    if errors:
        rows = [_text(f"• {(l.get('rep_no') + ': ') if l.get('rep_no') else ''}{l.get('text', '')}"[:160], size="xxs",
                      color=RED, wrap=True) for l in errors[:5]]
        if len(errors) > 5:
            rows.append(_text(f"… และอีก {len(errors) - 5} รายการ", size="xxs", color=GREY))
        sections.append([_text("ข้อผิดพลาด", weight="bold", size="sm", color=RED)] + rows)
    body = []
    for i, section in enumerate(sections):
        if i:
            body.append({"type": "separator", "margin": "lg"})
        body.append({"type": "box", "layout": "vertical", "spacing": "xs", "margin": "lg", "contents": section})
    trouble = bool(errors) or eclaim is None or seamless is None
    return {"type": "bubble", "size": "mega",
            "header": {"type": "box", "layout": "vertical", "backgroundColor": "#E65100" if trouble else GREEN,
                       "paddingAll": "lg", "contents": [
                _text("สรุปดึง REP อัตโนมัติ", weight="bold", size="lg", color="#FFFFFF"),
                _text(f"{started:%d/%m/}{started.year + 543} {started:%H:%M} น.", size="xs", color="#E0F2EE")]},
            "body": {"type": "box", "layout": "vertical", "contents": body}}


class AutoPull:
    def __init__(self, app: FastAPI, settings: Settings, send: Callable[[str], None] | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(TZ), poll: float = 5.0):
        self.app, self.settings, self.clock, self.poll = app, settings, clock, poll
        self.moph = bool(settings.moph_alert_client_key and settings.moph_alert_secret_key)
        self.flex = send is None  # the real senders take a flex card; a test's sender gets the text only
        self.send = send or (self._moph_send if self.moph else
                             lambda text, flex=None: line_push.send(settings.line_channel_token, settings.line_to,
                                                                    text, flex=flex))
        self.at = parse_time(settings.auto_pull_time)
        self._lock = threading.Lock()
        self.running = False
        self.last: dict = {}
        self.steps: list[dict] = []  # this run's steps, shown live on the page
        self.next_at: datetime | None = None
        self._stop = threading.Event()

    def _moph_send(self, text: str, flex: dict | None = None) -> None:
        s = self.settings
        moph_alert.send(s.moph_alert_client_key, s.moph_alert_secret_key, text, s.moph_alert_url or moph_alert.URL,
                        flex=flex)

    def deliver(self, text: str, card: dict | None) -> None:
        """Send the flex card; if LINE / MOPH Alert refuses it, send the plain text instead."""
        if not (self.flex and card):
            self.send(text)
            return
        try:
            self.send(text, card)
        except line_push.LineError as exc:
            log.warning("flex summary refused (%s); sending text", exc)
            self.send(text)

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
        eclaim = seamless = aipn = mms = None
        use_aipn = bool(self.settings.aipn_user and self.settings.aipn_password)
        use_mms = bool(self.settings.mms_user and self.settings.mms_password and hasattr(state, "start_mms"))
        self.steps = [{"key": k, "label": label, "state": "wait" if on else "skip"} for k, label, on in (
            ("eclaim", f"REP e-Claim (UCS/OFC/LGO ย้อนหลัง {self.settings.auto_pull_months} เดือน)", True),
            ("seamless", f"Seamless for DMIS ปีงบ {year_be}", True),
            ("aipn", f"REP / STM AIPN (ย้อนหลัง {self.settings.auto_pull_months} เดือน)", use_aipn),
            ("mms", f"REP / STM ประกันสังคม MMS: ทันตกรรม ทุพพลภาพ ฉุกเฉิน 72 ชม. (ย้อนหลัง {self.settings.auto_pull_months} เดือน)", use_mms),
            ("line", "ส่งสรุปทาง LINE", True))]
        self.last = {"started_at": started.isoformat(timespec="seconds")}
        try:
            eclaim = self._start(state.eclaim_job, lambda: state.start_eclaim(WHO, list(eclaim_web.FUNDS),
                                                                              self.settings.auto_pull_months), "eclaim")
            seamless = self._start(state.seamless_job, lambda: state.start_seamless(WHO, year_be), "seamless")
            if use_aipn:
                aipn = self._start(state.aipn_job, lambda: state.start_aipn(WHO, self.settings.auto_pull_months), "aipn")
            if use_mms:
                mms = self._start(state.mms_job, lambda: state.start_mms(WHO, self.settings.auto_pull_months), "mms")
            text = summary(eclaim, seamless, started, year_be, aipn, mms)
            self._mark("line", "running")
            try:
                self.deliver(text, flex(eclaim, seamless, started, year_be, aipn, mms))
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
        jobs = {"eclaim": "eclaim_job", "seamless": "seamless_job", "aipn": "aipn_job", "mms": "mms_job"}
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
