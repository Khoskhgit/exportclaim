"""6) กรณีบริการเบาหวานระยะสงบ (DM Remission) - type 2 diabetes (E11), age 18 and over, UC right; paid by outcome:

   stage 1  4,000 baht  HbA1c < 6.5% twice in a row, at least 3 months apart, after stopping glucose-lowering drugs
                        (or by strict lifestyle change)
   stage 2  2,000 baht  still in remission: one more HbA1c < 6.5% at least 3 months after the second test
   each stage is paid once per patient.

There is no ADP code: สปสช. works the outcome out from the HbA1c results sent to e-Claim (data cut on 15 September
and 31 March). So the rows come from the HbA1c results (lab_items 198) and the ZIP carries the HbA1c visits of each
stage with their LAB file and no charges (CHT 0): the visits themselves are claimed on the 18-file page, so nothing
is billed twice.

"After stopping the drugs" is read as: no glucose-lowering drug dispensed from 3 months before the first of the two
tests until the second (and, for stage 2, until the third). A drug keyed with quantity 0 still counts: in HOSxP it
usually means the patient has the drug at home, so is still on it. A patient who never had one is shown with a warning
(the lifestyle route, or a diagnosis to check). The patient must also be registered in the e-Claim program, which
HOSxP does not hold.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from .nhso18 import lit, lit_list
from .ppfs import ERROR, WARN, Row, Select, _day
from .validate import text

HBA1C_LAB = 198
# The LAB file's LABTEST code of HbA1c (the 43-file lab list HOSxP uses: 03 DTX, 08 HDL, 09 LDL, 16 Hb, 22 Hct, …).
# HOSxP's lab_items.labtest is empty for HbA1c, so the 18-file LAB would leave it out: the rows are made here.
HBA1C_LABTEST = "05"
LIMIT = Decimal("6.5")
GAP_DAYS = 90
DRUG_BEFORE_DAYS = 90
RATES = {1: Decimal("4000"), 2: Decimal("2000")}
DM_DRUGS = ("METFORMIN|GLIPIZIDE|GLICLAZIDE|GLIBENCLAMIDE|GLIMEPIRIDE|INSULIN|PIOGLITAZONE|GLIPTIN|GLIFLOZIN|ACARBOSE|"
            "REPAGLINIDE|GLUTIDE")

VISIT_COLS = """o.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name, seekhipdatas(o.pttype,0) maininscl,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, p.cid, p.nationality, p.sex, v.age_y"""
VISIT_JOINS = """JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype LEFT JOIN vn_stat v ON v.vn=o.vn"""


def value(result) -> Decimal | None:
    match = re.match(r"\s*(\d+(?:\.\d+)?)", str(result or ""))
    return Decimal(match.group(1)) if match else None


def fetch(q: Select, start: date, end: date) -> dict:
    """Every HbA1c and glucose-lowering drug of the patients with an HbA1c in the range."""
    hns = sorted({text(r["hn"]) for r in q(f"""
        SELECT DISTINCT o.hn FROM lab_head lh JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number
        JOIN ovst o ON o.vn=lh.vn
        WHERE lo.lab_items_code={HBA1C_LAB} AND o.vstdate BETWEEN {lit(start)} AND {lit(end)}""")})
    if not hns:
        return {"tests": [], "drugs": [], "e11": set()}
    tests = q(f"""
        SELECT {VISIT_COLS}, lo.lab_order_result result FROM lab_head lh
        JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number JOIN ovst o ON o.vn=lh.vn {VISIT_JOINS}
        WHERE lo.lab_items_code={HBA1C_LAB} AND o.hn IN ({lit_list(hns)}) AND o.vstdate<={lit(end)}
          AND IFNULL(lo.lab_order_result,'')<>''""")
    drugs = q(f"""
        SELECT DISTINCT o.hn, o.vstdate, CONCAT_WS(' ',di.name,di.strength) name FROM ovst o
        JOIN opitemrece oo ON oo.vn=o.vn JOIN drugitems di ON di.icode=oo.icode
        WHERE o.hn IN ({lit_list(hns)}) AND o.vstdate<={lit(end)}
          AND CONCAT_WS(' ',di.name,di.generic_name) REGEXP {lit(DM_DRUGS)}""")
    e11 = {text(r["hn"]) for r in q(f"""
        SELECT DISTINCT o.hn FROM ovst o JOIN ovstdiag x ON x.vn=o.vn
        WHERE o.hn IN ({lit_list(hns)}) AND o.vstdate<={lit(end)} AND x.icd10 REGEXP '^E11'""")}
    return {"tests": tests, "drugs": drugs, "e11": e11}


def stages(tests: list[tuple[date, Decimal, dict]], drug_days: list[date]) -> dict[int, dict]:
    """The first remission of a patient: {1: {...}, 2: {...}} with the tests that make each stage.

    tests: (date, HbA1c, visit row) sorted by date. Stage 1 is the second of two tests in a row below 6.5%, at least
    3 months apart, with no glucose-lowering drug from 3 months before the first; stage 2 is the next test, again below
    6.5%, at least 3 months after, with no drug in between."""
    def no_drug(since: date, until: date) -> bool:
        return not any(since <= d <= until for d in drug_days)

    for i in range(1, len(tests)):
        (d1, a, _), (d2, b, visit) = tests[i - 1], tests[i]
        if a < LIMIT and b < LIMIT and (d2 - d1).days >= GAP_DAYS and no_drug(d1 - timedelta(days=DRUG_BEFORE_DAYS), d2):
            out = {1: {"visit": visit, "tests": [tests[i - 1], tests[i]]}}
            if i + 1 < len(tests):
                d3, c, visit3 = tests[i + 1]
                if c < LIMIT and (d3 - d2).days >= GAP_DAYS and no_drug(d2, d3):
                    out[2] = {"visit": visit3, "tests": [tests[i - 1], tests[i], tests[i + 1]]}
            return out
    return {}


def check(data: dict, start: date, end: date, sent: dict[tuple[str, str], str] | None = None) -> list[Row]:
    sent = sent or {}
    by_hn: dict[str, list[tuple[date, Decimal, dict]]] = {}
    for r in data["tests"]:
        day, result = _day(r["vstdate"]), value(r["result"])
        if day and result is not None:
            by_hn.setdefault(text(r["hn"]), []).append((day, result, r))
    drugs: dict[str, list[date]] = {}
    last_drug: dict[str, tuple[date, str]] = {}
    for r in data["drugs"]:
        if day := _day(r["vstdate"]):
            hn = text(r["hn"])
            drugs.setdefault(hn, []).append(day)
            if hn not in last_drug or day > last_drug[hn][0]:
                last_drug[hn] = (day, text(r["name"]))

    rows: list[Row] = []
    for hn, tests in by_hn.items():
        tests.sort(key=lambda t: (t[0], text(t[2]["vn"])))
        # One result a day is enough (a repeat on the same day is not a new test).
        tests = [t for i, t in enumerate(tests) if i == 0 or t[0] != tests[i - 1][0]]
        for stage, found in stages(tests, drugs.get(hn, [])).items():
            visit = found["visit"]
            day = _day(visit["vstdate"])
            if not (start <= day <= end):
                continue
            vn = text(visit["vn"])
            code = f"DMR{stage}"  # this program's own key: no ADP code is sent
            row = Row(key=f"{vn}|{code}", vn=vn, code=code, item_name=f"ขั้น {stage}: " + (
                "เข้าสู่เบาหวานระยะสงบ" if stage == 1 else "ยังคงเบาหวานระยะสงบ"))
            row.qty, row.amount = Decimal("1"), RATES[stage]
            row.hn, row.date, row.time = hn, day.isoformat(), str(visit.get("vsttime") or "")[:8]
            row.ptname, row.cid = text(visit.get("ptname")), text(visit.get("cid"))
            row.pttype = f"{text(visit.get('pttype'))}: {text(visit.get('pttype_name'))}".strip(": ")
            row.count = stage
            age = visit.get("age_y")
            drug = last_drug.get(hn)
            row.extra = {"stage": stage, "age": int(age) if age is not None else None,
                         "tests": [{"date": d.isoformat(), "value": f"{v}", "vn": text(t["vn"])} for d, v, t in found["tests"]],
                         "all_tests": [{"date": d.isoformat(), "value": f"{v}"} for d, v, _ in tests],
                         "last_drug": drug[0].isoformat() if drug else "", "last_drug_name": drug[1] if drug else "",
                         "maininscl": text(visit.get("maininscl"))}
            if text(visit.get("maininscl")) != "UCS":
                row.add(ERROR, "NOT_UC", f"visit นี้ไม่ใช่สิทธิ UC ({text(visit.get('maininscl')) or 'ไม่ทราบ'})")
            if hn not in data["e11"]:
                row.add(ERROR, "NO_E11", "ไม่พบรหัสโรค E11 (เบาหวานชนิดที่ 2) ใน HOSxP")
            if age is not None and int(age) < 18:
                row.add(ERROR, "AGE", f"อายุ {int(age)} ปี ต้อง 18 ปีขึ้นไป")
            if text(visit.get("an")):
                row.add(ERROR, "IPD", "visit นี้ Admit แล้ว (มี AN)")
            if not drug:
                row.add(WARN, "NEVER_DRUG", "ไม่เคยได้ยาลดระดับน้ำตาลใน HOSxP ให้ยืนยันว่าเป็นกลุ่มปรับพฤติกรรม (ไม่ใช่ลงรหัส E11 ผิด)")
            row.sent = sent.get((vn, code), "")
            rows.append(row)
    return sorted(rows, key=lambda x: (x.date, x.time, x.vn, x.code))


def lab_rows(q: Select, vns: list[str], hcode: str) -> list[dict]:
    """The LAB rows of the HbA1c results at these visits (LABTEST 05)."""
    if not vns:
        return []
    out = []
    for r in q(f"""
            SELECT lh.vn, o.hn, o.vstdate, p.cid, lo.lab_order_result result FROM lab_head lh
            JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number JOIN ovst o ON o.vn=lh.vn JOIN patient p ON p.hn=o.hn
            WHERE lh.vn IN ({lit_list(vns)}) AND lo.lab_items_code={HBA1C_LAB} AND IFNULL(lo.lab_order_result,'')<>''"""):
        result = value(r["result"])
        if result is not None and result > 0:
            out.append({"HCODE": hcode, "HN": text(r["hn"]), "PERSON_ID": text(r["cid"]), "DATESERV": _day(r["vstdate"]),
                        "SEQ": text(r["vn"]), "LABTEST": HBA1C_LABTEST, "LABRESULT": result.quantize(Decimal("0.01"))})
    return out


def stage_visits(picked: list[Row]) -> list[str]:
    """The HbA1c visits that make up the picked stages, in date order: they go into the ZIP with their LAB rows."""
    seen: dict[str, str] = {}
    for r in picked:
        for t in r.extra.get("tests", []):
            seen.setdefault(t["vn"], t["date"])
    return sorted(seen, key=lambda vn: (seen[vn], vn))


def detail(q: Select, vn: str) -> dict:
    visit = (q(f"""
        SELECT o.vn, o.hn, o.vstdate, p.nationality, v.age_y,
          (SELECT n.name FROM nationality n WHERE n.nationality=p.nationality) nationality_name
        FROM ovst o JOIN patient p ON p.hn=o.hn LEFT JOIN vn_stat v ON v.vn=o.vn WHERE o.vn={lit(vn)}""") or [None])[0]
    if visit is None:
        return {}
    hn = text(visit["hn"])
    tests = q(f"""
        SELECT o.vn, o.vstdate, lo.lab_order_result result FROM lab_head lh JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number
        JOIN ovst o ON o.vn=lh.vn WHERE lo.lab_items_code={HBA1C_LAB} AND o.hn={lit(hn)} AND IFNULL(lo.lab_order_result,'')<>''
        ORDER BY o.vstdate""")
    drugs = q(f"""
        SELECT o.vstdate, CONCAT_WS(' ',di.name,di.strength) name, SUM(oo.qty) qty FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn
        JOIN drugitems di ON di.icode=oo.icode
        WHERE o.hn={lit(hn)} AND CONCAT_WS(' ',di.name,di.generic_name) REGEXP {lit(DM_DRUGS)}
        GROUP BY o.vstdate, oo.icode ORDER BY o.vstdate DESC LIMIT 30""")
    items = q(f"""
        SELECT oo.icode, CONCAT_WS(' ',d.name,d.strength) name, d.nhso_adp_code adp, SUM(oo.qty) qty,
          SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount, EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode) is_drug
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn={lit(vn)}
        GROUP BY oo.icode ORDER BY d.nhso_adp_code IS NULL, oo.icode""")
    dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={lit(vn)} ORDER BY diagtype, icd10")
    return {"visit": visit, "tests": tests, "drugs": drugs, "items": items, "dx": dx}
