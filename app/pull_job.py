"""One background pull at a time (e-Claim REP, Seamless for DMIS): the page polls `view()` for progress."""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable

log = logging.getLogger("ssop.pull")


class PullJob:
    def __init__(self, name: str):
        self.name = name
        self._lock = threading.Lock()
        self._job: dict = {"running": False, "lines": []}

    def view(self) -> dict:
        with self._lock:
            return {**self._job, "lines": list(self._job["lines"])}

    def update(self, line: dict | None = None, **fields) -> None:
        with self._lock:
            self._job.update(fields)
            if line:
                self._job["lines"].append(line)

    def start(self, who: str, work: Callable[["PullJob"], None], **info) -> dict:
        """Run `work(job)` in a thread; False if a pull is already running (the caller answers 409)."""
        with self._lock:
            if self._job["running"]:
                return {}
            self._job = {"running": True, "by": who, "started_at": datetime.now().isoformat(timespec="seconds"),
                         "phase": "เริ่ม", "total": 0, "done": 0, "imported": 0, "errors": 0, "found": 0, "known": 0,
                         "finished_at": "", "lines": [], **info}
        threading.Thread(target=self._run, args=(work,), daemon=True, name=self.name).start()
        return self.view()

    def busy_by(self) -> str:
        with self._lock:
            return self._job.get("by", "") if self._job["running"] else ""

    def _run(self, work) -> None:
        try:
            work(self)
        except Exception as exc:  # noqa: BLE001 - the thread must always finish the job
            log.exception("%s failed", self.name)
            with self._lock:
                self._job["errors"] = self._job.get("errors", 0) + 1
            self.update({"kind": "error", "ok": False, "text": str(exc) or exc.__class__.__name__})
        finally:
            self.update(running=False, phase="เสร็จ", finished_at=datetime.now().isoformat(timespec="seconds"))
            job = self.view()
            log.info("%s done imported=%s errors=%s", self.name, job.get("imported"), job.get("errors"))
