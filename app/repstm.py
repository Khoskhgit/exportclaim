"""Read the SSO (สกส.) reply files: REP (ตอบรับ, checked A/C per claim) and STM (statement of what was paid).

REP comes as a ZIP of TIS-620 text files:
  {hcode}_SOCDBIL_{repno}.BIL   one "*|" line per claim: Stat Station, Line, Hcode, Hmain, AuthCode, DTTran, InvNo,
                                Pid, BP, Amount, Claimamt | CheckCodes
  {hcode}_SOCDDIS_{repno}.BIL   only the failed prescriptions: "*|" dispensing lines, each followed by "=|" drug lines
STM comes as a ZIP of UTF-8 XML (plus .htm/.xsl copies for viewing):
  {hcode}_{acc}STMP_{period}.xml  header + one <TBill> per paid claim; <rid> is the REP number that accepted it
  {hcode}_{acc}SUMP_{period}.xml  the cover sheet: the same totals as STMP, so it is not stored separately
CSMBS (ข้าราชการ, จาก สปสช. e-Claim) REP and STM come as .xls (BIFF, needs xlrd), stored with account "OFC":
  eclaim_{hcode}_{OPCS|IPCS}_{date}_{no}.xls  sheet "Detail": one row per visit with REP, SEQ NO (= VN; AN for
                                       IPCS) and Error Code
  The UC (บัตรทอง) REP eclaim_{hcode}_{OP|IP}_{date}_{no}.xls has the same sheet ("REP No.") and is stored as "UCS".
  STM_{hcode}_OP{yyyymm}_{nn}.xls  sheet "พึงรับ": a block per REP NO, one row per visit with SEQ NO (= VN) and
                                   พึงรับทั้งหมด (the amount to be paid); it is stored as STM account "OFC".
SSO inpatient (AIPN, account "SIGN") REP comes as a ZIP of TIS-620 text and STM as UTF-8 XML; the AN is the key:
  {hcode}_SIGNREP_{repno}.REP  one "*|" line per AN: pcode tcode(A/C) iptype CareAs SS, HMain, HCare, AN, DRG, rw,
                               adjrw, ST, SST, PT, Amt, name[:err[:dat],err…]; "--251: …" explains the codes
  {hcode}_SIGNSUP_{repno}.REP  the failed ANs ("*|AN, HN, name (PID), C (codes)") with the IPDx "+|", IPOp "#|" and
                               BillItems "=|" lines to fix; kept as the REP's failed details (rep_disps, dispid = AN)
  {hcode}_SIGNSTMS_{period}.xml  <STMLIST>: <stmdat> and one <Bill> per paid AN (drg, adjrw, Reimb, rid);
                               SIGNSUMS / SIGNSUPS repeat the same bills and are not stored
The patient's name and national ID in these files are not kept: VN, AN and HN link back to HOSxP.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import datetime

THAI_MONTHS = {name: i for i, name in enumerate(
    ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
     "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"], start=1)}
NAME = re.compile(r"(?P<hcode>\d{5})_(?P<acc>[A-Z]{4})(?P<part>BIL|DIS|STMP|SUMP|REP|SUP|STMS|SUMS|SUPS)_(?P<no>[\w-]+)\.(?P<ext>\w+)$", re.I)
MAX_FILES = 50


class RepStmError(ValueError):
    """The file is not a REP or STM this program can read."""


@dataclass
class RepBill:
    line: int
    stat: str
    station: str
    hcode: str
    hmain: str
    authcode: str
    dttran: str
    invno: str
    bp: str
    amount: str
    claimamt: str
    codes: str


@dataclass
class RepDrug:
    codes: str
    hospdrgid: str
    prdcat: str
    name: str
    qty: str
    unitprice: str
    chargeamt: str
    reimbamt: str
    drgid: str
    claimcont: str


@dataclass
class RepDisp:
    line: int
    codes: str
    invno: str
    dispid: str
    item_count: str
    charge: str
    claim: str
    paid: str
    other: str
    drugs: list[RepDrug] = field(default_factory=list)


@dataclass
class Rep:
    """One REP number: the BIL part, the DIS part, or both once merged."""
    rep_no: str
    hcode: str = ""
    account: str = ""
    session: str = ""
    station: str = ""
    issued_at: str = ""
    sent: int = 0
    passed: int = 0
    failed: int = 0
    bills: list[RepBill] = field(default_factory=list)
    disps: list[RepDisp] = field(default_factory=list)
    has_bil: bool = False
    has_dis: bool = False
    code_names: dict[str, str] = field(default_factory=dict)


@dataclass
class StmBill:
    seq: int
    station: str
    hmain: str
    hproc: str
    hcare: str
    hn: str
    invno: str
    bf: str
    pcode: str
    care: str
    payplan: str
    bp: str
    dttran: str
    copay: str
    cfh: str
    total: str
    extp: str
    rid: str
    cstat: str


@dataclass
class Stm:
    stm_no: str
    hcode: str = ""
    account: str = ""
    period: str = ""
    date_start: str = ""
    date_end: str = ""
    date_due: str = ""
    date_issue: str = ""
    count: int = 0
    amount: str = "0"
    bills: list[StmBill] = field(default_factory=list)


@dataclass
class Parsed:
    reps: dict[str, Rep] = field(default_factory=dict)
    stms: dict[str, Stm] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


# ----------------------------------------------------------------- dates
def be_datetime(value: str) -> str:
    """'15/10/2569 20:56' (Buddhist year) -> '2026-10-15T20:56'; '' when it cannot be read."""
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\D+(\d{1,2}):(\d{2}))?", value or "")
    if not match:
        return ""
    day, month, year, hour, minute = match.groups()
    year = int(year) - 543 if int(year) > 2400 else int(year)
    try:
        moment = datetime(year, int(month), int(day), int(hour or 0), int(minute or 0))
    except ValueError:
        return ""
    return moment.isoformat(timespec="minutes") if hour else moment.date().isoformat()


def thai_date(value: str) -> str:
    """'8  ตุลาคม  2569' -> '2026-10-08'."""
    parts = (value or "").split()
    if len(parts) != 3 or parts[1] not in THAI_MONTHS or not parts[0].isdigit() or not parts[2].isdigit():
        return ""
    try:
        return datetime(int(parts[2]) - 543, THAI_MONTHS[parts[1]], int(parts[0])).date().isoformat()
    except ValueError:
        return ""


def _codes(value: str) -> str:
    return ",".join(c for c in (p.strip().upper() for p in value.replace("---", "").split(",")) if c)


def _money(value: str) -> str:
    value = (value or "").strip().replace(",", "")
    try:
        return f"{float(value):.2f}" if value else "0.00"
    except ValueError:
        return "0.00"


def _header(lines: list[str], label: str) -> str:
    for line in lines:
        if line.strip().startswith(label) and "=" in line:
            return line.split("=", 1)[1].strip()
    return ""


def _decode(data: bytes) -> str:
    for encoding in ("tis-620", "cp874"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("cp874", errors="replace")


def _code_names(lines: list[str]) -> dict[str, str]:
    """The REP explains every code it used at the end, e.g. 'R61 : เป็นรายการลบหรือแก้ไข...'."""
    names = {}
    for line in lines:
        match = re.match(r"\s*([A-Z]\d{2})\s*:\s*(.+?)\s*$", line)
        if match:
            names[match.group(1)] = match.group(2)
    return names


# ------------------------------------------------------------------- REP
def parse_rep_bil(data: bytes, rep: Rep) -> None:
    lines = _decode(data).splitlines()
    rep.has_bil = True
    rep.hcode = rep.hcode or _header(lines, "รหัส ร.พ.")
    rep.session = _header(lines, "งวดส่งของ ร.พ.") or rep.session
    rep.issued_at = be_datetime(_header(lines, "วันที่ออกเลขตอบรับ").replace("เวลา:", "")) or rep.issued_at
    station = next((ln.split(":", 1)[1].strip() for ln in lines if ln.strip().startswith("สถานี:")), "")
    rep.station = station or rep.station
    for label, attr in (("รายการที่ส่งไป", "sent"), ("รายการที่ตรวจผ่าน", "passed"), ("รายการที่ตรวจไม่ผ่าน", "failed")):
        value = _header(lines, label)
        setattr(rep, attr, int(value) if value.isdigit() else 0)
    rep.code_names.update(_code_names(lines))
    for line in lines:
        if not line.startswith("*|"):
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        fields = [f.strip() for f in parts[1].split(",")]
        if len(fields) < 11:
            continue
        stat_station = fields[0].split()
        rep.bills.append(RepBill(
            line=int(fields[1]) if fields[1].isdigit() else len(rep.bills) + 1,
            stat=stat_station[0].upper() if stat_station else "",
            station=stat_station[1] if len(stat_station) > 1 else "",
            hcode=fields[2], hmain=fields[3], authcode=fields[4], dttran=be_datetime(fields[5]), invno=fields[6],
            bp=fields[8], amount=_money(fields[9]), claimamt=_money(fields[10]), codes=_codes(parts[2]),
        ))


def parse_rep_dis(data: bytes, rep: Rep) -> None:
    lines = _decode(data).splitlines()
    rep.has_dis = True
    rep.hcode = rep.hcode or _header(lines, "รหัส ร.พ.")
    rep.session = rep.session or _header(lines, "งวดส่งของ ร.พ.")
    rep.issued_at = rep.issued_at or be_datetime(_header(lines, "วันที่ออกเลขตอบรับ"))
    rep.code_names.update(_code_names(lines))
    current = None
    for line in lines:
        parts = [p.strip() for p in line.split("|")]
        if line.startswith("*|") and len(parts) >= 11:
            current = RepDisp(
                line=int(parts[1]) if parts[1].isdigit() else 0, codes=_codes(parts[2]), invno=parts[3],
                dispid=parts[4], item_count=parts[6], charge=_money(parts[7]), claim=_money(parts[8]),
                paid=_money(parts[9]), other=_money(parts[10]),
            )
            rep.disps.append(current)
        elif line.startswith("=|") and current is not None and len(parts) >= 11:
            current.drugs.append(RepDrug(
                codes=_codes(parts[1]), hospdrgid=parts[2], prdcat=parts[3], name=parts[4], qty=parts[5],
                unitprice=_money(parts[6]), chargeamt=_money(parts[7]), reimbamt=_money(parts[8]),
                drgid=parts[9], claimcont=parts[10],
            ))


# -------------------------------------------------------------- AIPN REP
def _aipn_codes(value: str) -> str:
    """'432:10866,644,666' (err[:dat] items) -> '432,644,666'."""
    return ",".join(c for c in (p.split(":", 1)[0].strip() for p in (value or "").split(",")) if c)


def parse_aipn_rep(data: bytes, rep: Rep) -> None:
    lines = _decode(data).splitlines()
    rep.has_bil = True
    rep.hcode = rep.hcode or _header(lines, "รหัส ร.พ.")
    rep.issued_at = be_datetime(_header(lines, "วันที่ออกเลขตอบรับ")) or rep.issued_at
    session = next((ln.split(":", 1)[1].strip() for ln in lines if "เลขงวดที่ส่ง" in ln and ":" in ln), "")
    rep.session = session or rep.session
    rep.station = "AIPN"
    for label, attr in (("รายการที่ส่งไป", "sent"), ("รายการที่ตรวจผ่าน", "passed"), ("รายการที่ตรวจไม่ผ่าน", "failed")):
        value = _header(lines, label).split()
        setattr(rep, attr, int(value[0]) if value and value[0].isdigit() else 0)
    for line in lines:
        match = re.match(r"\s*--(\d{3}):\s*(.+?)\s*$", line)
        if match:
            rep.code_names[match.group(1)] = match.group(2)
    for line in lines:
        if not line.startswith("*|"):
            continue
        parts = line[2:].split(",")
        if len(parts) < 12:
            continue
        flags = parts[0].split()
        hmain, hcare, an, drg, rw, adjrw, st, sst, pt, amt = (p.strip() for p in parts[1:11])
        rest = ",".join(parts[11:])  # the patient's name, then ":" and the codes; the name is not kept
        rep.bills.append(RepBill(
            line=len(rep.bills) + 1, stat=flags[1].upper() if len(flags) > 1 else "", station=st, hcode=hcare,
            hmain=hmain, authcode=drg, dttran="", invno=an, bp=flags[3] if len(flags) > 3 else "",
            amount=adjrw, claimamt=_money(amt), codes=_aipn_codes(rest.split(":", 1)[1]) if ":" in rest else "",
        ))


def parse_aipn_sup(data: bytes, rep: Rep) -> None:
    """The ANs that failed, each with its diagnosis, procedure and bill item lines to fix."""
    lines = _decode(data).splitlines()
    rep.has_dis = True
    current = None
    for line in lines:
        if line.startswith("*|"):
            fields = line[2:].split(",")
            status = re.search(r",\s*([AC])\s*\(([^)]*)\)\s*$", line)
            an = fields[0].strip()
            if not an.isdigit():  # the format description at the top also starts with "*|"
                current = None
                continue
            current = RepDisp(line=len(rep.disps) + 1, codes=_aipn_codes(status.group(2)) if status else "", invno=an,
                              dispid=an, item_count="0", charge="0.00", claim="0.00", paid="0.00", other="0.00")
            rep.disps.append(current)
            continue
        kind = line.strip()[:2]
        if current is None or kind not in ("=|", "+|", "#|"):
            continue
        parts = line.strip().split("|", 2)
        if len(parts) < 3:
            continue
        codes, fields = _codes(parts[1]), [f.strip() for f in parts[2].split(",")]
        if kind == "=|" and len(fields) >= 11:
            item = RepDrug(codes=codes, hospdrgid=fields[2], prdcat=fields[1], name=",".join(fields[5:-5]).strip(),
                           qty=fields[-5], unitprice=_money(fields[-4]), chargeamt=_money(fields[-3]),
                           reimbamt=_money(fields[-1]), drgid=fields[4], claimcont=fields[3])
            current.charge = f"{float(current.charge) + float(item.chargeamt):.2f}"
            current.claim = f"{float(current.claim) + float(item.reimbamt):.2f}"
        elif kind == "#|" and len(fields) >= 3:
            item = RepDrug(codes=codes, hospdrgid=fields[2], prdcat=f"IPOp {fields[1]}", name=",".join(fields[3:]).strip(),
                           qty="", unitprice="0.00", chargeamt="0.00", reimbamt="0.00", drgid="", claimcont="")
        elif kind == "+|" and len(fields) >= 4:
            item = RepDrug(codes=codes, hospdrgid=fields[3], prdcat=f"IPDx {fields[1]}", name=",".join(fields[4:]).strip(),
                           qty="", unitprice="0.00", chargeamt="0.00", reimbamt="0.00", drgid="", claimcont="")
        else:
            continue
        current.drugs.append(item)
        current.item_count = str(len(current.drugs))


# ------------------------------------------------------------------- STM
def _text(node, tag: str) -> str:
    found = node.find(tag)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_stm(data: bytes) -> Stm:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise RepStmError(f"อ่าน XML ไม่ได้: {exc}") from None
    if root.tag != "STMSTMP":
        raise RepStmError("ไม่ใช่ไฟล์ STMP")
    stm = Stm(
        stm_no=_text(root, "STMdoc"), hcode=_text(root, "hcode"), account=_text(root, "stmAccountID"),
        period=_text(root, "AccPeriod"), date_start=thai_date(_text(root, "dateStart")),
        date_end=thai_date(_text(root, "dateEnd")), date_due=thai_date(_text(root, "dateDue")),
        date_issue=thai_date(_text(root, "dateIssue")), amount=_money(_text(root, "amount")),
    )
    acount = _text(root, "acount")
    stm.count = int(acount) if acount.isdigit() else 0
    for seq, bill in enumerate(root.iter("TBill"), start=1):
        sys_id = bill.find("sys")
        stm.bills.append(StmBill(
            seq=int(sys_id.get("id")) if sys_id is not None and (sys_id.get("id") or "").isdigit() else seq,
            station=_text(bill, "station"), hmain=_text(bill, "hmain"), hproc=_text(bill, "hproc"),
            hcare=_text(bill, "hcare"), hn=_text(bill, "hn"), invno=_text(bill, "invno"), bf=_text(bill, "bf"),
            pcode=_text(bill, "pcode"), care=_text(bill, "care"), payplan=_text(bill, "payplan"), bp=_text(bill, "bp"),
            dttran=_text(bill, "dttran"), copay=_text(bill, "copay"), cfh=_text(bill, "cfh"),
            total=_money(_text(bill, "total")), extp=_text(bill, "ExtP"), rid=_text(bill, "rid"),
            cstat=_text(bill, "cstat"),
        ))
    if not stm.stm_no:
        raise RepStmError("ไม่พบเลขที่ STM ในไฟล์")
    return stm


def parse_stm_aipn(data: bytes) -> Stm:
    """SIGNSTMS: the paid ANs of one period; total is Reimb, payplan the DRG and cfh the AdjRW."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise RepStmError(f"อ่าน XML ไม่ได้: {exc}") from None
    head = root.find("stmdat")
    if root.tag != "STMLIST" or head is None:
        raise RepStmError("ไม่ใช่ไฟล์ STMS ของ AIPN")
    stm = Stm(stm_no=_text(head, "stmno"), hcode=_text(head, "hcode"), account=_text(root, "stmAccountID") or "SIGN",
              period=_text(head, "period"), date_due=thai_date(_text(head, "dateDue")))
    bills = root.find("Bills")
    for seq, bill in enumerate(bills.iter("Bill") if bills is not None else [], start=1):
        stm.bills.append(StmBill(
            seq=seq, station="AIPN", hmain=_text(bill, "hmain"), hproc=_text(bill, "hproc"), hcare=_text(bill, "hcode"),
            hn=_text(bill, "hn"), invno=_text(bill, "an"), bf=_text(bill, "bf"), pcode=_text(bill, "ptype"),
            care=_text(bill, "careas"), payplan=_text(bill, "drg"), bp=_text(bill, "PP"), dttran=_text(bill, "datedsc"),
            copay=_money(_text(bill, "Copay")), cfh=_text(bill, "adjrw"), total=_money(_text(bill, "Reimb")),
            extp=_money(_text(bill, "Nreimb")), rid=_text(bill, "rid"), cstat=_text(bill, "ft"),
        ))
    if not stm.stm_no:
        raise RepStmError("ไม่พบเลขที่ STM ในไฟล์")
    days = sorted(_text(b, "dateadm") for b in (bills.iter("Bill") if bills is not None else []) if _text(b, "dateadm"))
    ends = sorted(b.dttran for b in stm.bills if b.dttran)
    stm.date_start, stm.date_end = (days[0] if days else ""), (ends[-1] if ends else "")
    stm.count = len(stm.bills)
    stm.amount = f"{sum(float(b.total) for b in stm.bills):.2f}"
    return stm


def _cell(sheet, row: int, col: int) -> str:
    value = sheet.cell_value(row, col) if col < sheet.ncols else ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value or "").strip()


def _ofc_datetime(value: str) -> str:
    """'17/10 /2026 15:23:00' (Christian year) -> '2026-10-17T15:23:00'."""
    match = re.match(r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", value or "")
    if not match:
        return ""
    d, m, y, hh, mm, ss = match.groups()
    year = int(y) - 543 if int(y) > 2400 else int(y)
    day = f"{year:04d}-{int(m):02d}-{int(d):02d}"
    return f"{day}T{int(hh):02d}:{mm}:{ss or '00'}" if hh else day


def _open_xls(data: bytes, name: str):
    try:
        import xlrd
    except ImportError:  # pragma: no cover - listed in requirements.txt
        raise RepStmError("ต้องติดตั้ง xlrd ก่อน (pip install -r requirements.txt) จึงจะอ่านไฟล์ e-Claim (.xls) ได้") from None
    try:
        return xlrd.open_workbook(file_contents=data)
    except Exception as exc:
        raise RepStmError(f"{name}: อ่านไฟล์ .xls ไม่ได้ ({exc})") from None


def _labelled(sheet, label: str, rows: int = 8) -> str:
    """The text after a label such as 'เลขที่เอกสาร' anywhere in the sheet's title rows."""
    for row in range(min(sheet.nrows, rows)):
        for col in range(sheet.ncols):
            text = _cell(sheet, row, col)
            if text.startswith(label):
                return text[len(label):].strip()
    return ""


def rep_account(doc: str) -> str:
    """The fund of an e-Claim REP from its document name: eclaim_{hcode}_OPCS_… / IPCS (ข้าราชการ) is "OFC",
    OPLGO / IPLGO "LGO", and OP / IP (บัตรทอง UC) "UCS"."""
    found = re.search(r"eclaim_\d+_(?:OP|IP)([A-Z]*)_", doc or "", re.I)
    kind = (found.group(1) if found else "CS").upper()
    return {"CS": "OFC", "": "UCS"}.get(kind, kind)


def parse_rep_ofc(data: bytes, name: str = "", book=None) -> list[Rep]:
    """The CSMBS (ข้าราชการ) e-Claim REP .xls (eclaim_{hcode}_OPCS_....xls), sheet "Detail": one row per visit with
    the REP number, TRAN_ID, SEQ NO (the VN; inpatient IPCS rows have only the AN, which is used instead), the net
    payout and an Error Code ('-' when it passed)."""
    book = book or _open_xls(data, name)
    sheet = book.sheet_by_name("Detail")
    head = next((r for r in range(min(sheet.nrows, 12)) if _cell(sheet, r, 0).startswith("REP")), None)
    if head is None:
        raise RepStmError(f"{name}: ไม่พบหัวตาราง REP ในแผ่นงาน Detail")
    # The UC file says "REP No." and puts notes under some labels ("เรียกเก็บ\n(1)"); the first column of a label wins.
    cols: dict[str, int] = {}
    for c in range(sheet.ncols):
        label = _cell(sheet, head, c).split("\n")[0].strip()
        if label:
            cols.setdefault("REP" if label.startswith("REP") else label, c)
    # UC: the claim is "รวมยอดเรียกเก็บ (1.3)" in the sub-header under the first เรียกเก็บ columns.
    for row in range(head + 1, min(head + 4, sheet.nrows)):
        for c in range(sheet.ncols):
            if _cell(sheet, row, c).startswith("รวมยอดเรียกเก็บ"):
                cols["เรียกเก็บ"] = c
    need = ("REP", "ลำดับที่", "TRAN_ID", "วันเข้ารักษา", "ชดเชยสุทธิ", "Error Code", "เรียกเก็บ", "SEQ NO")
    if any(label not in cols for label in need):
        raise RepStmError(f"{name}: คอลัมน์ในแผ่นงาน Detail ไม่ครบ ({', '.join(l for l in need if l not in cols)})")
    session = _labelled(sheet, "เลขที่เอกสาร").split()[0] if _labelled(sheet, "เลขที่เอกสาร") else ""
    hcode = (_labelled(sheet, "โรงพยาบาล").split() or [""])[0]
    issued = be_datetime(_labelled(sheet, "ออกรายงานวันที่").replace("เวลา", ""))
    account = rep_account(session or name)
    reps: dict[str, Rep] = {}
    for row in range(head + 1, sheet.nrows):
        rep_no, line, vn = _cell(sheet, row, cols["REP"]), _cell(sheet, row, cols["ลำดับที่"]), _cell(sheet, row, cols["SEQ NO"])
        if not vn and "AN" in cols:
            vn = _cell(sheet, row, cols["AN"])  # inpatient (IPCS) rows carry the AN instead of SEQ NO
        if not (rep_no.isdigit() and line.isdigit() and vn.isdigit()):
            continue  # the second header line, blank and note lines
        rep = reps.setdefault(rep_no, Rep(rep_no=rep_no, hcode=hcode, account=account, session=session,
                                          issued_at=issued, has_bil=True))
        codes = _codes(_cell(sheet, row, cols["Error Code"]).replace("-", ""))
        claimed = _money(_cell(sheet, row, cols["เรียกเก็บ"]))
        rep.bills.append(RepBill(
            line=int(line), stat="C" if codes else "A", station="", hcode=hcode, hmain="",
            authcode=_cell(sheet, row, cols["TRAN_ID"]), dttran=_ofc_datetime(_cell(sheet, row, cols["วันเข้ารักษา"]))[:16],
            invno=vn, bp="", amount=claimed, claimamt=claimed, codes=codes,
        ))
    if not reps:
        raise RepStmError(f"{name}: ไม่พบรายการในแผ่นงาน Detail")
    for rep in reps.values():
        rep.sent = len(rep.bills)
        rep.passed = sum(b.stat == "A" for b in rep.bills)
        rep.failed = rep.sent - rep.passed
    return list(reps.values())


def parse_stm_ofc(data: bytes, name: str = "", book=None) -> Stm:
    """The CSMBS (ข้าราชการ) OP statement .xls from NHSO e-Claim."""
    book = book or _open_xls(data, name)
    sheet = book.sheet_by_index(0)
    head = {}
    for row in range(min(sheet.nrows, 12)):
        text = _cell(sheet, row, 0)
        for label, key in (("เลขที่เอกสาร", "doc"), ("โรงพยาบาล", "hosp"), ("ออกรายงานวันที่", "issued")):
            if text.startswith(label):
                head[key] = text[len(label):].strip()
    doc = head.get("doc", "").split()[0] if head.get("doc") else ""
    found = re.match(r"(\d{5})_([A-Z]+?)(\d{6})_(\d+)$", doc)
    if not found:
        raise RepStmError(f"{name}: ไม่พบเลขที่เอกสาร STM ข้าราชการ (เช่น 10866_OP202610_02)")
    hcode, _, period, _ = found.groups()
    stm = Stm(stm_no=doc, hcode=hcode, account="OFC", period=period,
              date_issue=_ofc_datetime(head.get("issued", ""))[:10])
    total = 0.0
    for row in range(sheet.nrows):
        rep, seq, vn = _cell(sheet, row, 0), _cell(sheet, row, 1), _cell(sheet, row, 20)
        if not (rep.isdigit() and seq.isdigit() and vn.isdigit()):
            continue  # titles, the column headers repeated per REP, blank and note lines
        amount = _money(_cell(sheet, row, 19))
        an = _cell(sheet, row, 3)
        stm.bills.append(StmBill(
            seq=int(seq), station="", hmain="", hproc="", hcare=hcode, hn=_cell(sheet, row, 2), invno=vn, bf="",
            pcode=_cell(sheet, row, 8), care="" if an in {"-", ""} else an, payplan="", bp="",
            dttran=_ofc_datetime(_cell(sheet, row, 6)), copay="", cfh="", total=amount, extp="", rid=rep, cstat="",
        ))
        total += float(amount or 0)
    if not stm.bills:
        raise RepStmError(f"{name}: ไม่พบรายการในแผ่นงาน {sheet.name}")
    days = sorted(b.dttran[:10] for b in stm.bills if b.dttran)
    stm.date_start, stm.date_end = (days[0], days[-1]) if days else ("", "")
    stm.count, stm.amount = len(stm.bills), f"{total:.2f}"
    return stm


# ----------------------------------------------------------------- files
def _add(name: str, data: bytes, parsed: Parsed) -> None:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    if base.lower().endswith(".zip"):
        _read_zip(base, data, parsed)
        return
    if base.lower().endswith(".xls"):
        book = _open_xls(data, base)
        if "Detail" in book.sheet_names():  # e-Claim REP; the STM's first sheet is "พึงรับ"
            parsed.reps.update((rep.rep_no, rep) for rep in parse_rep_ofc(data, base, book))
        else:
            stm = parse_stm_ofc(data, base, book)
            parsed.stms[stm.stm_no] = stm
        return
    match = NAME.match(base)
    if not match:
        if not base.lower().endswith((".htm", ".html", ".xsl", ".jpg", ".png")) and not base.startswith("."):
            parsed.skipped.append(base)
        return
    part, ext = match["part"].upper(), match["ext"].lower()
    if part in {"REP", "SUP"} and ext == "rep":
        rep_no = match["no"]
        rep = parsed.reps.setdefault(rep_no, Rep(rep_no=rep_no, hcode=match["hcode"], account=match["acc"].upper()))
        (parse_aipn_rep if part == "REP" else parse_aipn_sup)(data, rep)
    elif part == "STMS" and ext == "xml":
        stm = parse_stm_aipn(data)
        parsed.stms[stm.stm_no] = stm
    elif part in {"SUMS", "SUPS"}:
        return  # the AIPN cover sheet and the per-main-hospital copy repeat STMS
    elif part in {"BIL", "DIS"} and ext == "bil":
        rep_no = match["no"]
        rep = parsed.reps.setdefault(rep_no, Rep(rep_no=rep_no, hcode=match["hcode"], account=match["acc"].upper()))
        (parse_rep_bil if part == "BIL" else parse_rep_dis)(data, rep)
    elif part == "STMP" and ext == "xml":
        stm = parse_stm(data)
        parsed.stms[stm.stm_no] = stm
    elif part == "SUMP" or ext in {"htm", "html"}:
        return  # the cover sheet and the browser copies repeat what STMP already holds
    else:
        parsed.skipped.append(base)


def _read_zip(name: str, data: bytes, parsed: Parsed) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise RepStmError(f"{name}: เปิด ZIP ไม่ได้") from None
    with archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if len(members) > MAX_FILES:
            raise RepStmError(f"{name}: มีไฟล์ใน ZIP มากเกินไป")
        for member in members:
            if member.file_size > 50 * 1024 * 1024:
                raise RepStmError(f"{name}: ไฟล์ {member.filename} ใหญ่เกินไป")
            _add(member.filename, archive.read(member), parsed)


def parse_upload(name: str, data: bytes) -> Parsed:
    """Read one uploaded file (a ZIP, a .BIL, an STMP .xml or a CSMBS STM .xls)."""
    parsed = Parsed()
    _add(name, data, parsed)
    if not parsed.reps and not parsed.stms:
        raise RepStmError(f"{name}: ไม่พบไฟล์ REP (SOCDBIL/SOCDDIS, AIPN SIGNREP), STM (STMP, AIPN SIGNSTMS) หรือ REP/STM ข้าราชการ (.xls) ที่อ่านได้")
    return parsed
