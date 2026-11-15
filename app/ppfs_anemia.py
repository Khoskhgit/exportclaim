"""PPFS 17) บริการคัดกรองโลหิตจางจากการขาดธาตุเหล็ก - Thai women aged 13-24, all rights.

ADP 13001, a CBC, 65 baht, once per person: counted over every 13001 ever billed for the patient in HOSxP, as the
user agreed ("1 ครั้งต่อคน" is for life, not once a year). The labs are this hospital's lab_items 3 Hb and 4 Hct.
(The child Hb/Hct items under ADP 10000 are another service and are not read here.)
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .nhso18 import lit, lit_list
from .ppfs import ERROR, SELF_PAID, WARN, Row, Select, _day, _money, self_paid_error
from .validate import text

CODE = "13001"
RATE = Decimal("65")
AGE = (13, 24)
LABS = {3: "Hb", 4: "Hct"}

VISIT_COLS = """o.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, p.cid, p.nationality, p.sex, v.age_y"""
VISIT_JOINS = """JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype LEFT JOIN vn_stat v ON v.vn=o.vn"""
FEE = "NOT EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode)"


def _labs(q: Select, vns: list[str]) -> dict[str, dict[str, str]]:
    if not vns:
        return {}
    out: dict[str, dict[str, str]] = {}
    for r in q(f"""
            SELECT lh.vn, lo.lab_items_code code, lo.lab_order_result result FROM lab_head lh
            JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number
            WHERE lh.vn IN ({lit_list(vns)}) AND lo.lab_items_code IN ({lit_list(LABS)})"""):
        name = LABS.get(int(r["code"])) if text(r["code"]).isdigit() else None
        if name:
            out.setdefault(text(r["vn"]), {}).setdefault(name, text(r["result"]))
    return out


def fetch(q: Select, start: date, end: date) -> dict:
    items = q(f"""
        SELECT {VISIT_COLS}, oo.icode, oo.paidst, d.name item_name, oo.qty, IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode {VISIT_JOINS}
        WHERE o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND d.nhso_adp_code={lit(CODE)} AND {FEE}""")
    hns = sorted({text(r["hn"]) for r in items})
    history = q(f"""
        SELECT oo.vn, o.hn, o.vstdate FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode
        WHERE o.hn IN ({lit_list(hns)}) AND d.nhso_adp_code={lit(CODE)} AND {FEE} AND IFNULL(oo.qty,0)>0
        GROUP BY oo.vn""") if hns else []
    return {"items": items, "history": history, "labs": _labs(q, sorted({text(r["vn"]) for r in items}))}


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

    claims: dict[str, list[tuple[date, str]]] = {}
    for h in data["history"]:
        if day := _day(h["vstdate"]):
            claims.setdefault(text(h["hn"]), []).append((day, text(h["vn"])))
    for v in claims.values():
        v.sort()

    for vn, row in rows.items():
        r = info[vn]
        day = _day(r.get("vstdate"))
        row.hn, row.date, row.time = text(r.get("hn")), day.isoformat() if day else "", str(r.get("vsttime") or "")[:8]
        row.ptname, row.cid = text(r.get("ptname")), text(r.get("cid"))
        row.pttype = f"{text(r.get('pttype'))}: {text(r.get('pttype_name'))}".strip(": ")
        age = r.get("age_y")
        labs = data["labs"].get(vn, {})
        earlier = [(d, v) for d, v in claims.get(row.hn, []) if day and (d, v) < (day, vn)]
        first = earlier[0] if earlier else None
        row.extra = {"age": int(age) if age is not None else None, "labs": {k: labs.get(k, "") for k in LABS.values()},
                     "first": first[0].isoformat() if first else "", "first_vn": first[1] if first else ""}
        billed = row.qty > 0
        row.count = len(earlier) + 1 if billed else None

        if text(r.get("sex")) != "2":
            row.add(ERROR, "NOT_FEMALE", "ไม่ใช่เพศหญิง")
        if text(r.get("nationality")) != "99":
            row.add(ERROR, "NOT_THAI", "ไม่ใช่คนไทย (สัญชาติไม่ใช่ 99)")
        if text(r.get("an")):
            row.add(ERROR, "IPD", "visit นี้ Admit แล้ว (มี AN)")
        self_paid_error(row)
        if age is not None and not AGE[0] <= int(age) <= AGE[1]:
            row.add(ERROR, "AGE", f"อายุ {int(age)} ปี เบิกได้เฉพาะอายุ {AGE[0]}–{AGE[1]} ปี")
        if billed and first:
            row.add(ERROR, "DONE_BEFORE", f"เคยเบิกคัดกรองไปแล้วเมื่อ {_thai(first[0])} (VN {first[1]}) เบิกได้คนละ 1 ครั้ง")
        missing = [k for k in LABS.values() if not labs.get(k)]
        if billed and missing:
            row.add(WARN, "NO_LAB", f"ไม่พบผล lab {' / '.join(missing)} ใน visit นี้")
        if billed and (row.qty != 1 or row.amount != RATE):
            row.add(WARN, "PRICE", f"ยอด {row.amount} บาท (จำนวน {row.qty:g}) ไม่ตรงอัตรา {RATE} บาท/คน")
        row.sent = sent.get((vn, CODE), "")
    return sorted(rows.values(), key=lambda x: (x.date, x.time, x.vn))


def _thai(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}/{day.year + 543}"


def detail(q: Select, vn: str) -> dict:
    """The drawer of one visit: every 13001 screening of the patient with its Hb / Hct."""
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
          SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn={lit(vn)}
        GROUP BY oo.icode ORDER BY d.nhso_adp_code IS NULL, oo.icode""")
    dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={lit(vn)} ORDER BY diagtype, icd10")
    return {"visit": visit, "claims": claims, "labs": _labs(q, sorted({text(c["vn"]) for c in claims} | {vn})),
            "items": items, "dx": dx}
