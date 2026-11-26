"""Download today's Approve Codes from KTB Healthcare Online and import them, every day at KTB_PULL_TIME (23:00 by
default, Asia/Bangkok) and from the button on the Approve Code page, then send a short summary to every channel
that is set: the LINE Official Account (LINE_CHANNEL_TOKEN, to LINE_GROUP_ID / LINE_TO or broadcast) and MOPH Alert
(MOPH_ALERT_CLIENT_KEY / SECRET_KEY). On only when KTB_COMPANY_ID / KTB_USER_ID / KTB_PASSWORD are set.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable

from fastapi import FastAPI, HTTPException, Request

from . import auth
from .approve import ApproveError
from .autopull import TZ, money, next_run, parse_time
from .config import Settings
from .ktb_web import KtbWeb, KtbWebError
from . import line_push, moph_alert
from .line_push import LineError

log = logging.getLogger("ssop.ktb")
WHO = "ดึงจาก KTB อัตโนมัติ"
TITLE = "Approve Code เบิกจ่ายตรง ขรก. (KTB)"


def summary(day: datetime, files: list[dict], error: str = "") -> str:
    head = f"{TITLE} {day:%d/%m/}{day.year + 543} {day:%H:%M} น."
    if error:
        return f"{head}\nดึงไม่สำเร็จ: {error}"
    rows = [f for upload in files for f in upload["files"]]
    if not rows:
        return f"{head}\nวันนี้ไม่มีรายการรูดบัตร"
    payments = sum(f["payments"] for f in rows)
    refunds = sum(f["refunds"] for f in rows)
    added = sum(f["added"] for f in rows)
    total = sum(float(f["total"] or 0) for f in rows)
    line = f"ชำระ {payments} รายการ" + (f" · คืนเงิน/ยกเลิก {refunds}" if refunds else "")
    return f"{head}\n{line}\nยอดรวม {money(total)} บาท · นำเข้าใหม่ {added} รายการ"


def senders(settings: Settings) -> list[tuple[str, Callable[[str], None]]]:
    """(name, send) of every summary channel set in .env: LINE OA and MOPH Alert both get it."""
    out: list[tuple[str, Callable[[str], None]]] = []
    if settings.line_channel_token:
        out.append(("LINE OA", lambda text: line_push.send(settings.line_channel_token, settings.line_to, text)))
    if settings.moph_alert_client_key and settings.moph_alert_secret_key:
        out.append(("MOPH Alert", lambda text: moph_alert.send(settings.moph_alert_client_key, settings.moph_alert_secret_key,
                                                               text, settings.moph_alert_url or moph_alert.URL)))
    return out


class KtbPull:
    def __init__(self, settings: Settings, import_data: Callable[[str, bytes, str], dict],
                 send: Callable[[str], None] | None = None, client: Callable[[], object] | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(TZ)):
        self.settings, self.import_data, self.clock = settings, import_data, clock
        # a test passes one sender; otherwise every channel set in .env
        self.sends = [("LINE", send)] if send else senders(settings)
        self.client = client or (lambda: KtbWeb(settings.ktb_company_id, settings.ktb_user_id, settings.ktb_password))
        self.ready = bool(settings.ktb_company_id and settings.ktb_user_id and settings.ktb_password)
        self.at = parse_time(settings.ktb_pull_time) if self.ready else None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.running = False
        self.last: dict = {}
        self.next_at: datetime | None = None

    def run_once(self, who: str = WHO) -> dict:
        with self._lock:
            if self.running:
                return {}
            self.running = True
        started = self.clock()
        files, error = [], ""
        try:
            try:
                for name, data in self.client().online_csvs():
                    files.append(self.import_data(name, data, who))
            except (KtbWebError, ApproveError) as exc:
                error = str(exc)
            except Exception as exc:  # noqa: BLE001 - a broken page must not stop tomorrow's run
                log.exception("KTB pull failed")
                error = f"{exc.__class__.__name__}: {str(exc)[:200]}"
            text = summary(started, files, error)
            results = []
            for name, send in self.sends:  # one channel failing does not stop the other
                try:
                    send(text)
                    results.append(f"ส่ง {name} แล้ว")
                except LineError as exc:
                    results.append(f"{name}: {exc}")
                    log.warning("KTB pull summary to %s: %s", name, exc)
            sent = " · ".join(results) or "ยังไม่ได้ตั้งค่า LINE OA / MOPH Alert"
            self.last = {"started_at": started.isoformat(timespec="seconds"),
                         "finished_at": self.clock().isoformat(timespec="seconds"), "by": who,
                         "ok": not error, "error": error, "files": files, "text": text, "line": sent}
            log.info("KTB pull done: %s", "ok" if not error else error)
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
            self.run_once()

    def start(self) -> None:
        if self.at:
            threading.Thread(target=self.loop, daemon=True, name="ktb-pull").start()
            log.info("KTB Approve Code pull every day at %02d:%02d", *self.at)
        elif self.ready and self.settings.ktb_pull_time:
            log.warning("KTB_PULL_TIME=%r is not HH:MM; the daily KTB pull is off", self.settings.ktb_pull_time)

    def stop(self) -> None:
        self._stop.set()

    def view(self) -> dict:
        return {"ready": self.ready, "time": self.settings.ktb_pull_time if self.at else "", "running": self.running,
                "next_at": self.next_at.isoformat(timespec="minutes") if self.next_at else "", "last": self.last}


def register(app: FastAPI, settings: Settings, import_data: Callable[[str, bytes, str], dict],
             send: Callable[[str], None] | None = None, **kw) -> KtbPull:
    pull = KtbPull(settings, import_data, send, **kw)
    app.router.on_startup.append(pull.start)
    app.router.on_shutdown.append(pull.stop)

    @app.get("/api/approve/ktb")
    def status():
        return pull.view()

    @app.post("/api/approve/ktb/run")
    def run_now(request: Request):
        if not pull.ready:
            raise HTTPException(400, "ยังไม่ได้ตั้งค่า KTB_COMPANY_ID / KTB_USER_ID / KTB_PASSWORD ใน .env")
        if pull.running:
            raise HTTPException(409, "กำลังดึงจาก KTB อยู่")
        threading.Thread(target=pull.run_once, args=(auth.user_label(request) or "ดึงจาก KTB (กดเอง)",), daemon=True, name="ktb-pull-now").start()
        return {"started": True}

    return pull
