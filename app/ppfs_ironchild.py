"""PPFS 27) บริการยาเสริมธาตุเหล็กเพื่อป้องกันโลหิตจางจากการขาดธาตุเหล็ก - iron syrup for Thai children aged
6-12 months, all rights, 50 baht once a fiscal year (October-September).

HOSxP keys the fee as item 3004084 "18จ่ายยาเสริมธาตุเหล็ก 6เดือน-1ปี6เดือน" under the placeholder ADP code 10000
(named "NONE"), which the child Hb/Hct screening shares. So this service reads the fee by its icode
(PPFS_IRON_CHILD_ICODES) and the ZIP sends the ADP code set in PPFS_IRON_CHILD_ADP in place of 10000. Until that
code is set every row is an error, so nothing goes out as 10000. Ages are the age at the visit in months (vn_stat);
the 2-5 year item (3004023) is not part of this service as the user gave it.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .nhso18 import lit, lit_list
from .ppfs import ERROR, SELF_PAID, WARN, Row, Select, _day, _money, fiscal_range, fiscal_year, self_paid_error
from .validate import text

HOSXP_CODE = "10000"
RATE = Decimal("50")
MONTHS = (6, 12)
IRON = "FERR|IRON|TRIFER"

VISIT_COLS = """o.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, p.cid, p.nationality, p.sex, v.age_y, v.age_m, v.age_d"""
VISIT_JOINS = """JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype LEFT JOIN vn_stat v ON v.vn=o.vn"""


def _iron(q: Select, vns: list[str]) -> dict[str, dict]:
    if not vns:
        return {}
    out: dict[str, dict] = {}
    for r in q(f"""
            SELECT oo.vn, CONCAT_WS(' ',di.name,di.strength) name, SUM(oo.qty) qty FROM opitemrece oo
            JOIN drugitems di ON di.icode=oo.icode
            WHERE oo.vn IN ({lit_list(vns)}) AND CONCAT_WS(' ',di.name,di.generic_name) REGEXP {lit(IRON)}
            GROUP BY oo.vn, oo.icode"""):
        entry = out.setdefault(text(r["vn"]), {"names": [], "qty": Decimal("0")})
        entry["names"].append(f"{text(r['name'])} × {Decimal(str(r['qty'] or 0)):g}")
        entry["qty"] += Decimal(str(r["qty"] or 0))
    return {vn: {"name": ", ".join(e["names"]), "qty": e["qty"]} for vn, e in out.items()}


def fetch(q: Select, start: date, end: date, icodes: tuple[str, ...]) -> dict:
    items = q(f"""
        SELECT {VISIT_COLS}, oo.icode, oo.paidst, d.name item_name, oo.qty, IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode {VISIT_JOINS}
        WHERE o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND oo.icode IN ({lit_list(icodes)})""")
    hns = sorted({text(r["hn"]) for r in items})
    history = q(f"""
        SELECT oo.vn, o.hn, o.vstdate FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn
        WHERE o.hn IN ({lit_list(hns)}) AND oo.icode IN ({lit_list(icodes)}) AND IFNULL(oo.qty,0)>0
          AND o.vstdate BETWEEN {lit(fiscal_range(fiscal_year(start))[0])} AND {lit(fiscal_range(fiscal_year(end))[1])}
        GROUP BY oo.vn""") if hns else []
    return {"items": items, "history": history, "iron": _iron(q, sorted({text(r["vn"]) for r in items}))}


def months_old(r: dict) -> int | None:
    if r.get("age_y") is None:
        return None
    return int(r["age_y"]) * 12 + int(r.get("age_m") or 0)


def check(data: dict, adp: str, sent: dict[tuple[str, str], str] | None = None) -> list[Row]:
    sent = sent or {}
    rows: dict[str, Row] = {}
    info: dict[str, dict] = {}
    for r in data["items"]:
        vn = text(r["vn"])
        info.setdefault(vn, r)
        row = rows.get(vn)
        if row is None:
            row = rows[vn] = Row(key=f"{vn}|{HOSXP_CODE}", vn=vn, code=HOSXP_CODE, item_name=text(r.get("item_name")))
        row.qty += Decimal(str(r.get("qty") or 0))
        row.amount += _money(r.get("amount"))
        row.self_paid |= text(r.get("paidst")) == SELF_PAID

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
        months = months_old(r)
        given = data["iron"].get(vn)
        year = fiscal_year(day) if day else None
        earlier = [(d, v) for d, v in claims.get((row.hn, year), []) if (d, v) < (day, vn)] if day else []
        first = earlier[0] if earlier else None
        row.extra = {"months": months, "age_text": f"{r.get('age_y')} ปี {r.get('age_m') or 0} เดือน" if months is not None else "",
                     "drug_name": given["name"] if given else "", "adp": adp, "year": year,
                     "first": first[0].isoformat() if first else "", "first_vn": first[1] if first else ""}
        billed = row.qty > 0
        row.count = len(earlier) + 1 if billed else None

        if not adp:
            row.add(ERROR, "NO_ADP", "ยังไม่ได้ตั้งรหัส ADP ของบริการนี้ (PPFS_IRON_CHILD_ADP ใน .env) HOSxP ผูกไว้เป็น 10000 = NONE")
        if text(r.get("nationality")) != "99":
            row.add(ERROR, "NOT_THAI", "ไม่ใช่คนไทย (สัญชาติไม่ใช่ 99)")
        if text(r.get("an")):
            row.add(ERROR, "IPD", "visit นี้ Admit แล้ว (มี AN)")
        self_paid_error(row)
        if months is not None and not MONTHS[0] <= months <= MONTHS[1]:
            row.add(ERROR, "AGE", f"อายุ {months} เดือน เบิกได้เฉพาะอายุ {MONTHS[0]}–{MONTHS[1]} เดือน")
        if billed and first:
            row.add(ERROR, "OVER_YEAR", f"ปีงบ {year + 543} เบิกไปแล้วเมื่อ {_thai(first[0])} (VN {first[1]}) ครั้งเดียวต่อปีงบ")
        if billed and not given:
            row.add(WARN, "NO_IRON", "ไม่พบการจ่ายยาเสริมธาตุเหล็กใน visit นี้")
        if billed and (row.qty != 1 or row.amount != RATE):
            row.add(WARN, "PRICE", f"ยอด {row.amount} บาท (จำนวน {row.qty:g}) ไม่ตรงอัตรา {RATE} บาท/ครั้ง/ปี")
        row.sent = sent.get((vn, HOSXP_CODE), "")
    return sorted(rows.values(), key=lambda x: (x.date, x.time, x.vn))


def _thai(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}/{day.year + 543}"


def rewrite_adp(data: dict[str, list[dict]], vns: set[str], adp: str) -> None:
    """Send the code e-Claim expects in place of HOSxP's placeholder 10000, for the visits being claimed."""
    for r in data.get("ADP", []):
        if text(r.get("SEQ")) in vns and text(r.get("CODE")) == HOSXP_CODE:
            r["CODE"] = adp


def detail(q: Select, vn: str, icodes: tuple[str, ...]) -> dict:
    visit = (q(f"""
        SELECT o.vn, o.hn, o.vstdate, p.nationality, v.age_y,
          (SELECT n.name FROM nationality n WHERE n.nationality=p.nationality) nationality_name
        FROM ovst o JOIN patient p ON p.hn=o.hn LEFT JOIN vn_stat v ON v.vn=o.vn WHERE o.vn={lit(vn)}""") or [None])[0]
    if visit is None:
        return {}
    claims = q(f"""
        SELECT o.vn, o.vstdate, v.age_y, v.age_m, SUM(oo.qty) qty, SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn LEFT JOIN vn_stat v ON v.vn=o.vn
        WHERE o.hn={lit(visit["hn"])} AND oo.icode IN ({lit_list(icodes)})
        GROUP BY o.vn ORDER BY o.vstdate, o.vn""")
    items = q(f"""
        SELECT oo.icode, CONCAT_WS(' ',d.name,d.strength) name, d.nhso_adp_code adp, SUM(oo.qty) qty,
          SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount, EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode) is_drug
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn={lit(vn)}
        GROUP BY oo.icode ORDER BY d.nhso_adp_code IS NULL, oo.icode""")
    dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={lit(vn)} ORDER BY diagtype, icd10")
    return {"visit": visit, "claims": claims, "iron": _iron(q, sorted({text(c["vn"]) for c in claims} | {vn})),
            "items": items, "dx": dx}
