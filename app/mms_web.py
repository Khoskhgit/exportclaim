"""SSO MMS (mms.sso.go.th, สำนักงานประกันสังคม) dental reports: REP "รายงานการส่งเบิกค่าบริการทางการแพทย์ทันตกรรม"
and STM "รายงานการจ่ายค่าบริการทางการแพทย์ทันตกรรม" of the hospital, one month at a time, as XLSX.

The site is Blazor Server (pages are drawn over a WebSocket), so this drives a headless Chromium with Playwright:
log in with MMS_USER / MMS_PASSWORD (no captcha or OTP), then open the report viewer
(/mmsrpt/report?ReportName=...&HOSP_CODE=...&DATE_START=...&DATE_END=..., the page the "รายงาน" menu opens) and
export it as XLSX. The web lets a range cover at most one month and end yesterday at the latest; this module asks the
same. Playwright is imported only when a pull runs, so the app works without it.
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .store import Store

log = logging.getLogger("ssop.mms_web")
LOGIN_URL = "https://mms.sso.go.th/login/"
REPORT_URL = "https://mms.sso.go.th/mmsrpt/report"
REPORTS = {"rep": "REPORT_01_DENTAL_CLAIM_SUBMISSION", "stm": "REPORT_02_DENTAL_CLAIM_PAYMENT"}
LABELS = {"rep": "REP ส่งเบิก", "stm": "STM การจ่าย"}
THAI_MONTHS = ("มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน",
               "ตุลาคม", "พฤศจิกายน", "ธันวาคม")


class MmsWebError(Exception):
    pass


def hosp_code9(hcode: str) -> str:
    """MMS hospital code: 10866 -> 001086600."""
    return f"00{str(hcode).strip().zfill(5)}00" if hcode else ""


def month_ranges(today: date, months: int) -> list[tuple[date, date]]:
    """The last `months` calendar months, newest first, each ending at the month's end or yesterday."""
    out, first = [], today.replace(day=1)
    yesterday = today - timedelta(days=1)
    for _ in range(months):
        end = min((first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1), yesterday)
        if first <= end:
            out.append((first, end))
        first = (first - timedelta(days=1)).replace(day=1)
    return out


def thai_date(text) -> str:
    """'01 ตุลาคม 2569' -> '2026-10-01'; '' if it isn't one."""
    found = re.fullmatch(r"\s*(\d{1,2})\s+(\S+)\s+(\d{4})\s*", str(text or ""))
    if not found or found[2] not in THAI_MONTHS:
        return ""
    return date(int(found[3]) - 543, THAI_MONTHS.index(found[2]) + 1, int(found[1])).isoformat()


def money(value) -> float:
    try:
        return float(str(value).replace(",", "")) if value not in (None, "") else 0.0
    except ValueError:
        return 0.0


HEADERS = {"ลำดับ": "seq", "เลขประจำตัวประชาชน": "pid", "วันที่ใช้สิทธิ": "service_date", "วันที่ส่งเบิก": "submit_date",
           "ประเภทค่าบริการ": "service_type", "สถานะรับแจ้ง": "status", "จำนวนเงินที่ขอเบิก": "claim",
           "จำนวนเงินจ่าย": "paid", "เลขที่ใบสำคัญรับเงิน": "voucher", "วันที่โอนเข้าบัญชี": "transfer_date"}


def parse_report(data: bytes) -> dict:
    """Read an exported report: its kind, period and rows (the name column is left out)."""
    import openpyxl

    try:
        sheet = openpyxl.load_workbook(io.BytesIO(data), data_only=True).active
    except Exception as exc:  # noqa: BLE001 - any unreadable file
        raise MmsWebError(f"อ่านไฟล์รายงาน MMS ไม่ได้: {exc}") from None
    rows = [list(r) for r in sheet.iter_rows(values_only=True)]
    title = next((str(c) for r in rows for c in r if c and "รายงานการ" in str(c)), "")
    kind = "stm" if "รายงานการจ่าย" in title else "rep" if "รายงานการส่งเบิก" in title else ""
    if not kind:
        raise MmsWebError("ไม่ใช่รายงานทันตกรรมของ MMS (ไม่พบหัวรายงาน)")
    period = next((str(c) for r in rows for c in r if c and str(c).startswith("ตั้งแต่วันที่")), "")
    found = re.search(r"ตั้งแต่วันที่\s+(.+?)\s+ถึงวันที่\s+(.+)$", period)
    start, end = (thai_date(found[1]), thai_date(found[2])) if found else ("", "")
    head = next((i for i, r in enumerate(rows) if r and any(str(c or "").strip() == "ลำดับ" for c in r)), None)
    if head is None:
        raise MmsWebError("ไม่พบหัวตารางในรายงาน MMS")
    columns = {i: HEADERS.get(re.sub(r"\s+", "", str(c or ""))) for i, c in enumerate(rows[head])}
    items = []
    for r in rows[head + 1:]:
        values = {key: r[i] for i, key in columns.items() if key and i < len(r)}
        if not str(values.get("seq") or "").strip().isdigit() or not str(values.get("pid") or "").strip():
            continue  # an empty report still has a lone "1" row
        items.append({"seq": int(str(values["seq"]).strip()), "pid": str(values.get("pid") or "").strip(),
                      "service_date": thai_date(values.get("service_date")),
                      "submit_date": thai_date(values.get("submit_date")),
                      "service_type": str(values.get("service_type") or "").strip(),
                      "status": str(values.get("status") or "").strip(), "claim": money(values.get("claim")),
                      "paid": money(values.get("paid")), "voucher": str(values.get("voucher") or "").strip(),
                      "transfer_date": thai_date(values.get("transfer_date"))})
    return {"kind": kind, "start": start, "end": end, "rows": items,
            "claim": round(sum(i["claim"] for i in items), 2), "paid": round(sum(i["paid"] for i in items), 2)}


# ----------------------------------------------------------------- store
SCHEMA = """
CREATE TABLE IF NOT EXISTS mms_dental_files (
  kind TEXT NOT NULL, month TEXT NOT NULL, date_start TEXT NOT NULL, date_end TEXT NOT NULL, rows INTEGER NOT NULL,
  claim REAL NOT NULL, paid REAL NOT NULL, saved_as TEXT NOT NULL, fetched_at TEXT NOT NULL, fetched_by TEXT NOT NULL,
  PRIMARY KEY (kind, month)
);
CREATE TABLE IF NOT EXISTS mms_dental_rows (
  kind TEXT NOT NULL, month TEXT NOT NULL, seq INTEGER NOT NULL, pid TEXT NOT NULL, service_date TEXT NOT NULL,
  submit_date TEXT NOT NULL, service_type TEXT NOT NULL, status TEXT NOT NULL, claim REAL NOT NULL, paid REAL NOT NULL,
  voucher TEXT NOT NULL, transfer_date TEXT NOT NULL, PRIMARY KEY (kind, month, seq)
);
CREATE INDEX IF NOT EXISTS mms_dental_rows_pid ON mms_dental_rows(pid, service_date);
"""
ROW_COLS = ("seq", "pid", "service_date", "submit_date", "service_type", "status", "claim", "paid", "voucher",
            "transfer_date")


class MmsStore:
    """The pulled months; pulling a month again replaces it (statuses and payments change after the first pull)."""

    def __init__(self, store: Store):
        self._db = store._db
        with self._db() as db:
            db.executescript(SCHEMA)

    def save(self, parsed: dict, month: str, saved_as: str, who: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM mms_dental_rows WHERE kind=? AND month=?", (parsed["kind"], month))
            db.executemany(f"INSERT INTO mms_dental_rows (kind, month, {', '.join(ROW_COLS)}) VALUES "
                           f"(?, ?, {', '.join('?' * len(ROW_COLS))})",
                           [(parsed["kind"], month, *(r[c] for c in ROW_COLS)) for r in parsed["rows"]])
            db.execute("INSERT OR REPLACE INTO mms_dental_files VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (parsed["kind"], month, parsed["start"], parsed["end"], len(parsed["rows"]), parsed["claim"],
                        parsed["paid"], saved_as, datetime.now().isoformat(timespec="seconds"), who))

    def months(self) -> list[dict]:
        """One row per month: REP and STM counts and money side by side."""
        with self._db() as db:
            found = db.execute("SELECT kind, month, date_start, date_end, rows, claim, paid, fetched_at "
                               "FROM mms_dental_files ORDER BY month DESC").fetchall()
        out: dict[str, dict] = {}
        for kind, month, start, end, rows, claim, paid, at in found:
            row = out.setdefault(month, {"month": month, "date_start": start, "date_end": end})
            row[kind] = {"rows": rows, "claim": claim, "paid": paid, "fetched_at": at}
            row["date_end"] = max(row["date_end"], end)
        return list(out.values())

    def file(self, kind: str, month: str) -> str:
        with self._db() as db:
            found = db.execute("SELECT saved_as FROM mms_dental_files WHERE kind=? AND month=?", (kind, month)).fetchone()
        return found[0] if found else ""


# ----------------------------------------------------------------- client
@dataclass
class Report:
    kind: str
    start: date
    end: date
    name: str
    data: bytes


class MmsWeb:
    """One browser session: `with MmsWeb(...) as web: web.report("rep", start, end)`."""

    def __init__(self, user: str, password: str, hcode9: str, timeout: int = 120, headless: bool = True):
        self.user, self.password, self.hcode9 = user, password, hcode9
        self.timeout, self.headless = timeout * 1000, headless
        self._pw = self._browser = self._ctx = None

    def __enter__(self) -> "MmsWeb":
        if not (self.user and self.password):
            raise MmsWebError("ยังไม่ได้ตั้งค่า MMS_USER / MMS_PASSWORD ใน .env")
        if not self.hcode9:
            raise MmsWebError("ยังไม่ได้ตั้งค่า HCODE (หรือ MMS_HOSP_CODE) ใน .env")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise MmsWebError("ยังไม่ได้ติดตั้ง Playwright: pip install playwright แล้ว playwright install chromium") from None
        self._pw = sync_playwright().start()
        try:
            try:
                self._browser = self._pw.chromium.launch(headless=self.headless)
            except Exception:  # noqa: BLE001 - no bundled Chromium: use the installed Chrome
                self._browser = self._pw.chromium.launch(headless=self.headless, channel="chrome")
        except Exception as exc:  # noqa: BLE001
            self.close()
            raise MmsWebError(f"เปิดเบราว์เซอร์ไม่ได้ (playwright install chromium): {exc}") from None
        self._ctx = self._browser.new_context(locale="th-TH", accept_downloads=True, viewport={"width": 1400, "height": 1000})
        self._ctx.set_default_timeout(self.timeout)
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        for thing in (self._ctx, self._browser):
            try:
                thing and thing.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw:
            self._pw.stop()
        self._pw = self._browser = self._ctx = None

    def login(self) -> None:
        try:
            self._login()
        except MmsWebError:
            raise
        except Exception as exc:  # noqa: BLE001 - Playwright timeouts
            first = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
            raise MmsWebError(f"เข้า MMS ไม่สำเร็จ ({first[:120]})") from None

    def _login(self) -> None:
        page = self._ctx.new_page()
        try:
            page.goto(LOGIN_URL, wait_until="load")
            page.locator("input[type=password]").first.wait_for()
            page.locator("input[type=text]").first.fill(self.user)
            page.locator("input[type=password]").first.fill(self.password)
            page.get_by_role("button", name="เข้าสู่ระบบ").click()
            try:
                page.wait_for_url(re.compile(r".*/portal/.*"), timeout=60000)
            except Exception:  # noqa: BLE001 - still on the login page: show the site's message
                body = re.sub(r"\s+", " ", page.inner_text("body"))
                hint = next((m for m in ("ไม่ถูกต้อง", "ไม่พบ", "ล็อก", "หมดอายุ") if m in body), "")
                raise MmsWebError("login MMS ไม่ผ่าน" + (f" ({hint})" if hint else " ตรวจ MMS_USER / MMS_PASSWORD")) from None
            log.info("mms login ok as %s", self.user)
        finally:
            page.close()

    def report(self, kind: str, start: date, end: date) -> Report:
        """Export one report (REP or STM) for start..end as XLSX."""
        for attempt in range(2):  # the viewer sometimes stalls: open it once more
            try:
                return self._report(kind, start, end)
            except Exception as exc:  # noqa: BLE001 - Playwright timeouts and closed pages
                if attempt:
                    if isinstance(exc, MmsWebError):
                        raise
                    first = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
                    raise MmsWebError(f"{LABELS[kind]} {start.month:02d}/{start.year + 543}: เปิดรายงานไม่สำเร็จ ({first[:120]})") from None
                log.warning("mms %s %s: %s; retrying", kind, start, exc)
        raise MmsWebError("unreachable")

    def _report(self, kind: str, start: date, end: date) -> Report:
        query = (f"?ReportName={REPORTS[kind]}&HOSP_CODE={self.hcode9}&PORTAL_PORT=H"
                 f"&DATE_START={start.isoformat()}&DATE_END={end.isoformat()}")
        page = self._ctx.new_page()
        try:
            page.goto(REPORT_URL + query, wait_until="load")  # the viewer keeps polling: never "networkidle"
            for _ in range(self.timeout // 2000):
                page.wait_for_timeout(2000)
                try:
                    body = page.inner_text("body", timeout=5000)
                except Exception:  # noqa: BLE001 - the viewer is still drawing
                    continue
                if "Document is ready" in body:  # the viewer's own status once the pages are built
                    break
            else:
                raise MmsWebError(f"{LABELS[kind]} {start.month:02d}/{start.year + 543}: รายงานสร้างไม่เสร็จในเวลา")
            menu = page.locator(".dxrd-toolbar-item-exportTo, [title*='ส่งออก'], [title*='Export']")
            if menu.count():
                menu.first.click()
                page.wait_for_timeout(800)
            with page.expect_download() as done:
                page.get_by_text("XLSX", exact=True).first.click()
            download = done.value
            path = download.path()
            data = open(path, "rb").read() if path else b""
            if not data.startswith(b"PK"):
                raise MmsWebError(f"{LABELS[kind]} {start.month:02d}/{start.year + 543}: ไม่ได้ไฟล์ XLSX")
            return Report(kind, start, end, download.suggested_filename or f"{REPORTS[kind]}.xlsx", data)
        finally:
            page.close()
