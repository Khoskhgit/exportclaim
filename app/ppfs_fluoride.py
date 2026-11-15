"""PPFS 19) บริการเคลือบฟลูออไรด์ - fluoride varnish (22,600 ppm) with counselling, age 25-59 at risk of caries.

ADP 15001, 100 baht a time, at most 2 times per person per fiscal year (October-September, as for every PPFS
limit). The dental record of the varnish is dtmain with ICD-10-TM 2387020 / 2387021 (topical fluoride, adult):
a record with no 15001 item is shown so the item can be keyed; a 15001 item with no record is a warning.
Neither the caries risk nor the nationality is checked: the announcement names no nationality and HOSxP has no
field for the risk. The children's fluoride (2377020, 4-12 years) and whole-mouth fluoride (61101) are other items.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .nhso18 import lit, lit_list
from .ppfs import (ERROR, SELF_PAID, WARN, Row, Select, _day, _money, fiscal_range, fiscal_year,
                   self_paid_error)
from .validate import text

CODE = "15001"
RATE = Decimal("100")
AGE = (25, 59)
MAX_A_YEAR = 2
DENTAL = "^238702[01]"

VISIT_COLS = """o.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, p.cid, p.nationality, p.sex, v.age_y"""
VISIT_JOINS = """JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype LEFT JOIN vn_stat v ON v.vn=o.vn"""
FEE = "NOT EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode)"
BILLED = (f"EXISTS(SELECT 1 FROM opitemrece oo JOIN s_drugitems d ON d.icode=oo.icode "
          f"WHERE oo.vn=o.vn AND d.nhso_adp_code='{CODE}')")


def _dental(q: Select, vns: list[str]) -> dict[str, list[str]]:
    """VN -> the topical fluoride procedures recorded in the dental record."""
    if not vns:
        return {}
    out: dict[str, list[str]] = {}
    for r in q(f"""
            SELECT dt.vn, tm.icd10tm_operation_code tm, tm.name FROM dtmain dt JOIN dttm tm ON tm.code=dt.tmcode
            WHERE dt.vn IN ({lit_list(vns)}) AND tm.icd10tm_operation_code REGEXP {lit(DENTAL)}"""):
        label = f"{text(r['tm'])} {text(r['name'])}".strip()
        if label not in out.setdefault(text(r["vn"]), []):
            out[text(r["vn"])].append(label)
    return out


def fetch(q: Select, start: date, end: date) -> dict:
    items = q(f"""
        SELECT {VISIT_COLS}, oo.icode, oo.paidst, d.name item_name, oo.qty, IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode {VISIT_JOINS}
        WHERE o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND d.nhso_adp_code={lit(CODE)} AND {FEE}""")
    unbilled = q(f"""
        SELECT {VISIT_COLS} FROM ovst o {VISIT_JOINS}
        WHERE o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND IFNULL(o.an,'')='' AND v.age_y BETWEEN {AGE[0]} AND {AGE[1]}
          AND EXISTS(SELECT 1 FROM dtmain dt JOIN dttm tm ON tm.code=dt.tmcode
                     WHERE dt.vn=o.vn AND tm.icd10tm_operation_code REGEXP {lit(DENTAL)})
          AND NOT {BILLED}""")
    hns = sorted({text(r["hn"]) for r in items})
    history = q(f"""
        SELECT oo.vn, o.hn, o.vstdate FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode
        WHERE o.hn IN ({lit_list(hns)}) AND d.nhso_adp_code={lit(CODE)} AND {FEE} AND IFNULL(oo.qty,0)>0
          AND o.vstdate BETWEEN {lit(fiscal_range(fiscal_year(start))[0])} AND {lit(fiscal_range(fiscal_year(end))[1])}
        GROUP BY oo.vn""") if hns else []
    vns = sorted({text(r["vn"]) for r in items + unbilled})
    return {"items": items, "unbilled": unbilled, "history": history, "dental": _dental(q, vns)}


def check(data: dict, sent: dict[tuple[str, str], str] | None = None) -> list[Row]:
    sent = sent or {}
    rows: dict[str, Row] = {}
    info: dict[str, dict] = {}
    for r in data["items"]:
        vn = text(r["vn"])
        info.setdefault(vn, r)
        row = rows.get(vn)
        if row is None:
            row = rows[vn] = Row(key=f"{vn}|{CODE}", vn=vn, code=CODE, item_name=text(r.get("item_name")))
        row.qty += Decimal(str(r.get("qty") or 0))
        row.amount += _money(r.get("amount"))
        row.self_paid |= text(r.get("paidst")) == SELF_PAID
    for r in data["unbilled"]:
        vn = text(r["vn"])
        if vn not in rows:
            info[vn] = r
            rows[vn] = Row(key=f"{vn}|{CODE}", vn=vn, code=CODE, item_name="(ยังไม่คีย์ค่าบริการ)")

    claims: dict[tuple[str, int], list[tuple[date, str]]] = {}
    for h in data["history"]:
        if day := _day(h["vstdate"]):
            claims.setdefault((text(h["hn"]), fiscal_year(day)), []).append((day, text(h["vn"])))
    for v in claims.values():
        v.sort()

    for vn, row in rows.items():
        r = info[vn]
        day = _day(r.get("vstdate"))
        row.hn, row.date, row.time = text(r.get("hn")), day.isoformat() if day else "", str(r.get("vsttime") or "")[:8]
        row.ptname, row.cid = text(r.get("ptname")), text(r.get("cid"))
        row.pttype = f"{text(r.get('pttype'))}: {text(r.get('pttype_name'))}".strip(": ")
        age = r.get("age_y")
        dental = data["dental"].get(vn, [])
        year = fiscal_year(day) if day else None
        done = claims.get((row.hn, year), [])
        billed = row.qty > 0
        position = next((i for i, (_, v) in enumerate(done, 1) if v == vn), None) if billed else None
        row.count = position
        row.extra = {"age": int(age) if age is not None else None, "dental": dental, "year": year,
                     "year_total": len(done)}

        if text(r.get("an")):
            row.add(ERROR, "IPD", "visit นี้ Admit แล้ว (มี AN)")
        self_paid_error(row)
        if not billed:
            row.add(ERROR, "NO_ITEM", f"มีบันทึกทาฟลูออไรด์ในทันตกรรม แต่ยังไม่คีย์ค่าบริการ {CODE} ใน HOSxP")
        if age is not None and not AGE[0] <= int(age) <= AGE[1]:
            row.add(ERROR, "AGE", f"อายุ {int(age)} ปี เบิกได้เฉพาะอายุ {AGE[0]}–{AGE[1]} ปี")
        if position and position > MAX_A_YEAR:
            row.add(ERROR, "OVER_YEAR", f"ครั้งที่ {position} ของปีงบ {year + 543} เกิน {MAX_A_YEAR} ครั้งต่อปี")
        if billed and not dental:
            row.add(WARN, "NO_DENTAL", "ไม่พบบันทึกทาฟลูออไรด์ (2387020 / 2387021) ในทันตกรรมของ visit นี้")
        if billed and (row.qty != 1 or row.amount != RATE):
            row.add(WARN, "PRICE", f"ยอด {row.amount} บาท (จำนวน {row.qty:g}) ไม่ตรงอัตรา {RATE} บาท/ครั้ง")
        row.sent = sent.get((vn, CODE), "")
    return sorted(rows.values(), key=lambda x: (x.date, x.time, x.vn))


def detail(q: Select, vn: str) -> dict:
    """The drawer of one visit: every 15001 claim of the patient with its dental record."""
    visit = (q(f"""
        SELECT o.vn, o.hn, o.vstdate, p.nationality, v.age_y,
          (SELECT n.name FROM nationality n WHERE n.nationality=p.nationality) nationality_name
        FROM ovst o JOIN patient p ON p.hn=o.hn LEFT JOIN vn_stat v ON v.vn=o.vn WHERE o.vn={lit(vn)}""") or [None])[0]
    if visit is None:
        return {}
    claims = q(f"""
        SELECT o.vn, o.vstdate, SUM(oo.qty) qty, SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode
        WHERE o.hn={lit(visit["hn"])} AND d.nhso_adp_code={lit(CODE)} AND {FEE}
        GROUP BY o.vn ORDER BY o.vstdate, o.vn""")
    items = q(f"""
        SELECT oo.icode, CONCAT_WS(' ',d.name,d.strength) name, d.nhso_adp_code adp, SUM(oo.qty) qty,
          SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount, EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode) is_drug
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn={lit(vn)}
        GROUP BY oo.icode ORDER BY d.nhso_adp_code IS NULL, oo.icode""")
    dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={lit(vn)} ORDER BY diagtype, icd10")
    return {"visit": visit, "claims": claims, "dental": _dental(q, sorted({text(c["vn"]) for c in claims} | {vn})),
            "items": items, "dx": dx}
