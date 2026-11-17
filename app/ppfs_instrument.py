"""PPFS Instrument สิทธิคนพิการ - devices for disabled people (ADP type 2), sent in the 18 files / FDH.

Ported from the old program's "รายชื่อสิทธิผู้พิการ" report, with these choices of the user (2026-11-17):
   visits     every OPD visit under a UC right (pttype.hipdata_code UCS/WEL); at this hospital disabled patients
              mostly come under the normal UC rights, so the disabled right and the disability registry
              (person_deformed) are only shown, never checked (the user dropped the warning, 2026-11-17)
   devices    opitemrece + s_drugitems with nhso_adp_type_id=2 and an ADP code starting 25, 81, 82, 85, 87, 88, 89 or
              equal to 6006 (the old regexp '^25|6006|^8[1|2|5|7|8|9]', without the '|' it also matched)
   ADP list   the old program's list of device codes (the "ADP" flag); a code outside it is a warning
   web        the device recorded in the NHSO device web: rcmdb.equipment_service, or the Export-Equipment*.CSV
              imported on the PPFS page ("ตรวจสอบ"); it may be claimed twice, so it is a warning, and the CSV's
              เงินพึงได้ (Income) is compared with what HOSxP bills
One row per visit and ADP code. HOSxP is only read.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable

from .dmrem_registry import iso as iso_date
from .nhso18 import lit, lit_list
from .ppfs import ERROR, SELF_PAID, WARN, Row, Select, _day, _money, self_paid_error
from .store import Store
from .validate import text

KEY = "instrument"
CODE_PATTERN = "^(25|81|82|85|87|88|89)|^6006$"
DISABLED_PTTYPE = "พิการ"
ADP_LIST = frozenset("""
2505 2506 2507 2508 2509 6006
8101 8102 8103 8104 8105 8106 8107 8108 8109 8110 8111 8112 8113 8114 8115 8116 8117 8118 8119 8120 8121 8122
8201 8202 8203 8204 8205 8206 8209 8218 8219 8220 8221 8222 8223 8224 8225 8226 8227 8228 8229 8230 8231 8232 8233
8234 8235 8236 8508 8524 8526 8527 8528
8706 8707 8708 8709 8710 8711 8801 8802 8803 8804 8805 8809 8810 8901 8902 8903 8904 9001
""".split())
ITEM_FILTER = f"d.nhso_adp_type_id=2 AND d.nhso_adp_code REGEXP {lit(CODE_PATTERN)}"


def is_instrument(code: str) -> bool:
    return bool(re.search(CODE_PATTERN, code or ""))


# ------------------------------------------------------------------ the NHSO device web export (CSV)
# The column order of Export-Equipment*.CSV, as the old program read it.
CSV_COLUMNS = ["Zone", "ZoneName", "ProvinceCode", "ProvinceName", "HospCode", "HospName", "VisitNo", "VstDate", "CID",
               "Fname", "Lname", "HN", "AN", "Target", "DisabillityType", "ADL", "MainInscl", "SubInscl", "ServiceType",
               "Code", "Name", "BrandCode", "BrandName", "ICD10", "ICD9", "Medium_Price", "Service_Charge", "Real_Price",
               "Diff", "Amount", "Request_Price", "Income", "RegDate", "Staff", "SerialNo", "Status"]
KEEP = ("hospcode", "vstdate", "cid", "hn", "code", "name", "amount", "request_price", "income", "regdate", "serialno", "status")


class CsvError(ValueError):
    pass


@dataclass
class WebRow:
    cid: str
    hn: str
    vstdate: str
    code: str
    name: str
    amount: Decimal
    request_price: Decimal
    income: Decimal
    reg_date: str
    serial_no: str
    status: str
    hospcode: str


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _num(value: str) -> Decimal:
    try:
        return Decimal(text(value).replace(",", "") or "0").quantize(Decimal("0.01"))
    except ArithmeticError:
        return Decimal("0.00")


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp874"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CsvError("อ่านไฟล์ไม่ได้ (ไม่ใช่ UTF-8 หรือ TIS-620)")


def parse_csv(data: bytes) -> list[WebRow]:
    """The rows of Export-Equipment*.CSV. A header row maps the columns by name; without one the old program's column
    order is used. Patient names and the staff name are not kept."""
    lines = [r for r in csv.reader(io.StringIO(_decode(data))) if any(c.strip() for c in r)]
    if not lines:
        raise CsvError("ไฟล์ว่าง")
    known = {_norm(c): i for i, c in enumerate(CSV_COLUMNS)}
    head = [_norm(c) for c in lines[0]]
    if "cid" in head and "vstdate" in head:
        where = {name: head.index(name) for name in known if name in head}
        body = lines[1:]
    else:
        where = dict(known)
        body = lines
    if "code" not in where:
        raise CsvError("ไม่พบคอลัมน์ Code ในไฟล์")
    get = lambda row, name: text(row[where[name]]) if name in where and where[name] < len(row) else ""
    out = []
    for row in body:
        day = iso_date(get(row, "vstdate")) or text(get(row, "vstdate"))[:10]
        cid, hn = re.sub(r"\D", "", get(row, "cid")), get(row, "hn")
        if not _day(day) or not (cid or hn) or not get(row, "code"):
            continue
        out.append(WebRow(cid=cid if len(cid) == 13 else "", hn=hn, vstdate=day, code=get(row, "code"), name=get(row, "name"),
                          amount=_num(get(row, "amount")), request_price=_num(get(row, "requestprice")),
                          income=_num(get(row, "income")), reg_date=iso_date(get(row, "regdate")),
                          serial_no=get(row, "serialno"), status=get(row, "status"), hospcode=get(row, "hospcode")))
    if not out:
        raise CsvError("ไม่พบรายการในไฟล์ (ต้องเป็น Export-Equipment*.CSV จากโปรแกรมบันทึกอุปกรณ์คนพิการ)")
    return out


SCHEMA = """
CREATE TABLE IF NOT EXISTS ppfs_instrument_web (
  cid TEXT NOT NULL, hn TEXT NOT NULL, vstdate TEXT NOT NULL, code TEXT NOT NULL, serial_no TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '', amount TEXT NOT NULL DEFAULT '', request_price TEXT NOT NULL DEFAULT '',
  income TEXT NOT NULL DEFAULT '', reg_date TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
  hospcode TEXT NOT NULL DEFAULT '', file TEXT NOT NULL DEFAULT '', imported_at TEXT NOT NULL,
  PRIMARY KEY (cid, hn, vstdate, code, serial_no)
);
CREATE INDEX IF NOT EXISTS ppfs_instrument_web_day ON ppfs_instrument_web(vstdate);
"""


class WebStore:
    """What was imported from the device web, kept so a later search still shows it."""

    def __init__(self, store: Store):
        self._db = store._db
        with self._db() as db:
            db.executescript(SCHEMA)

    def save(self, rows: list[WebRow], file: str) -> dict:
        at = datetime.now().isoformat(timespec="seconds")
        added = 0
        with self._db() as db:
            for r in rows:
                key = (r.cid, r.hn if not r.cid else "", r.vstdate, r.code, r.serial_no)
                added += db.execute("DELETE FROM ppfs_instrument_web WHERE cid=? AND hn=? AND vstdate=? AND code=? AND serial_no=?",
                                    key).rowcount == 0
                db.execute("INSERT INTO ppfs_instrument_web (cid, hn, vstdate, code, serial_no, name, amount, request_price, income, "
                           "reg_date, status, hospcode, file, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (*key, r.name, str(r.amount), str(r.request_price), str(r.income), r.reg_date, r.status,
                            r.hospcode, file, at))
        return {"rows": len(rows), "added": added, "updated": len(rows) - added}

    def between(self, start: date, end: date) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM ppfs_instrument_web WHERE vstdate BETWEEN ? AND ? ORDER BY vstdate",
                                                (start.isoformat(), end.isoformat()))]

    def summary(self) -> dict:
        with self._db() as db:
            r = db.execute("SELECT COUNT(*) n, MIN(vstdate) first, MAX(vstdate) last, MAX(imported_at) at FROM ppfs_instrument_web").fetchone()
            name = db.execute("SELECT file FROM ppfs_instrument_web ORDER BY imported_at DESC LIMIT 1").fetchone()
        return {"n": r["n"], "first": r["first"] or "", "last": r["last"] or "", "imported_at": r["at"] or "",
                "file": name["file"] if name else ""}


# ------------------------------------------------------------------ HOSxP (read only)
VISIT_COLS = """o.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name, ptt.pcode, p.cid,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname,
          (SELECT vp.claim_code FROM visit_pttype vp WHERE vp.vn=o.vn AND vp.pttype=o.pttype LIMIT 1) authen"""
VISIT_JOINS = "JOIN patient p ON p.hn=o.hn JOIN pttype ptt ON ptt.pttype=o.pttype"
UC = "ptt.hipdata_code IN ('UCS','WEL')"


def fetch(q: Select, start: date, end: date, rcm: str, web: list[dict]) -> dict:
    items = q(f"""
        SELECT {VISIT_COLS}, oo.icode, oo.paidst, d.nhso_adp_code code, CONCAT_WS(' ',d.name,d.strength) item_name,
          oo.qty, IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode {VISIT_JOINS}
        WHERE o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND IFNULL(o.an,'')='' AND {UC} AND {ITEM_FILTER}""")
    # Visits of the imported web rows that HOSxP bills nothing for: shown so the device can be keyed (or left out).
    keys = {(w["cid"], w["vstdate"]) for w in web if w["cid"]} | {("hn:" + w["hn"], w["vstdate"]) for w in web if not w["cid"]}
    web_visits: list[dict] = []
    cids = sorted({c for c, _ in keys if not c.startswith("hn:")})
    hns = sorted({c[3:] for c, _ in keys if c.startswith("hn:")})
    if cids or hns:
        who = " OR ".join(x for x in (f"p.cid IN ({lit_list(cids)})" if cids else "", f"o.hn IN ({lit_list(hns)})" if hns else "") if x)
        web_visits = [v for v in q(f"""
            SELECT {VISIT_COLS} FROM ovst o {VISIT_JOINS}
            WHERE o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND IFNULL(o.an,'')='' AND ({who})""")
            if (text(v["cid"]), str(v["vstdate"])[:10]) in keys or ("hn:" + text(v["hn"]), str(v["vstdate"])[:10]) in keys]
    vns = sorted({text(r["vn"]) for r in items + web_visits})
    hn_list = sorted({text(r["hn"]) for r in items + web_visits})
    deformed = {}
    if hn_list:
        for r in q(f"""
            SELECT p.hn, MIN(pd.register_date) register_date, MAX(pd.deformed_no) deformed_no FROM patient p
            JOIN person pe ON pe.cid=p.cid JOIN person_deformed pd ON pd.person_id=pe.person_id
            WHERE p.hn IN ({lit_list(hn_list)}) GROUP BY p.hn"""):
            deformed[text(r["hn"])] = r
    has_rcm = lambda t: bool(q(f"SELECT 1 FROM information_schema.tables WHERE table_schema={lit(rcm)} AND table_name={lit(t)}"))
    equipment = {}
    if vns and has_rcm("equipment_service"):
        for r in q(f"SELECT VN vn, Equipment_Code code, RealPrice price, RegDate regdate, Status status FROM `{rcm}`.equipment_service "
                   f"WHERE VN IN ({lit_list(vns)})"):
            equipment.setdefault((text(r["vn"]), text(r["code"])), r)
    eclaim = {}
    if vns and has_rcm("repeclaim"):
        for r in q(f"SELECT vn, GROUP_CONCAT(DISTINCT rep) rep, MAX(Compensated) paid FROM `{rcm}`.repeclaim "
                   f"WHERE vn IN ({lit_list(vns)}) AND IFNULL(errorcode,'')='' GROUP BY vn"):
            eclaim[text(r["vn"])] = {"rep": text(r["rep"]), "paid": text(r["paid"])}
    if vns and has_rcm("repdata"):
        for r in q(f"SELECT vn, GROUP_CONCAT(DISTINCT rep) rep FROM `{rcm}`.repdata "
                   f"WHERE vn IN ({lit_list(vns)}) AND IFNULL(errorcode,'')='' GROUP BY vn"):
            eclaim.setdefault(text(r["vn"]), {"rep": text(r["rep"]), "paid": ""})
    return {"items": items, "web_visits": web_visits, "web": web, "deformed": deformed, "equipment": equipment, "eclaim": eclaim}


def _web_for(web: list[dict], cid: str, hn: str, day: str) -> list[dict]:
    return [w for w in web if w["vstdate"] == day and ((cid and w["cid"] == cid) or (not w["cid"] and w["hn"] == hn))]


def check(data: dict, sent: dict[tuple[str, str], str] | None = None) -> list[Row]:
    sent = sent or {}
    rows: dict[tuple[str, str], Row] = {}
    info: dict[str, dict] = {}
    for r in data["items"]:
        vn, code = text(r["vn"]), text(r["code"])
        info.setdefault(vn, r)
        row = rows.get((vn, code))
        if row is None:
            row = rows[(vn, code)] = Row(key=f"{vn}|{code}", vn=vn, code=code, item_name=text(r.get("item_name")))
        row.qty += Decimal(str(r.get("qty") or 0))
        row.amount += _money(r.get("amount"))
        row.self_paid |= text(r.get("paidst")) == SELF_PAID
    for v in data["web_visits"]:
        vn = text(v["vn"])
        info.setdefault(vn, v)
        for w in _web_for(data["web"], text(v["cid"]), text(v["hn"]), str(v["vstdate"])[:10]):
            if (vn, w["code"]) not in rows:
                rows[(vn, w["code"])] = Row(key=f"{vn}|{w['code']}", vn=vn, code=w["code"], item_name=w["name"] or "(ยังไม่คีย์ใน HOSxP)")

    for (vn, code), row in rows.items():
        r = info[vn]
        day = _day(r.get("vstdate"))
        row.hn, row.date, row.time = text(r.get("hn")), day.isoformat() if day else "", str(r.get("vsttime") or "")[:8]
        row.ptname, row.cid = text(r.get("ptname")), text(r.get("cid"))
        row.pttype = f"{text(r.get('pttype'))}: {text(r.get('pttype_name'))}".strip(": ")
        disabled_right = DISABLED_PTTYPE in text(r.get("pttype_name")) or text(r.get("pcode")) == "AD"
        deformed = data["deformed"].get(row.hn)
        web = [w for w in _web_for(data["web"], row.cid, row.hn, row.date) if w["code"] == code]
        equipment = data["equipment"].get((vn, code))
        income = sum((Decimal(w["income"] or 0) for w in web), Decimal(0))
        eclaim = data["eclaim"].get(vn, {})
        row.extra = {
            "authen": text(r.get("authen")), "in_list": code in ADP_LIST, "disabled_right": disabled_right,
            "deformed": bool(deformed), "deformed_no": text(deformed.get("deformed_no")) if deformed else "",
            "deformed_date": str(deformed.get("register_date") or "")[:10] if deformed else "",
            "web": bool(web or equipment), "income": f"{income:.2f}" if web else "",
            "web_reg": web[0]["reg_date"] if web else str(equipment.get("regdate") or "")[:10] if equipment else "",
            "web_status": web[0]["status"] if web else text(equipment.get("status")) if equipment else "",
            "eclaim": eclaim.get("rep", ""), "eclaim_paid": eclaim.get("paid", ""),
        }
        billed = row.qty > 0
        row.count = int(row.qty) if billed and row.qty == int(row.qty) else None
        if not billed:
            row.add(ERROR, "NO_ITEM", f"มีในเว็บบันทึกอุปกรณ์ แต่ไม่พบรายการ {code} ใน HOSxP (คีย์ค่าอุปกรณ์ก่อนส่ง)")
        self_paid_error(row)
        if code not in ADP_LIST:
            row.add(WARN, "NOT_IN_LIST", f"รหัส {code} ไม่อยู่ในรายการ ADP อุปกรณ์คนพิการ")
        if not row.extra["authen"]:
            row.add(WARN, "NO_AUTHEN", "ไม่มี AuthenCode (Claim Code) ของ visit นี้")
        if billed and row.extra["web"]:
            row.add(WARN, "IN_WEB", "บันทึกในเว็บอุปกรณ์คนพิการแล้ว ระวังเบิกซ้ำ")
        if billed and web and row.amount > income:
            row.add(WARN, "OVER_INCOME", f"ยอด HOSxP {row.amount} บาท มากกว่าเงินพึงได้ในเว็บ {income} บาท")
        if row.extra["eclaim"]:
            row.add(WARN, "ECLAIM", f"visit นี้มี REP e-Claim แล้ว ({row.extra['eclaim']})")
        row.sent = sent.get((vn, code), "")
    return sorted(rows.values(), key=lambda x: (x.date, x.time, x.vn, x.code))


def detail(q: Select, vn: str) -> dict:
    """The drawer: the patient's devices in the past 2 years, the disability registry and the visit's items and dx."""
    visit = (q(f"""
        SELECT o.vn, o.hn, o.vstdate, p.nationality, p.cid,
          (SELECT n.name FROM nationality n WHERE n.nationality=p.nationality) nationality_name
        FROM ovst o JOIN patient p ON p.hn=o.hn WHERE o.vn={lit(vn)}""") or [None])[0]
    if visit is None:
        return {}
    day = _day(visit["vstdate"])
    since = date(day.year - 2, day.month, min(day.day, 28)) if day else date(1900, 1, 1)
    history = q(f"""
        SELECT o.vn, o.vstdate, d.nhso_adp_code code, CONCAT_WS(' ',d.name,d.strength) name, SUM(oo.qty) qty,
          SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode
        WHERE o.hn={lit(visit["hn"])} AND o.vstdate BETWEEN {lit(since)} AND {lit(day)} AND {ITEM_FILTER}
        GROUP BY o.vn, d.nhso_adp_code, oo.icode ORDER BY o.vstdate DESC, o.vn""")
    deformed = q(f"""
        SELECT pd.deformed_no, pd.register_date, pd.certificate_date FROM person pe JOIN person_deformed pd ON pd.person_id=pe.person_id
        WHERE pe.cid={lit(visit["cid"])} ORDER BY pd.register_date""") if text(visit.get("cid")) else []
    items = q(f"""
        SELECT oo.icode, CONCAT_WS(' ',d.name,d.strength) name, d.nhso_adp_code adp, SUM(oo.qty) qty,
          SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn={lit(vn)}
        GROUP BY oo.icode ORDER BY d.nhso_adp_code IS NULL, oo.icode""")
    dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={lit(vn)} ORDER BY diagtype, icd10")
    return {"visit": visit, "history": history, "deformed": deformed, "items": items, "dx": dx}


def web_rows_for(rows: Iterable[dict], cid: str, hn: str, day: str) -> list[dict]:
    return _web_for(list(rows), cid, hn, day)
