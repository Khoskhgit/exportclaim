"""PPFS 9) บริการวางแผนครอบครัวและการป้องกันการตั้งครรภ์ไม่พึงประสงค์ - Thai women of reproductive age, all rights.

   FP003_1  ยาเม็ดคุมกำเนิดฮอร์โมนรวม     40 baht a pack   } at most 3 packs a visit and 13 packs a year (both kinds)
   FP003_2  ยาเม็ดคุมกำเนิดฮอร์โมนเดี่ยว   80 baht a pack   } single-hormone: breast-feeding (birth within 1 1/2 years)
                                                          or migraine with aura (G43.1)
   FP003_4  ยาฉีดคุมกำเนิด                60 baht, at most 5 a year
   FP001    ใส่ห่วงอนามัย                 800 baht, once a year
   FP002_1  ฝังยาคุมกำเนิด                2,150 baht
   FP002_2  ถอดยาฝังคุมกำเนิด             350 baht
   FP003_3  ยาเม็ดคุมกำเนิดฉุกเฉิน         50 baht a pack, at most 2 packs a year
   (condoms have no item in HOSxP yet, so they are not here)

The year is the calendar year, as for the pregnancy test. In HOSxP the drug (e.g. DMPA) and the service fee
("18ฉีดยาคุมกำเนิด") carry the same ADP code: only the fee, a non-drug item, is claimed; the drug is shown beside it.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from .nhso18 import lit, lit_list
from .ppfs import ERROR, SELF_PAID, WARN, Row, Select, _day, _money, self_paid_error
from .validate import text

PILL_C, PILL_S, EC, INJECT, IUD, IMPLANT, REMOVE = "FP003_1", "FP003_2", "FP003_3", "FP003_4", "FP001", "FP002_1", "FP002_2"
CODES = {
    PILL_C: ("ยาเม็ดคุมฮอร์โมนรวม", Decimal("40"), "แผง"),
    PILL_S: ("ยาเม็ดคุมฮอร์โมนเดี่ยว", Decimal("80"), "แผง"),
    INJECT: ("ยาฉีดคุมกำเนิด", Decimal("60"), "ครั้ง"),
    IUD: ("ใส่ห่วงอนามัย", Decimal("800"), "ครั้ง"),
    IMPLANT: ("ฝังยาคุมกำเนิด", Decimal("2150"), "ครั้ง"),
    REMOVE: ("ถอดยาฝังคุมกำเนิด", Decimal("350"), "ครั้ง"),
    EC: ("ยาคุมฉุกเฉิน", Decimal("50"), "แผง"),
}
PILLS = (PILL_C, PILL_S)
PER_VISIT = {PILL_C: 3, PILL_S: 3}
# Yearly caps: a group of codes shares one cap; counted in packs for pills, in visits for the rest.
YEAR_CAPS = [((PILL_C, PILL_S), 13, "แผง"), ((INJECT,), 5, "ครั้ง"), ((IUD,), 1, "ครั้ง"), ((EC,), 2, "แผง")]
BREASTFEEDING_DAYS = 548  # 1 year 6 months
AGE = (10, 60)

VISIT_COLS = """o.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, p.cid, p.nationality, p.sex, v.age_y"""
VISIT_JOINS = """JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype LEFT JOIN vn_stat v ON v.vn=o.vn"""
ITEM_COLS = """d.nhso_adp_code code, oo.icode, oo.paidst, d.name item_name, oo.qty, IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0) amount,
          EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode) is_drug"""


def _cap(code: str):
    return next((c for c in YEAR_CAPS if code in c[0]), None)


def _counts(row_qty: Decimal, code: str) -> Decimal:
    """What one visit adds to its yearly cap: packs for pills and emergency pills, one for the other services."""
    return row_qty if CODES[code][2] == "แผง" else Decimal("1")


def fetch(q: Select, start: date, end: date) -> dict:
    codes = lit_list(CODES)
    items = q(f"""
        SELECT {VISIT_COLS}, {ITEM_COLS}
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode {VISIT_JOINS}
        WHERE o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND d.nhso_adp_code IN ({codes})""")
    hns = sorted({text(r["hn"]) for r in items})
    history = q(f"""
        SELECT oo.vn, o.hn, o.vstdate, d.nhso_adp_code code, SUM(oo.qty) qty
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode
        WHERE o.hn IN ({lit_list(hns)}) AND o.vstdate BETWEEN {lit(date(start.year, 1, 1))} AND {lit(date(end.year, 12, 31))}
          AND d.nhso_adp_code IN ({codes}) AND NOT EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode)
        GROUP BY oo.vn, d.nhso_adp_code HAVING SUM(oo.qty)>0""") if hns else []
    births = q(f"""
        SELECT p.patient_hn hn, a.labor_date FROM person p JOIN person_anc a ON a.person_id=p.person_id
        WHERE p.patient_hn IN ({lit_list(hns)}) AND a.labor_date IS NOT NULL""") if hns else []
    migraine = {text(r["hn"]) for r in q(f"""
        SELECT DISTINCT o.hn FROM ovst o JOIN ovstdiag x ON x.vn=o.vn
        WHERE o.hn IN ({lit_list(hns)}) AND x.icd10 REGEXP '^G431'""")} if hns else set()
    implants = q(f"""
        SELECT o.hn, MAX(o.vstdate) last FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode
        WHERE o.hn IN ({lit_list(hns)}) AND d.nhso_adp_code IN ({lit_list([IMPLANT, 'FP002'])}) AND o.vstdate<={lit(end)}
        GROUP BY o.hn""") if hns else []
    return {"items": items, "history": history, "births": births, "migraine": migraine,
            "implants": {text(r["hn"]): _day(r["last"]) for r in implants}}


def check(data: dict, sent: dict[tuple[str, str], str] | None = None) -> list[Row]:
    sent = sent or {}
    rows: dict[tuple[str, str], Row] = {}
    info: dict[str, dict] = {}
    drugs: dict[tuple[str, str], list] = {}
    for r in data["items"]:
        vn, code = text(r["vn"]), text(r["code"])
        info.setdefault(vn, r)
        row = rows.get((vn, code))
        if row is None:
            row = rows[(vn, code)] = Row(key=f"{vn}|{code}", vn=vn, code=code)
        if r.get("is_drug"):
            entry = drugs.setdefault((vn, code), [Decimal("0"), text(r.get("item_name"))])
            entry[0] += Decimal(str(r.get("qty") or 0))
        else:
            row.item_name = text(r.get("item_name"))
            row.qty += Decimal(str(r.get("qty") or 0))
            row.amount += _money(r.get("amount"))
            row.self_paid |= text(r.get("paidst")) == SELF_PAID

    # Running totals of each yearly cap, from every fee billed in HOSxP that calendar year.
    running: dict[tuple[str, str], Decimal] = {}
    done: dict[tuple[str, int, tuple], list[tuple[date, str, str, Decimal]]] = {}
    for h in data["history"]:
        day, code = _day(h["vstdate"]), text(h["code"])
        cap = _cap(code)
        if day and cap:
            done.setdefault((text(h["hn"]), day.year, cap[0]), []).append((day, text(h["vn"]), code, Decimal(str(h["qty"] or 0))))
    for (hn, _, _), entries in done.items():
        total = Decimal("0")
        for day, vn, code, qty in sorted(entries):
            total += _counts(qty, code)
            running[(vn, code)] = total
    births: dict[str, list[date]] = {}
    for b in data["births"]:
        if d := _day(b["labor_date"]):
            births.setdefault(text(b["hn"]), []).append(d)

    for (vn, code), row in rows.items():
        r = info[vn]
        day = _day(r.get("vstdate"))
        name, rate, unit = CODES[code]
        row.hn, row.date, row.time = text(r.get("hn")), day.isoformat() if day else "", str(r.get("vsttime") or "")[:8]
        row.ptname, row.cid = text(r.get("ptname")), text(r.get("cid"))
        row.pttype = f"{text(r.get('pttype'))}: {text(r.get('pttype_name'))}".strip(": ")
        age = r.get("age_y")
        drug_qty, drug_name = drugs.get((vn, code), [Decimal("0"), ""])
        cap = _cap(code)
        total = running.get((vn, code))
        row.extra = {"age": int(age) if age is not None else None, "unit": unit, "fee_qty": f"{row.qty:g}",
                     "drug_qty": f"{drug_qty:g}" if drug_qty else "", "drug_name": drug_name, "year": day.year if day else None,
                     "year_total": f"{total:g}" if total is not None else "", "cap": cap[1] if cap else None,
                     "cap_unit": cap[2] if cap else ""}
        row.count = int(total) if total is not None else None
        billed = row.qty > 0

        if text(r.get("sex")) != "2":
            row.add(ERROR, "NOT_FEMALE", "ไม่ใช่เพศหญิง")
        if text(r.get("nationality")) != "99":
            row.add(ERROR, "NOT_THAI", "ไม่ใช่คนไทย (สัญชาติไม่ใช่ 99)")
        if text(r.get("an")):
            row.add(ERROR, "IPD", "visit นี้ Admit แล้ว (มี AN)")
        if not billed:
            row.add(ERROR, "NO_ITEM", f"จ่าย {drug_name or 'ยา'} แต่ยังไม่คีย์ค่าบริการ {code} ใน HOSxP")
        self_paid_error(row)
        if billed and code in PER_VISIT and row.qty > PER_VISIT[code]:
            row.add(ERROR, "PER_VISIT", f"{row.qty:g} แผงต่อครั้ง เกิน {PER_VISIT[code]} แผง")
        if billed and cap and total is not None and total > cap[1]:
            row.add(ERROR, "OVER_YEAR", f"สะสมปี {day.year + 543} เป็น {total:g} {cap[2]} เกิน {cap[1]} {cap[2]}")
        if age is not None and not AGE[0] <= int(age) <= AGE[1]:
            row.add(WARN, "AGE", f"อายุ {int(age)} ปี อยู่นอกวัยเจริญพันธุ์ ({AGE[0]}–{AGE[1]} ปี)")
        if billed and code == PILL_S:
            feeding = day and any(timedelta(0) <= day - b <= timedelta(BREASTFEEDING_DAYS) for b in births.get(row.hn, []))
            if not feeding and row.hn not in data["migraine"]:
                row.add(WARN, "SINGLE_HORMONE", "ยาเม็ดฮอร์โมนเดี่ยว: ไม่พบการคลอดภายใน 1 ปี 6 เดือน (ให้นมบุตร) หรือไมเกรนมีออร่า (G43.1)")
        if billed and drug_qty and drug_qty != row.qty and code in (*PILLS, EC, INJECT):
            row.add(WARN, "DRUG_QTY", f"ค่าบริการ {row.qty:g} {unit} แต่จ่ายยา {drug_qty:g} หน่วย")
        if billed and code == REMOVE:
            last = data["implants"].get(row.hn)
            if not last or (day and last > day):
                row.add(WARN, "NO_IMPLANT", "ไม่พบประวัติฝังยาคุมใน HOSxP (อาจฝังที่อื่น)")
        if billed and (row.amount != rate * row.qty or (unit == "ครั้ง" and row.qty != 1)):
            row.add(WARN, "PRICE", f"ยอด {row.amount} บาท (จำนวน {row.qty:g}) ไม่ตรงอัตรา {rate} บาท/{unit}")
        row.sent = sent.get((vn, code), "")
    return sorted(rows.values(), key=lambda x: (x.date, x.time, x.vn, x.code))


def detail(q: Select, vn: str) -> dict:
    """The drawer of one visit: every family planning fee and drug of the patient in that calendar year."""
    visit = (q(f"""
        SELECT o.vn, o.hn, o.vstdate, p.nationality, v.age_y,
          (SELECT n.name FROM nationality n WHERE n.nationality=p.nationality) nationality_name
        FROM ovst o JOIN patient p ON p.hn=o.hn LEFT JOIN vn_stat v ON v.vn=o.vn WHERE o.vn={lit(vn)}""") or [None])[0]
    if visit is None:
        return {}
    year = _day(visit["vstdate"]).year
    history = q(f"""
        SELECT o.vn, o.vstdate, d.nhso_adp_code code,
          SUM(IF(EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode),0,oo.qty)) fee_qty,
          SUM(IF(EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode),oo.qty,0)) drug_qty,
          SUM(IF(EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode),0,IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0))) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode
        WHERE o.hn={lit(visit["hn"])} AND YEAR(o.vstdate)={year} AND d.nhso_adp_code IN ({lit_list(CODES)})
        GROUP BY o.vn, d.nhso_adp_code ORDER BY o.vstdate, o.vn, d.nhso_adp_code""")
    items = q(f"""
        SELECT oo.icode, CONCAT_WS(' ',d.name,d.strength) name, d.nhso_adp_code adp, SUM(oo.qty) qty,
          SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount, EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode) is_drug
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn={lit(vn)}
        GROUP BY oo.icode ORDER BY d.nhso_adp_code IS NULL, oo.icode""")
    dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={lit(vn)} ORDER BY diagtype, icd10")
    return {"visit": visit, "year": year, "history": history, "items": items, "dx": dx}
