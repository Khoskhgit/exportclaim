"""Download e-Claim (สปสช.) REP Excel files from eclaim.nhso.go.th with the hospital's own account.

The e-Claim login goes through the NHSO Keycloak (iam.nhso.go.th). With TOTP (an authenticator app) set on the
account there is no captcha: this module posts the username and password, then the TOTP code it computes from the
secret in .env (ECLAIM_TOTP_SECRET), and keeps the session cookies. Plain HTTP, no browser; the User-Agent names this
program. The REP list is the page "ตรวจสอบข้อมูล" (ValidationMainAction.do, table #content2); each row's
"download excel" link gives the .xls that repstm.parse_upload reads.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.cookiejar
import logging
import re
import struct
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

log = logging.getLogger("ssop.eclaim_web")
BASE = "https://eclaim.nhso.go.th/webComponent/"
LOGIN_URL = BASE + "LoginSSOAction.do"
LIST_URL = BASE + "validation/ValidationMainAction.do"
USER_AGENT = "KHH-ClaimSure/1.0"
FUNDS = ("ucs", "ofc", "lgo")


class EclaimWebError(Exception):
    pass


# ----------------------------------------------------------------- TOTP (RFC 6238)
def totp(secret: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(re.sub(r"[\s-]", "", secret).upper() + "=" * (-len(re.sub(r"[\s-]", "", secret)) % 8))
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


# ----------------------------------------------------------------- HTML
@dataclass
class Form:
    attrs: dict
    fields: list[tuple[str, str]] = field(default_factory=list)

    def values(self) -> dict[str, str]:
        return dict(self.fields)


class _Page(HTMLParser):
    """Forms (with their inputs) and the cells of one table, by id."""

    def __init__(self, table_id: str = "content2"):
        super().__init__(convert_charrefs=True)
        self.forms: list[Form] = []
        self.table_id, self.in_table, self.depth = table_id, False, 0
        self.heads: list[str] = []
        self.rows: list[list[dict]] = []
        self._cell: dict | None = None
        self._head: list[str] | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "form":
            self.forms.append(Form(a))
        elif tag == "input" and self.forms and a.get("name"):
            kind = a.get("type", "text").lower()
            skip = kind in ("submit", "button", "image") or kind in ("checkbox", "radio") and "checked" not in a
            if not skip:
                self.forms[-1].fields.append((a["name"], a.get("value", "")))
        elif tag == "table" and a.get("id") == self.table_id:
            self.in_table, self.depth = True, 1
        elif tag == "table" and self.in_table:
            self.depth += 1
        elif self.in_table and tag == "tr":
            self.rows.append([])
        elif self.in_table and tag == "th":
            self._head = []
        elif self.in_table and tag == "td" and self.rows:
            self._cell = {"text": [], "href": ""}
        elif self.in_table and tag == "a" and self._cell is not None and not self._cell["href"]:
            self._cell["href"] = a.get("href", "")

    def handle_endtag(self, tag):
        if tag == "table" and self.in_table:
            self.depth -= 1
            self.in_table = self.depth > 0
        elif tag == "th" and self._head is not None:
            self.heads.append(" ".join("".join(self._head).split()))
            self._head = None
        elif tag == "td" and self._cell is not None:
            self.rows[-1].append({"text": " ".join("".join(self._cell["text"]).split()), "href": self._cell["href"]})
            self._cell = None

    def handle_data(self, data):
        if self._head is not None:
            self._head.append(data)
        if self._cell is not None:
            self._cell["text"].append(data)
        self.text.append(data)


def parse(html: str) -> _Page:
    page = _Page()
    page.feed(html)
    return page


@dataclass
class RepLink:
    rep_no: str
    fund: str
    name: str
    url: str


def rep_links(html: str, fund: str, page_url: str = LIST_URL) -> list[RepLink]:
    """The REP rows of one list page: number (ลำดับที่), file name and its "download excel" link."""
    page = parse(html)
    if not any(f.attrs.get("id") == "form-search" for f in page.forms):
        raise EclaimWebError("e-Claim ไม่แสดงหน้ารายการ REP (session หมดอายุ?)")
    if not page.heads:
        return []
    try:
        i_rep, i_excel = page.heads.index("ลำดับที่"), page.heads.index("Excel File")
    except ValueError:
        raise EclaimWebError(f"หน้า REP {fund.upper()} ไม่มีคอลัมน์ ลำดับที่ / Excel File (เว็บ e-Claim เปลี่ยนรูปแบบ?)") from None
    i_file = page.heads.index("File") if "File" in page.heads else -1
    links = []
    for cells in page.rows:
        if len(cells) <= max(i_rep, i_excel):
            continue
        rep_no, href = cells[i_rep]["text"], cells[i_excel]["href"]
        if not re.fullmatch(r"\d+", rep_no) or not href:
            continue
        doc = cells[i_file]["text"] if 0 <= i_file < len(cells) else ""
        base = re.sub(r"\.ecd$", "", doc, flags=re.I) or f"eclaim_{rep_no}"
        links.append(RepLink(rep_no, fund, re.sub(r"[^\w.-]", "_", base) + ".xls", urllib.parse.urljoin(page_url, href)))
    return links


def months_back(count: int, today: time.struct_time | None = None) -> list[tuple[int, int]]:
    """(month, Buddhist year) of this month and the count-1 before it."""
    now = today or time.localtime()
    out, year, month = [], now.tm_year, now.tm_mon
    for _ in range(max(1, count)):
        out.append((month, year + 543))
        month, year = (month - 1, year) if month > 1 else (12, year - 1)
    return out


# ----------------------------------------------------------------- client
class EclaimWeb:
    def __init__(self, user: str, password: str, totp_secret: str, hcode: str, timeout: int = 60,
                 opener: urllib.request.OpenerDirector | None = None, clock=time.time, sleep=time.sleep):
        self.user, self.password, self.secret, self.hcode = user, password, totp_secret, hcode
        self.timeout, self.clock, self.sleep = timeout, clock, sleep
        self.jar = http.cookiejar.CookieJar()
        self.opener = opener or urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.addheaders = [("User-Agent", USER_AGENT)]
        self.logged_in = False
        self.lock = threading.Lock()

    def _open(self, url: str, data: dict | None = None) -> tuple[str, bytes, str]:
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        try:
            with self.opener.open(urllib.request.Request(url, data=body), timeout=self.timeout) as response:  # noqa: S310
                return response.geturl(), response.read(), response.headers.get_content_charset() or "utf-8"
        except OSError as exc:
            raise EclaimWebError(f"ติดต่อ e-Claim ไม่ได้ ({exc})") from None

    def _html(self, url: str, data: dict | None = None) -> tuple[str, str]:
        final, raw, charset = self._open(url, data)
        return final, raw.decode(charset, "replace")

    @staticmethod
    def _feedback(html: str) -> str:
        found = re.search(r'(?:id="input-error[^"]*"|class="[^"]*(?:kc-feedback-text|alert-error|pf-c-alert__title)[^"]*")[^>]*>(.*?)<',
                          html, re.S)
        return " ".join(found.group(1).split()) if found else ""

    def login(self) -> None:
        if not (self.user and self.password and self.secret):
            raise EclaimWebError("ยังไม่ได้ตั้ง NHSO_USER / NHSO_PASSWORD / ECLAIM_TOTP_SECRET ใน .env")
        self.jar.clear()
        url, html = self._html(LOGIN_URL)
        form = next((f for f in parse(html).forms if f.attrs.get("id") == "kc-form-login"), None)
        if form is None:
            raise EclaimWebError("ไม่พบฟอร์ม login ของ สปสช. (หน้า login เปลี่ยน หรือถูกบล็อก)")
        fields = {**form.values(), "username": self.user, "password": self.password}
        url, html = self._html(urllib.parse.urljoin(url, form.attrs.get("action", "")), fields)
        for attempt in range(2):
            if url.startswith(BASE):
                break
            page = parse(html)
            otp_form = next((f for f in page.forms if "otp" in f.values() or "otp" in f.attrs.get("id", "")), None)
            if otp_form is None:
                raise EclaimWebError(f"login e-Claim ไม่ผ่าน: {self._feedback(html) or 'ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง'}")
            if attempt:  # a code is refused once it is used: wait for the next 30-second step
                self.sleep(30 - self.clock() % 30 + 1)
            fields = {**otp_form.values(), "otp": totp(self.secret, self.clock())}
            url, html = self._html(urllib.parse.urljoin(url, otp_form.attrs.get("action", "")), fields)
        if not url.startswith(BASE):
            raise EclaimWebError(f"รหัส TOTP ไม่ผ่าน: {self._feedback(html) or 'ตรวจ ECLAIM_TOTP_SECRET และเวลาของเครื่อง'}")
        self.logged_in = True
        log.info("e-Claim login ok as %s", self.user)

    def list_reps(self, fund: str, month: int, year: int) -> list[RepLink]:
        fields = {"mo": str(month), "ye": str(year), "repNo": "", "maininscl": fund, "zp": self.hcode,
                  "lev": "H", "bc": "null", "bc2": "null"}
        for attempt in range(2):
            if not self.logged_in:
                self.login()
            url, html = self._html(LIST_URL, fields)
            try:
                return rep_links(html, fund, url)
            except EclaimWebError:
                if attempt or "form-search" in html:
                    raise
                self.logged_in = False  # the session ran out: log in once more
        return []

    def download(self, link: RepLink) -> bytes:
        _, data, _ = self._open(link.url)
        if data[:64].lstrip().startswith(b"<"):
            self.logged_in = False
            raise EclaimWebError("ได้หน้าเว็บแทนไฟล์ Excel (session หมดอายุ?)")
        return data
