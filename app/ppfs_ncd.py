"""PPFS 15) บริการคัดกรองเบาหวานและไขมันในเลือด - all rights.

   12003  FPG (fasting plasma glucose), 40 baht, age 35-59, once a fiscal year,
          not for anyone treated for diabetes before (E10-E14 at an earlier visit)
   12004  Total cholesterol and HDL, 160 baht, age 45-70, once in 5 years,
          not for anyone treated for dyslipidaemia before (E78 at an earlier visit, or a lipid-lowering drug)

The year is the fiscal year (October-September), as the user chose for every PPFS limit. Found the disease on the
screening day itself is fine (the screening found it): that is only a warning. The diabetes risk factors in the
announcement's note (BMI, blood pressure, family history, …) are not checked: HOSxP has no single place for them.
The labs are this hospital's lab_items: 76 Glucose serum/plasma (FPG), 102 Cholesterol, 91 HDL.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from .nhso18 import lit, lit_list
from .ppfs import ERROR, SELF_PAID, WARN, Row, Select, _day, _money, fiscal_range, fiscal_year, self_paid_error
from .validate import text

FPG, LIPID = "12003", "12004"
CODES = {FPG: ("คัดกรองเบาหวาน FPG", Decimal("40")), LIPID: ("คัดกรองไขมัน TC + HDL", Decimal("160"))}
AGES = {FPG: (35, 59), LIPID: (45, 70)}
LIPID_YEARS = 5
LABS = {FPG: {76: "FPG"}, LIPID: {102: "TC", 91: "HDL"}}
DM = "^E1[0-4]"
LIPID_DX = "^E78"
LIPID_DRUGS = "STATIN|GEMFIBROZIL|FENOFIBRATE|EZETIMIBE"

VISIT_COLS = """o.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, p.cid, p.nationality, p.sex, v.age_y"""
VISIT_JOINS = """JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype LEFT JOIN vn_stat v ON v.vn=o.vn"""
FEE = "NOT EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode)"


def _labs(q: Select, vns: list[str]) -> dict[str, dict[str, str]]:
    """VN -> {"FPG": result, "TC": result, "HDL": result} for the screening labs done at the visit."""
    codes = {c: name for group in LABS.values() for c, name in group.items()}
    if not vns:
        return {}
    out: dict[str, dict[str, str]] = {}
    for r in q(f"""
            SELECT lh.vn, lo.lab_items_code code, lo.lab_order_result result FROM lab_head lh
            JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number
            WHERE lh.vn IN ({lit_list(vns)}) AND lo.lab_items_code IN ({lit_list(codes)})"""):
        name = codes.get(int(r["code"])) if text(r["code"]).isdigit() else None
        if name:
            out.setdefault(text(r["vn"]), {}).setdefault(name, text(r["result"]))
    return out


def _first(q: Select, hns: list[str], sql_where: str) -> dict[str, date]:
    if not hns:
        return {}
    return {text(r["hn"]): _day(r["first"]) for r in q(f"""
        SELECT o.hn, MIN(o.vstdate) first FROM ovst o {sql_where.format(hns=lit_list(hns))} GROUP BY o.hn""")}


def histories(q: Select, hns: list[str]) -> dict[str, dict[str, date]]:
    """The first time each patient was treated: diabetes, dyslipidaemia diagnosis, lipid-lowering drug."""
    return {
        "dm": _first(q, hns, f"JOIN ovstdiag x ON x.vn=o.vn WHERE o.hn IN ({{hns}}) AND x.icd10 REGEXP {lit(DM)}"),
        "lipid": _first(q, hns, f"JOIN ovstdiag x ON x.vn=o.vn WHERE o.hn IN ({{hns}}) AND x.icd10 REGEXP {lit(LIPID_DX)}"),
        "statin": _first(q, hns, f"""JOIN opitemrece x ON x.vn=o.vn JOIN drugitems di ON di.icode=x.icode
            WHERE o.hn IN ({{hns}}) AND CONCAT_WS(' ',di.name,di.generic_name) REGEXP {lit(LIPID_DRUGS)}"""),
    }


def fetch(q: Select, start: date, end: date) -> dict:
    items = q(f"""
        SELECT {VISIT_COLS}, d.nhso_adp_code code, oo.icode, oo.paidst, d.name item_name, oo.qty,
          IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode {VISIT_JOINS}
        WHERE o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND d.nhso_adp_code IN ({lit_list(CODES)}) AND {FEE}""")
    hns = sorted({text(r["hn"]) for r in items})
    since = min(fiscal_range(fiscal_year(start))[0], start - timedelta(days=366 * LIPID_YEARS))
    history = q(f"""
        SELECT oo.vn, o.hn, o.vstdate, d.nhso_adp_code code FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn
        JOIN s_drugitems d ON d.icode=oo.icode
        WHERE o.hn IN ({lit_list(hns)}) AND o.vstdate BETWEEN {lit(since)} AND {lit(fiscal_range(fiscal_year(end))[1])}
          AND d.nhso_adp_code IN ({lit_list(CODES)}) AND {FEE} AND IFNULL(oo.qty,0)>0
        GROUP BY oo.vn, d.nhso_adp_code""") if hns else []
    return {"items": items, "history": history, "labs": _labs(q, sorted({text(r["vn"]) for r in items})),
            **histories(q, hns)}


def _since(first: date | None, day: date | None) -> str:
    """'before' when treated before the screening day, 'today' when found on it, '' otherwise."""
    if not first or not day:
        return ""
    return "before" if first < day else "today" if first == day else ""


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

    claims: dict[tuple[str, str], list[tuple[date, str]]] = {}
    for h in data["history"]:
        if day := _day(h["vstdate"]):
            claims.setdefault((text(h["hn"]), text(h["code"])), []).append((day, text(h["vn"])))
    for v in claims.values():
        v.sort()

    for (vn, code), row in rows.items():
        r = info[vn]
        day = _day(r.get("vstdate"))
        name, rate = CODES[code]
        row.hn, row.date, row.time = text(r.get("hn")), day.isoformat() if day else "", str(r.get("vsttime") or "")[:8]
        row.ptname, row.cid = text(r.get("ptname")), text(r.get("cid"))
        row.pttype = f"{text(r.get('pttype'))}: {text(r.get('pttype_name'))}".strip(": ")
        age = r.get("age_y")
        labs = data["labs"].get(vn, {})
        wanted = list(LABS[code].values())
        dm = _since(data["dm"].get(row.hn), day)
        lipid = _since(data["lipid"].get(row.hn), day)
        statin = _since(data["statin"].get(row.hn), day)
        done = claims.get((row.hn, code), [])
        earlier = [(d, v) for d, v in done if (d, v) < (day, vn)] if day else []
        last = earlier[-1] if earlier else None
        history = {
            "dm": data["dm"].get(row.hn).isoformat() if data["dm"].get(row.hn) else "",
            "lipid": data["lipid"].get(row.hn).isoformat() if data["lipid"].get(row.hn) else "",
            "statin": data["statin"].get(row.hn).isoformat() if data["statin"].get(row.hn) else "",
        }
        row.extra = {"age": int(age) if age is not None else None, "labs": {k: labs.get(k, "") for k in wanted},
                     "history": history, "last": last[0].isoformat() if last else "", "last_vn": last[1] if last else "",
                     "year": fiscal_year(day) if day else None}
        billed = row.qty > 0

        if text(r.get("an")):
            row.add(ERROR, "IPD", "visit นี้ Admit แล้ว (มี AN)")
        self_paid_error(row)
        low, high = AGES[code]
        if age is not None and not low <= int(age) <= high:
            row.add(ERROR, "AGE", f"อายุ {int(age)} ปี {name} เบิกได้เฉพาะอายุ {low}–{high} ปี")
        if code == FPG:
            if dm == "before":
                row.add(ERROR, "DM_HISTORY", f"มีประวัติรักษาเบาหวาน (E10–E14) ตั้งแต่ {_thai(data['dm'][row.hn])}")
            if billed and day:
                same_year = [v for d, v in earlier if fiscal_year(d) == fiscal_year(day)]
                if same_year:
                    row.add(ERROR, "OVER_YEAR", f"ปีงบ {fiscal_year(day) + 543} เบิกคัดกรองเบาหวานไปแล้วที่ VN {same_year[-1]} (ปีละ 1 ครั้ง)")
            if dm == "today":
                row.add(WARN, "NEW_DM", "visit นี้วินิจฉัยเบาหวานครั้งแรก (คัดกรองแล้วพบโรค)")
        else:
            if lipid == "before" or statin == "before":
                since = min(d for d in (data["lipid"].get(row.hn), data["statin"].get(row.hn)) if d and d < day)
                row.add(ERROR, "LIPID_HISTORY", f"มีประวัติรักษาไขมันในเลือดสูง (E78 หรือยาลดไขมัน) ตั้งแต่ {_thai(since)}")
            if billed and last and day - last[0] < timedelta(days=round(365.25 * LIPID_YEARS)):
                row.add(ERROR, "WITHIN_5Y", f"เบิกคัดกรองไขมันไปแล้วเมื่อ {_thai(last[0])} (VN {last[1]}) ยังไม่ครบ {LIPID_YEARS} ปี")
            if lipid == "today" and statin != "before":
                row.add(WARN, "NEW_LIPID", "visit นี้วินิจฉัยไขมันในเลือดสูงครั้งแรก (คัดกรองแล้วพบโรค)")
        missing = [k for k in wanted if not labs.get(k)]
        if billed and missing:
            row.add(WARN, "NO_LAB", f"ไม่พบผล lab {' / '.join(missing)} ใน visit นี้")
        if billed and (row.qty != 1 or row.amount != rate):
            row.add(WARN, "PRICE", f"ยอด {row.amount} บาท (จำนวน {row.qty:g}) ไม่ตรงอัตรา {rate} บาท/ครั้ง")
        row.count = len(earlier) + 1 if billed else None
        row.sent = sent.get((vn, code), "")
    return sorted(rows.values(), key=lambda x: (x.date, x.time, x.vn, x.code))


def _thai(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}/{day.year + 543}"


def detail(q: Select, vn: str) -> dict:
    """The drawer of one visit: every 12003 / 12004 claim of the patient with its labs, and when treatment began."""
    visit = (q(f"""
        SELECT o.vn, o.hn, o.vstdate, p.nationality, v.age_y,
          (SELECT n.name FROM nationality n WHERE n.nationality=p.nationality) nationality_name
        FROM ovst o JOIN patient p ON p.hn=o.hn LEFT JOIN vn_stat v ON v.vn=o.vn WHERE o.vn={lit(vn)}""") or [None])[0]
    if visit is None:
        return {}
    hn = text(visit["hn"])
    claims = q(f"""
        SELECT o.vn, o.vstdate, d.nhso_adp_code code, SUM(oo.qty) qty, SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode
        WHERE o.hn={lit(hn)} AND d.nhso_adp_code IN ({lit_list(CODES)}) AND {FEE}
        GROUP BY o.vn, d.nhso_adp_code ORDER BY o.vstdate, o.vn, d.nhso_adp_code""")
    labs = _labs(q, sorted({text(c["vn"]) for c in claims} | {vn}))
    drug = (q(f"""
        SELECT o.vstdate, CONCAT_WS(' ',di.name,di.strength) name FROM ovst o JOIN opitemrece x ON x.vn=o.vn
        JOIN drugitems di ON di.icode=x.icode
        WHERE o.hn={lit(hn)} AND CONCAT_WS(' ',di.name,di.generic_name) REGEXP {lit(LIPID_DRUGS)}
        ORDER BY o.vstdate LIMIT 1""") or [None])[0]
    items = q(f"""
        SELECT oo.icode, CONCAT_WS(' ',d.name,d.strength) name, d.nhso_adp_code adp, SUM(oo.qty) qty,
          SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn={lit(vn)}
        GROUP BY oo.icode ORDER BY d.nhso_adp_code IS NULL, oo.icode""")
    dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={lit(vn)} ORDER BY diagtype, icd10")
    first = histories(q, [hn])
    return {"visit": visit, "claims": claims, "labs": labs, "items": items, "dx": dx,
            "first": {k: v.get(hn) for k, v in first.items()}, "first_drug": drug}
