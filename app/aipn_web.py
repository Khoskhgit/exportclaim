"""AIPN web (cs8.chi.or.th/aipnupload, สกส.): list and download the REP (RP1, 10866_SIGNREP_{rid}.ZIP) and
Statement AIPN (10866_SIGNSTM_{period}.ZIP) files that the REP/STM page imports.

The site is ASP.NET Core: the login form posts hcode/username/password with its anti-forgery token (the page also
loads reCAPTCHA v3 but the site accepts the form without its token). A download (GET Operation/DownloadRep|Stm?id=)
only works after the list page was opened in the same session, so `list_*` must come before `download`.
"""
from __future__ import annotations

import html
import http.cookiejar
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("ssop.aipn_web")
BASE = "https://cs8.chi.or.th/aipnupload"
PAGES = {"rep": "/Partial/RepHcode", "stm": "/Partial/StmAIPN"}
DOWNLOAD = {"rep": "/Operation/DownloadRep", "stm": "/Operation/DownloadStm"}


class AipnWebError(Exception):
    pass


@dataclass(frozen=True)
class AipnFile:
    kind: str  # "rep" or "stm"
    no: str  # เลขที่ตอบรับ (rid) or Statement No. (period yymm)
    issued_at: str  # yyyy-mm-ddThh:mm:ss


def csrf(page: str) -> str:
    found = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', page) or \
        re.search(r'value="([^"]+)"[^>]*name="__RequestVerificationToken"', page)
    if not found:
        raise AipnWebError("ไม่พบ token ของฟอร์ม login AIPN (หน้าเว็บเปลี่ยน?)")
    return found.group(1)


def rows(page: str, kind: str) -> list[AipnFile]:
    """The rows of the REP / STM table: number and issue time (from data-order yyyymmddhhmmss)."""
    table = re.search(rf'<table id="table{"Rep" if kind == "rep" else "Stm"}".*?</table>', page, re.S)
    if not table:
        raise AipnWebError(f"ไม่พบตาราง {'REP' if kind == 'rep' else 'Statement'} ในหน้า AIPN (หน้าเว็บเปลี่ยน?)")
    found = []
    for no, order in re.findall(r'<td class="text-center">\s*(\d+)\s*</td>\s*<td[^>]*data-order="(\d{14})"', table.group(0)):
        o = order
        found.append(AipnFile(kind, no, f"{o[:4]}-{o[4:6]}-{o[6:8]}T{o[8:10]}:{o[10:12]}:{o[12:14]}"))
    return found


class AipnWeb:
    def __init__(self, hcode: str, user: str, password: str, timeout: int = 60):
        self.hcode, self.user, self.password, self.timeout = hcode, user, password, timeout
        self.logged_in = False
        self._new_session()

    def _new_session(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.addheaders = [("User-Agent", "Mozilla/5.0 (KHH Claim Sure)")]

    def _open(self, path: str, data: dict | None = None) -> tuple[str, bytes, dict]:
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        try:
            with self.opener.open(BASE + path, body, timeout=self.timeout) as reply:
                return reply.geturl(), reply.read(), dict(reply.headers)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and path.startswith("/Operation/Download"):
                raise AipnWebError("เว็บ AIPN ไม่มีไฟล์นี้ให้ดาวน์โหลดแล้ว (HTTP 404)") from None
            raise AipnWebError(f"AIPN ตอบ HTTP {exc.code} ({path})") from None
        except (urllib.error.URLError, OSError) as exc:
            raise AipnWebError(f"เชื่อมต่อ AIPN ไม่ได้: {exc}") from None

    def login(self) -> None:
        if not (self.hcode and self.user and self.password):
            raise AipnWebError("ยังไม่ได้ตั้งค่า AIPN_USER / AIPN_PASSWORD ใน .env")
        self._new_session()
        _, raw, _ = self._open("")
        url, raw, _ = self._open("", {"Token": "", "hcode": self.hcode, "username": self.user, "password": self.password,
                                      "__RequestVerificationToken": csrf(raw.decode("utf-8", "replace"))})
        page = raw.decode("utf-8", "replace")
        if 'name="password"' in page or "Operation/Logout" not in page:
            message = re.search(r'class="[^"]*(?:text-danger|validation-summary|modal_target)[^"]*"[^>]*>\s*([^<]{3,200})', page)
            raise AipnWebError("login AIPN ไม่ผ่าน" + (f": {html.unescape(message.group(1)).strip()}" if message else
                                                    " (ตรวจ AIPN_USER / AIPN_PASSWORD)"))
        self.logged_in = True
        log.info("aipn login ok (%s)", url)

    def _page(self, kind: str) -> str:
        for attempt in range(2):
            if not self.logged_in:
                self.login()
            url, raw, _ = self._open(PAGES[kind])
            page = raw.decode("utf-8", "replace")
            if 'name="password"' not in page:
                return page
            self.logged_in = False  # the session ran out: log in again once
        raise AipnWebError("session AIPN หมดอายุและ login ใหม่ไม่ได้")

    def list_files(self, kind: str) -> list[AipnFile]:
        return rows(self._page(kind), kind)

    def download(self, item: AipnFile) -> tuple[str, bytes]:
        """The ZIP of one REP/STM; opens its list page first because the site needs it in the session."""
        self._page(item.kind)
        _, data, headers = self._open(f"{DOWNLOAD[item.kind]}?{urllib.parse.urlencode({'id': item.no})}")
        name = re.search(r'filename="?([^";]+)', headers.get("Content-Disposition", ""))
        if not data.startswith(b"PK") or not name:
            raise AipnWebError(f"{item.kind.upper()} {item.no}: ไม่ได้ไฟล์ ZIP")
        return name.group(1).strip(), data
