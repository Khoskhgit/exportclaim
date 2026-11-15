"""PPFS 8) บริการทดสอบการตั้งครรภ์ - urine pregnancy test or a self-test kit for women who may be pregnant, all rights.

ADP 30014, 75 baht a time, at most 4 times a year. The year is the fiscal year (October-September), as the user
chose for every PPFS limit. What HOSxP holds:
   opitemrece + s_drugitems.nhso_adp_code 30014   the billed item (what the ZIP claims)
   lab_order of "Pregnancy Test,Urine"            the UPT result (none when a self-test kit was given)
   ovstdiag Z32                                   the pregnancy test diagnosis; a Z32 visit with no 30014 item is
                                                  shown so the item can be keyed in HOSxP
Foreigners are a warning only: the service names no nationality.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .nhso18 import lit, lit_list
from .ppfs import ERROR, PREGTEST, SELF_PAID, WARN, Row, Select, _day, _money, fiscal_range, fiscal_year, self_paid_error
from .validate import text

MAX_A_YEAR = 4
AGE = (10, 60)
UPT = "Pregnancy Test|UPT|ตั้งครรภ์"

VISIT_COLS = """o.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, p.cid, p.nationality, p.sex, v.age_y"""
VISIT_JOINS = """JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype LEFT JOIN vn_stat v ON v.vn=o.vn"""


def _upt(q: Select, vns: list[str]) -> dict[str, str]:
    if not vns:
        return {}
    rows = q(f"""
        SELECT lh.vn, lo.lab_order_result result FROM lab_head lh JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number
        JOIN lab_items li ON li.lab_items_code=lo.lab_items_code
        WHERE lh.vn IN ({lit_list(vns)}) AND li.lab_items_name REGEXP {lit(UPT)} AND li.lab_items_name NOT REGEXP 'HCG'
        ORDER BY lh.vn, lo.lab_order_result IS NULL""")
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(text(r["vn"]), text(r["result"]))
    return out


def fetch(q: Select, start: date, end: date) -> dict:
    items = q(f"""
        SELECT {VISIT_COLS}, oo.icode, oo.paidst, d.name item_name, oo.qty, IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode {VISIT_JOINS}
        WHERE o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND d.nhso_adp_code={lit(PREGTEST)}""")
    unbilled = q(f"""
        SELECT {VISIT_COLS} FROM ovst o {VISIT_JOINS}
        WHERE o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND IFNULL(o.an,'')=''
          AND EXISTS(SELECT 1 FROM ovstdiag x WHERE x.vn=o.vn AND x.icd10 REGEXP '^Z32')
          AND NOT EXISTS(SELECT 1 FROM opitemrece oo JOIN s_drugitems d ON d.icode=oo.icode
                         WHERE oo.vn=o.vn AND d.nhso_adp_code={lit(PREGTEST)})""")
    hns = sorted({text(r["hn"]) for r in items})
    history = q(f"""
        SELECT oo.vn, o.hn, o.vstdate FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode
        WHERE o.hn IN ({lit_list(hns)}) AND o.vstdate BETWEEN {lit(fiscal_range(fiscal_year(start))[0])} AND {lit(fiscal_range(fiscal_year(end))[1])}
          AND d.nhso_adp_code={lit(PREGTEST)} AND IFNULL(oo.qty,0)>0
        GROUP BY oo.vn""") if hns else []
    vns = sorted({text(r["vn"]) for r in items + unbilled})
    z32 = {text(r["vn"]) for r in q(
        f"SELECT DISTINCT vn FROM ovstdiag WHERE vn IN ({lit_list(vns)}) AND icd10 REGEXP '^Z32'")} if vns else set()
    return {"items": items, "unbilled": unbilled, "history": history, "upt": _upt(q, vns), "z32": z32}


def check(data: dict, sent: dict[tuple[str, str], str] | None = None) -> list[Row]:
    sent = sent or {}
    rows: dict[str, Row] = {}
    info: dict[str, dict] = {}
    for r in data["items"]:
        vn = text(r["vn"])
        info.setdefault(vn, r)
        row = rows.get(vn)
        if row is None:
            row = rows[vn] = Row(key=f"{vn}|{PREGTEST}", vn=vn, code=PREGTEST, item_name=text(r.get("item_name")))
        row.qty += Decimal(str(r.get("qty") or 0))
        row.amount += _money(r.get("amount"))
        row.self_paid |= text(r.get("paidst")) == SELF_PAID
    for r in data["unbilled"]:
        vn = text(r["vn"])
        if vn not in rows:
            info[vn] = r
            rows[vn] = Row(key=f"{vn}|{PREGTEST}", vn=vn, code=PREGTEST, item_name="(ยังไม่คีย์ค่าบริการ)")

    # The number of each test in its fiscal year, from everything billed in HOSxP that year.
    by_year: dict[tuple[str, int], list[tuple[date, str]]] = {}
    for h in data["history"]:
        day = _day(h["vstdate"])
        if day:
            by_year.setdefault((text(h["hn"]), fiscal_year(day)), []).append((day, text(h["vn"])))
    for v in by_year.values():
        v.sort()

    for vn, row in rows.items():
        r = info[vn]
        day = _day(r.get("vstdate"))
        row.hn, row.date, row.time = text(r.get("hn")), day.isoformat() if day else "", str(r.get("vsttime") or "")[:8]
        row.ptname, row.cid = text(r.get("ptname")), text(r.get("cid"))
        row.pttype = f"{text(r.get('pttype'))}: {text(r.get('pttype_name'))}".strip(": ")
        age = r.get("age_y")
        upt = data["upt"].get(vn, "")
        has_z32 = vn in data["z32"]
        row.extra = {"age": int(age) if age is not None else None, "upt": upt, "z32": has_z32, "year": fiscal_year(day) if day else None}
        billed = row.qty > 0

        if text(r.get("sex")) != "2":
            row.add(ERROR, "NOT_FEMALE", "ไม่ใช่เพศหญิง")
        if text(r.get("an")):
            row.add(ERROR, "IPD", "visit นี้ Admit แล้ว (มี AN)")
        if not billed:
            row.add(ERROR, "NO_ITEM", f"มีรหัส Z32 แต่ยังไม่คีย์ค่าบริการ {PREGTEST} ใน HOSxP")
        self_paid_error(row)
        if billed and day:
            done = by_year.get((row.hn, fiscal_year(day)), [])
            position = next((i for i, (_, v) in enumerate(done, 1) if v == vn), None)
            row.count = position
            if position and position > MAX_A_YEAR:
                row.add(ERROR, "OVER_COUNT", f"ทดสอบครั้งที่ {position} ของปีงบ {fiscal_year(day) + 543} เกิน {MAX_A_YEAR} ครั้งต่อปี")
            earlier = [v for d, v in done[:(position or 1) - 1] if d == day]
            if earlier:
                row.add(ERROR, "SAME_DAY", f"ทดสอบซ้ำวันเดียวกันกับ VN {earlier[0]}")
        if text(r.get("nationality")) != "99":
            row.add(WARN, "NOT_THAI", "ไม่ใช่คนไทย (สัญชาติไม่ใช่ 99)")
        if age is not None and not AGE[0] <= int(age) <= AGE[1]:
            row.add(WARN, "AGE", f"อายุ {int(age)} ปี อยู่นอกวัยเจริญพันธุ์ ({AGE[0]}–{AGE[1]} ปี)")
        if billed and not upt:
            row.add(WARN, "NO_UPT", "ไม่พบผลตรวจ UPT ใน visit นี้ (ถ้าให้ชุดทดสอบด้วยตัวเอง ไม่ต้องแก้)")
        if billed and not has_z32:
            row.add(WARN, "NO_Z32", "ไม่มีรหัสวินิจฉัย Z32 (การทดสอบการตั้งครรภ์)")
        if billed and (row.qty != 1 or row.amount != Decimal("75")):
            row.add(WARN, "PRICE", f"ยอด {row.amount} บาท (จำนวน {row.qty:g}) ไม่ตรงอัตรา 75 บาท/ครั้ง")
        row.sent = sent.get((vn, PREGTEST), "")
    return sorted(rows.values(), key=lambda x: (x.date, x.time, x.vn))


def detail(q: Select, vn: str) -> dict:
    """The drawer of one visit: every 30014 test of the patient in that fiscal year with its UPT result."""
    visit = (q(f"""
        SELECT o.vn, o.hn, o.vstdate, p.nationality, v.age_y,
          (SELECT n.name FROM nationality n WHERE n.nationality=p.nationality) nationality_name
        FROM ovst o JOIN patient p ON p.hn=o.hn LEFT JOIN vn_stat v ON v.vn=o.vn WHERE o.vn={lit(vn)}""") or [None])[0]
    if visit is None:
        return {}
    year = fiscal_year(_day(visit["vstdate"]))
    first, last = fiscal_range(year)
    tests = q(f"""
        SELECT o.vn, o.vstdate, SUM(oo.qty) qty, SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode
        WHERE o.hn={lit(visit["hn"])} AND o.vstdate BETWEEN {lit(first)} AND {lit(last)} AND d.nhso_adp_code={lit(PREGTEST)}
        GROUP BY o.vn ORDER BY o.vstdate, o.vn""")
    upt = _upt(q, sorted({text(t["vn"]) for t in tests} | {vn}))
    items = q(f"""
        SELECT oo.icode, CONCAT_WS(' ',d.name,d.strength) name, d.nhso_adp_code adp, SUM(oo.qty) qty,
          SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn={lit(vn)}
        GROUP BY oo.icode ORDER BY d.nhso_adp_code IS NULL, oo.icode""")
    dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={lit(vn)} ORDER BY diagtype, icd10")
    return {"visit": visit, "year": year, "tests": tests, "upt": upt, "items": items, "dx": dx}
