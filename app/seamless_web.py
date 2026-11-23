"""Seamless for DMIS (สปสช. โรคเฉพาะ): download the REP Excel ("Unite report") of every project for a fiscal year.

The same NHSO login as e-Claim (iam.nhso.go.th, password + TOTP): EclaimWeb logs in, then "Sign in with NHSO OSS"
on seamlessfordmis.nhso.go.th passes through the Keycloak session. The site is JSF/PrimeFaces, so the steps are
the ones a person clicks: enter any project (authenUser.jsf), open the menu "REP - รายงาน REP แบบ SUMMARY",
pick a project and fiscal year with round "ทั้งหมด", search (AJAX), then per REP row open the dialog
"Download รายงาน" and press the Excel button of "Unite report". Generated element ids (j_idtNN) are found by
their labels each time, not hard-coded.

The files hold patient names and ids: they are kept as downloaded under data/seamless/ and only the REP header
(project, period, REP number and date, net amount) goes into SQLite.
"""
from __future__ import annotations

import html
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .eclaim_web import EclaimWeb, EclaimWebError
from .store import Store

log = logging.getLogger("ssop.seamless")
BASE = "https://seamlessfordmis.nhso.go.th/seamlessfordmis/faces/"
SUMMARY_URL = BASE + "report/summary.jsf"
F = "templateForm:summaryForm:"
HEADS = ["Download รายงาน", "เขต", "จังหวัด", "รหัสหน่วยบริการ", "ชื่อหน่วยบริการ", "งวด/เลขที่เบิกจ่าย", "REP NO.", "REP Date"]
# Cells 8-25 of a REP row are columns 8-25 of the report's Summary sheet. The table header is a generic 96-column
# template, so the cells are read by position: (field, cell) of the baht amounts kept.
AMOUNT_COLS = {
    "claimed_items": 8, "claimed": 9,            # ข้อมูลปกติ / เรียกเก็บ  (รายการ, บาท)
    "paid_items": 10, "paid": 11,                # ข้อมูลปกติ / ชดเชย
    "denied_items": 12, "denied": 13,            # ข้อมูลปกติ / ไม่ชดเชย
    "appeal_add": 23, "appeal_recall": 25,       # ข้อมูลอุทธรณ์ / ชดเชยสุทธิ / จ่ายเพิ่ม, เรียกคืน (บาท)
}
ROW_CELLS = 26


class SeamlessError(Exception):
    pass


@dataclass
class Project:
    id: str
    name: str


@dataclass
class RepRow:
    project_id: str
    project: str
    ri: str
    link: str
    period: str
    rep_no: str
    rep_date: str
    amounts: dict  # AMOUNT_COLS: the REP's totals as the table row shows them

    @property
    def net(self) -> float:
        """ชดเชยสุทธิ of the REP: compensated, plus what an appeal adds, less what it takes back."""
        a = self.amounts
        return round(a.get("paid", 0) + a.get("appeal_add", 0) - a.get("appeal_recall", 0), 2)


# ----------------------------------------------------------------- HTML helpers
def form(body: str, form_id: str) -> tuple[str, dict[str, str], str]:
    """(action, the values a browser would post, inner HTML) of one form."""
    head = re.search(rf'<form id="{re.escape(form_id)}"[^>]*>', body)
    if not head:
        raise SeamlessError(f"ไม่พบฟอร์ม {form_id} (หน้าเว็บ Seamless เปลี่ยน หรือ session หมดอายุ)")
    end = body.find("</form>", head.end())
    inner = body[head.end():end if end >= 0 else None]
    action = html.unescape(re.search(r'action="([^"]+)"', head.group(0)).group(1))
    fields: dict[str, str] = {}
    for tag in re.findall(r"<input[^>]*>", inner):
        name = re.search(r'name="([^"]+)"', tag)
        kind = (re.search(r'type="([^"]+)"', tag) or [None, "text"])[1].lower()
        if not name or kind in ("submit", "button", "image") or kind in ("checkbox", "radio") and "checked" not in tag:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        fields[name.group(1)] = html.unescape(value.group(1)) if value else ""
    for sel in re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', inner, re.S):
        chosen = re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', sel.group(2)) or re.search(r'<option[^>]*value="([^"]*)"', sel.group(2))
        fields[sel.group(1)] = html.unescape(chosen.group(1)) if chosen else ""
    return action, fields, inner


def text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def update(xml: str, element_id: str) -> str:
    """The new HTML of one element in a JSF partial response."""
    found = re.search(rf'<update id="{re.escape(element_id)}"><!\[CDATA\[(.*?)\]\]></update>', xml, re.S)
    if not found:
        if "<error>" in xml or "ViewExpired" in xml:
            raise SeamlessError("Seamless ตอบ error (session หมดอายุ?)")
        raise SeamlessError(f"Seamless ไม่ส่ง {element_id} กลับมา")
    return found.group(1)


def projects(page: str) -> tuple[str, list[Project]]:
    """The project drop-down of the summary page: (its component id, the projects)."""
    label = page.find("โครงการ :")
    found = re.search(r'<select id="(templateForm:summaryForm:[^"]+)_input"[^>]*>(.*?)</select>', page[label:] if label >= 0 else page, re.S)
    if not found:
        raise SeamlessError("ไม่พบช่องเลือกโครงการในหน้า REP SUMMARY")
    items = [Project(v, text(t)) for v, t in re.findall(r'<option value="(\d+)"[^>]*>(.*?)</option>', found.group(2), re.S)]
    return found.group(1), items


def years(page: str) -> set[int]:
    """The fiscal years (ค.ศ.) the year drop-down of the summary page offers; empty if it isn't found."""
    found = re.search(rf'<select id="{re.escape(F)}yearSelectId_input"[^>]*>(.*?)</select>', page, re.S)
    return {int(v) for v in re.findall(r'<option value="(\d{4})"', found.group(1))} if found else set()


def button_id(page: str, caption: str) -> str:
    for b in re.finditer(r'<button id="([^"]+)"[^>]*>(.*?)</button>', page, re.S):
        if text(b.group(2)) == caption:
            return b.group(1)
    raise SeamlessError(f"ไม่พบปุ่ม {caption}")


def rep_rows(table: str, project: Project, whole_table: bool = True) -> tuple[int, list[RepRow]]:
    """(total rows, the rows on this page) of the REP table dtb01; a paginator reply has the rows only."""
    total = re.search(r"\(\d+ - \d+ of (\d+)\)", table)
    heads = [text(h) for h in re.findall(r'<th[^>]*rowspan="6"[^>]*>(.*?)</th>', table, re.S)]
    if whole_table and heads[:len(HEADS)] != HEADS:
        raise SeamlessError("ตาราง REP ของ Seamless เปลี่ยนรูปแบบ (หัวตารางไม่ตรง)")
    rows = []
    for tr in re.finditer(r'<tr data-ri="(\d+)"[^>]*>(.*?)</tr>', table, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr.group(2), re.S)
        # the link's title is "รายงานในรูปแบบ Excel"; some replies come back with the Thai as "?"
        link = re.search(r'<a id="([^"]+:dtb01:\d+:[^"]+)"[^>]*title="[^"]*Excel"', cells[0]) if cells else None
        if not link or len(cells) < len(HEADS):
            continue
        if len(cells) != ROW_CELLS:
            raise SeamlessError(f"แถว REP ของ Seamless มี {len(cells)} ช่อง ไม่ใช่ {ROW_CELLS} (เว็บเปลี่ยนรูปแบบ)")
        amounts = {field: number(text(cells[i])) for field, i in AMOUNT_COLS.items()}
        rows.append(RepRow(project.id, project.name, tr.group(1), link.group(1), text(cells[5]), text(cells[6]),
                           text(cells[7]), amounts))
    return int(total.group(1)) if total else len(rows), rows


def excel_buttons(dialog: str) -> list[tuple[str, str, str]]:
    """(label, button name, file name) of each Excel button in the "Download รายงาน" dialog."""
    return [(text(label).rstrip(" :"), name, text(file)) for label, name, file in re.findall(
        r'<label[^>]*>([^<]+)</label></td><td[^>]*><input type="submit" name="([^"]+)"[^>]*class="btn-icon-excel"[^>]*/>'
        r'<label[^>]*>([^<]+)</label>', dialog)]


def number(value: str) -> float:
    try:
        return float(value.replace(",", "")) if value.strip() else 0.0
    except ValueError:
        return 0.0


def be_date(value: str) -> str:
    """dd/mm/yyyy (พ.ศ.) -> yyyy-mm-dd (ค.ศ.)."""
    found = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value.strip())
    return f"{int(found[3]) - 543:04d}-{int(found[2]):02d}-{int(found[1]):02d}" if found else ""


# ----------------------------------------------------------------- client
class SeamlessWeb:
    def __init__(self, eclaim: EclaimWeb, timeout: int = 180):
        self.web, self.timeout = eclaim, timeout
        self.page = ""
        self.fields: dict[str, str] = {}
        self.dialog_fields: dict[str, str] = {}
        self.project_select = ""
        self.search_button = ""
        self.shown = (0, 20)  # (first row, rows) the REP table shows now; a row's link works only on its page
        self.year = 0
        self.years: set[int] = set()

    def _post(self, url: str, data: dict, ajax: bool = False) -> tuple[str, bytes, dict]:
        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
        if ajax:
            headers.update({"Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest"})
        body = urllib.parse.urlencode(data).encode()
        for attempt in range(2):
            request = urllib.request.Request(url, data=body, headers=headers)
            try:
                with self.web.opener.open(request, timeout=self.timeout) as response:  # noqa: S310 - fixed https host
                    return response.geturl(), response.read(), dict(response.headers)
            except urllib.error.HTTPError as exc:
                raise SeamlessError(f"Seamless ตอบ HTTP {exc.code}") from None
            except OSError as exc:  # a dropped connection: try once more
                if attempt:
                    raise SeamlessError(f"ติดต่อ Seamless ไม่ได้ ({exc})") from None
                self.web.sleep(3)
        raise SeamlessError("ติดต่อ Seamless ไม่ได้")

    def _ajax(self, source: str, execute: str, render: str, fields: dict | None = None, extra: dict | None = None) -> str:
        data = {**(self.fields if fields is None else fields), "javax.faces.partial.ajax": "true", "javax.faces.source": source,
                "javax.faces.partial.execute": execute, "javax.faces.partial.render": render, source: source, **(extra or {})}
        _, raw, _ = self._post(SUMMARY_URL, data, ajax=True)
        return raw.decode("utf-8", "replace")

    def _get(self, url: str) -> tuple[str, str]:
        try:
            return self.web._html(url)
        except EclaimWebError as exc:
            raise SeamlessError(str(exc).replace("e-Claim", "Seamless")) from None

    def enter(self) -> list[Project]:
        """Log in (through e-Claim's NHSO session) and open the REP SUMMARY page; the projects it offers."""
        if not self.web.logged_in:
            self.web.login()
        url, body = self._get(BASE + "login.jsf")
        for attempt in range(2):
            oss = re.search(r'<form id="([^"]+)"[^>]*>(?:(?!</form>).)*Sign in with NHSO OSS', body, re.S)
            if not oss:
                raise SeamlessError("ไม่พบปุ่ม Sign in with NHSO OSS")
            action, fields, inner = form(body, oss.group(1))
            fields[re.search(r'<button id="[^"]+" name="([^"]+)"', inner).group(1)] = ""
            final, raw, _ = self._post(urllib.parse.urljoin(url, action), fields)
            url, body = final, raw.decode("utf-8", "replace")
            if "selectProjectImageId" in body:
                break
            if attempt or "kc-form-login" not in body:
                raise SeamlessError("เข้า Seamless ไม่ได้ (บัญชีนี้อาจไม่มีสิทธิ์ใช้ Seamless for DMIS)")
            self.web.login()  # the NHSO session ran out
            url, body = self._get(BASE + "login.jsf")
        # any project opens the system; the summary page then offers all of them
        pick = re.search(r"\{'([^']+)':'[^']+','projectId':'(\d+)','projectName':'([^']+)'\}", body)
        if not pick:
            raise SeamlessError("บัญชีนี้ไม่มีโครงการใน Seamless for DMIS")
        action, fields, _ = form(body, "selectProjectImageId")
        fields.update({pick.group(1): pick.group(1), "projectId": pick.group(2), "projectName": html.unescape(pick.group(3))})
        url, raw, _ = self._post(urllib.parse.urljoin(url, action), fields)
        body = raw.decode("utf-8", "replace")
        menu = re.search(r"addSubmitParam\('templateForm',(\{[^}]*'menuUrl':'/faces/report/summary\.jsf'[^}]*\})", body)
        if not menu:
            raise SeamlessError("ไม่พบเมนู REP - รายงาน REP แบบ SUMMARY")
        action, fields, _ = form(body, "templateForm")
        fields.update(dict(re.findall(r"'([^']+)':'([^']*)'", menu.group(1))))
        _, raw, _ = self._post(urllib.parse.urljoin(url, action), fields)
        self.page = raw.decode("utf-8", "replace")
        _, self.fields, _ = form(self.page, "templateForm")
        _, self.dialog_fields, _ = form(self.page, "dialogsForm")
        self.project_select, items = projects(self.page)
        self.search_button = button_id(self.page, "ค้นหา")
        self.years = years(self.page)
        log.info("seamless ready: %s projects", len(items))
        return items

    def reps(self, project: Project, year: int) -> list[RepRow]:
        """Every REP row of one project for a fiscal year (ค.ศ., the value of the year drop-down), round ทั้งหมด."""
        self.year = year
        self.fields.update({self.project_select + "_input": project.id, F + "yearSelectId_input": str(year),
                            F + "roundSelectId_input": ""})
        change = {"javax.faces.behavior.event": "change", "javax.faces.partial.event": "change"}
        self._ajax(self.project_select, self.project_select, f"{F}summarySearchPanel {F}dtb01", extra=change)
        self._ajax(F + "yearSelectId", F + "yearSelectId", F + "roundSelectId", extra=change)
        self.fields[F + "roundSelectId_input"] = ""
        table = update(self._ajax(self.search_button, "@all", F + "dtb01"), F + "dtb01")
        total, rows = rep_rows(table, project)
        self.shown = (0, 20)
        while len(rows) < total:  # the table shows 20 rows at a time
            more = rep_rows(self._page(len(rows)), project, whole_table=False)[1]
            if not more:
                break
            rows += more
        return rows

    def _page(self, first: int) -> str:
        """Show the REP table from row `first`, 100 rows at a time."""
        self.shown = (first, 100)
        return update(self._ajax(F + "dtb01", F + "dtb01", F + "dtb01", extra={
            F + "dtb01_pagination": "true", F + "dtb01_first": str(first), F + "dtb01_rows": "100",
            F + "dtb01_skipChildren": "true", F + "dtb01_encodeFeature": "true"}), F + "dtb01")

    def download(self, row: RepRow) -> tuple[str, bytes]:
        """The "Unite report" Excel of one REP row, checked to be that REP's own file."""
        unite = self._unite_button(row)
        if unite is None:  # the dialog still showed another REP: search the project again and press once more
            self.reps(Project(row.project_id, row.project), self.year)
            unite = self._unite_button(row)
        if unite is None:
            raise SeamlessError(f"REP {row.rep_no}: หน้าต่าง Download รายงานแสดงไฟล์ของ REP อื่น (ไม่ได้บันทึก)")
        _, data, headers = self._post(SUMMARY_URL, {**self.dialog_fields, unite[1]: ""})
        disposition = headers.get("Content-Disposition", "")
        name = re.search(r'filename="?([^";]+)', disposition)
        if not name or not data.startswith(b"PK"):
            raise SeamlessError(f"REP {row.rep_no}: ไม่ได้ไฟล์ Excel (session หมดอายุ?)")
        if row.rep_no not in name.group(1):
            raise SeamlessError(f"REP {row.rep_no}: ได้ไฟล์ {name.group(1)} ของ REP อื่น (ไม่ได้บันทึก)")
        return re.sub(r"[^\w.-]", "_", name.group(1)), data

    def _unite_button(self, row: RepRow) -> tuple[str, str, str] | None:
        """Press the row's Download link and read the dialog: its "Unite report" button, if the dialog is this REP's."""
        first, count = self.shown
        if not first <= int(row.ri) < first + count:  # bring the row's page back so its link can be pressed
            self._page(int(row.ri) // 100 * 100)
        self._ajax(row.link, "@all", "dialogsForm:summaryDialog")
        dialog = update(self._ajax("dialogsForm:summaryDialog", "dialogsForm:summaryDialog", "dialogsForm:summaryDialog",
                                   fields=self.dialog_fields, extra={"dialogsForm:summaryDialog_contentLoad": "true"}),
                        "dialogsForm:summaryDialog")
        buttons = [b for b in excel_buttons(dialog) if row.rep_no in b[2]]  # the files are named 10866_<REP NO.>
        return next((b for b in buttons if b[0].lower().startswith("unite")), buttons[0] if buttons else None)


# ----------------------------------------------------------------- store
SCHEMA = """
CREATE TABLE IF NOT EXISTS seamless_reps (
  rep_no TEXT PRIMARY KEY, project_id TEXT NOT NULL, project TEXT NOT NULL, period TEXT NOT NULL,
  rep_date TEXT NOT NULL, net TEXT NOT NULL, fiscal_year INTEGER NOT NULL, file TEXT NOT NULL,
  downloaded_at TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT ''
);
"""
AMOUNT_FIELDS = tuple(AMOUNT_COLS)


class SeamlessStore:
    def __init__(self, store: Store):
        self._db = store._db
        self.folder = Path(store.path).parent / "seamless"
        with self._db() as db:
            db.executescript(SCHEMA)
            have = {r[1] for r in db.execute("PRAGMA table_info(seamless_reps)")}
            for column in AMOUNT_FIELDS:  # added after the first release
                if column not in have:
                    db.execute(f"ALTER TABLE seamless_reps ADD COLUMN {column} REAL NOT NULL DEFAULT 0")
            # The first release could save another REP's file under a row; the files are named 10866_<REP NO.>,
            # so such a row is dropped and the next pull tries it again (the file itself belongs to the other REP).
            wrong = db.execute("DELETE FROM seamless_reps WHERE instr(file, rep_no) = 0").rowcount
            if wrong:
                log.warning("seamless: dropped %s rows whose file is another REP's", wrong)

    def refresh(self, rows: Iterable[RepRow]) -> None:
        """Update the amounts of REPs already downloaded (an appeal can change them); the file stays."""
        with self._db() as db:
            for row in rows:
                db.execute(f"UPDATE seamless_reps SET net=?, {', '.join(f'{c}=?' for c in AMOUNT_FIELDS)} WHERE rep_no=?",
                           (f"{row.net:.2f}", *(row.amounts.get(c, 0) for c in AMOUNT_FIELDS), row.rep_no))

    def forget(self, rep_nos: Iterable[str]) -> int:
        """Drop REP rows so the next pull downloads them again (their files are left alone)."""
        nos = list(rep_nos)
        with self._db() as db:
            return db.execute(f"DELETE FROM seamless_reps WHERE rep_no IN ({','.join('?' * len(nos))})", nos).rowcount if nos else 0

    def known(self, rep_nos: Iterable[str]) -> set[str]:
        wanted = sorted(set(rep_nos))
        if not wanted:
            return set()
        with self._db() as db:
            return {r[0] for r in db.execute(f"SELECT rep_no FROM seamless_reps WHERE rep_no IN ({','.join('?' * len(wanted))})", wanted)}

    def save(self, row: RepRow, name: str, data: bytes, fiscal_year: int, created_by: str) -> str:
        self.folder.mkdir(parents=True, exist_ok=True)
        saved = self.folder / name
        saved.write_bytes(data)
        with self._db() as db:
            db.execute(f"INSERT OR REPLACE INTO seamless_reps (rep_no, project_id, project, period, rep_date, net, fiscal_year, "
                       f"file, downloaded_at, created_by, {', '.join(AMOUNT_FIELDS)}) VALUES ({','.join('?' * (10 + len(AMOUNT_FIELDS)))})",
                       (row.rep_no, row.project_id, row.project, row.period, be_date(row.rep_date) or row.rep_date, f"{row.net:.2f}",
                        fiscal_year, name, datetime.now().isoformat(timespec="seconds"), created_by,
                        *(row.amounts.get(c, 0) for c in AMOUNT_FIELDS)))
        return name

    def listing(self, limit: int = 500) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                f"SELECT rep_no, project_id, project, period, rep_date, net, fiscal_year, file, downloaded_at, created_by, "
                f"{', '.join(AMOUNT_FIELDS)} FROM seamless_reps ORDER BY rep_date DESC, rep_no DESC LIMIT ?", (limit,))]

    def file(self, rep_no: str) -> Path | None:
        with self._db() as db:
            row = db.execute("SELECT file FROM seamless_reps WHERE rep_no=?", (rep_no,)).fetchone()
        path = self.folder / row[0] if row else None
        return path if path and path.is_file() and path.parent == self.folder else None
