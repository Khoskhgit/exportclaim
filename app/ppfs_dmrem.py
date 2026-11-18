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
tests until the second (and, for stage 2, until the third). The registry imported from e-Claim (dmrem_registry) is
checked: no registration in force is an error, a registration made after the stage's test a warning. A drug keyed with quantity 0 does not count: the user
reads it as the drug being stopped (2026-11-17). A patient who never had one is shown with a warning
(the lifestyle route, or a diagnosis to check). The patient must also be registered in the e-Claim program, which
HOSxP does not hold.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from .nhso18 import lit, lit_list
from .ppfs import ERROR, WARN, Row, Select, _day, fiscal_range, fiscal_year
from .validate import text

HBA1C_LAB = 198
# The LAB file's LABTEST code of HbA1c (the 43-file lab list HOSxP uses: 03 DTX, 08 HDL, 09 LDL, 16 Hb, 22 Hct, …).
# HOSxP's lab_items.labtest is empty for HbA1c, so the 18-file LAB would leave it out: the rows are made here.
HBA1C_LABTEST = "05"
LIMIT = Decimal("6.5")
GAP_DAYS = 90
# The remission counted is the one of the registration's fiscal year: tests from 180 days before that year began (the
# first test may come before). With no registry, the year before the searched range.
BEFORE_REGISTRATION_DAYS = 180


def window_start(reg_date: str) -> date:
    return fiscal_range(fiscal_year(date.fromisoformat(reg_date)))[0] - timedelta(days=BEFORE_REGISTRATION_DAYS)


def stop_day(registrations: list[dict], until: date | None = None) -> date | None:
    """The first day off the drugs from the e-Claim registry: the วันที่ลงทะเบียน of the latest Y registration
    that began on or before `until` (a patient registered again starts over from the new one).

    With it the registration is the first test (stages_registered): the drugs dispensed before it are ignored, a drug
    dispensed after it still breaks the remission. Without it the HbA1c and drugs in HOSxP decide (stages)."""
    days = [date.fromisoformat(r["reg_date"]) for r in registrations if r.get("status") == "Y" and r.get("reg_date")]
    return max((x for x in days if until is None or x <= until), default=None)
DRUG_BEFORE_DAYS = 90
RATES = {0: Decimal("0"), 1: Decimal("4000"), 2: Decimal("2000")}
DM_DRUGS = ("METFORMIN|GLIPIZIDE|GLICLAZIDE|GLIBENCLAMIDE|GLIMEPIRIDE|INSULIN|PIOGLITAZONE|GLIPTIN|GLIFLOZIN|ACARBOSE|"
            "REPAGLINIDE|GLUTIDE")

VISIT_COLS = """o.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name, seekhipdatas(o.pttype,0) maininscl,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, p.cid, p.nationality, p.sex, v.age_y"""
VISIT_JOINS = """JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype LEFT JOIN vn_stat v ON v.vn=o.vn"""


def value(result) -> Decimal | None:
    match = re.match(r"\s*(\d+(?:\.\d+)?)", str(result or ""))
    return Decimal(match.group(1)) if match else None


def drug_icodes(q: Select) -> dict[str, str]:
    """icode -> name of the glucose-lowering drugs (a few dozen rows of drugitems)."""
    return {text(r["icode"]): text(r["name"]) for r in q(f"""
        SELECT icode, CONCAT_WS(' ',name,strength) name FROM drugitems
        WHERE CONCAT_WS(' ',name,generic_name) REGEXP {lit(DM_DRUGS)}""")}


def drug_days(q: Select, icodes: dict[str, str], since: date, end: date, hns: set[str] | None = None) -> list[dict]:
    """OPD dispensing of those drugs between the days: one row per patient, day and drug.

    opitemrece is read through its icode (or hn) index, not joined from every visit of the patients: the old join
    read the whole history of thousands of patients and took about a minute."""
    if not icodes:
        return []
    by_hn = f"AND hn IN ({lit_list(sorted(hns))})" if hns is not None and len(hns) <= 500 else ""
    rows = q(f"""
        SELECT DISTINCT hn, vstdate, icode FROM opitemrece
        WHERE icode IN ({lit_list(sorted(icodes))}) AND vstdate BETWEEN {lit(since)} AND {lit(end)} AND IFNULL(vn,'')<>'' AND qty>0 {by_hn}""")
    return [{**r, "hn": text(r["hn"]), "name": icodes.get(text(r["icode"]), "")} for r in rows
            if hns is None or text(r["hn"]) in hns]


def last_drugs(q: Select, icodes: dict[str, str], end: date, hns: set[str]) -> dict[str, tuple[date, str]]:
    """hn -> the last day a glucose-lowering drug was dispensed (any time up to end) and its name."""
    if not icodes or not hns:
        return {}
    out = {}
    for r in q(f"""
        SELECT hn, MAX(vstdate) d, SUBSTRING_INDEX(GROUP_CONCAT(icode ORDER BY vstdate DESC),',',1) icode FROM opitemrece
        WHERE icode IN ({lit_list(sorted(icodes))}) AND vstdate<={lit(end)} AND IFNULL(vn,'')<>'' AND qty>0 GROUP BY hn"""):
        hn = text(r["hn"])
        if hn in hns and (day := _day(r["d"])):
            out[hn] = (day, icodes.get(text(r["icode"]), ""))
    return out


def fetch(q: Select, start: date, end: date, since: date | None = None) -> dict:
    """The HbA1c from `since` (default a year before the range) to the end of the range of the patients with an HbA1c in
    the range, and their glucose-lowering drugs.

    since: how far back a remission may have begun, from the earliest registration in the registry (window_start)."""
    since = min(since or start, start - timedelta(days=365))
    found = q(f"""
        SELECT {VISIT_COLS}, lo.lab_order_result result FROM ovst o JOIN lab_head lh ON lh.vn=o.vn
        JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number AND lo.lab_items_code={HBA1C_LAB} {VISIT_JOINS}
        WHERE o.vstdate BETWEEN {lit(since)} AND {lit(end)} AND IFNULL(lo.lab_order_result,'')<>''""")
    hns = {text(r["hn"]) for r in found if start <= (_day(r["vstdate"]) or since) <= end}
    if not hns:
        return {"tests": [], "drugs": [], "e11": set(), "last_drug": {}}
    tests = [r for r in found if text(r["hn"]) in hns]
    icodes = drug_icodes(q)
    drugs = drug_days(q, icodes, since - timedelta(days=DRUG_BEFORE_DAYS), end, hns)
    e11 = {text(r["hn"]) for r in q(f"""
        SELECT DISTINCT hn FROM ovstdiag WHERE hn IN ({lit_list(sorted(hns))}) AND icd10 LIKE 'E11%%'""")}
    return {"tests": tests, "drugs": drugs, "e11": e11, "last_drug": last_drugs(q, icodes, end, hns)}


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


def stages_registered(tests: list[tuple[date, Decimal, dict]], drug_days: list[date], stop: date) -> dict[int, dict]:
    """The stages of a patient in the e-Claim registry: the registration (its วันที่ลงทะเบียน, `stop`) is the first
    test; the 2nd (stage 1) and 3rd (stage 2) are the next HbA1c below 6.5% at least 3 months after the one before,
    with no glucose-lowering drug dispensed (qty > 0) in between. A result of 6.5% or more on the way ends it; a lower
    one sooner than 3 months is passed over.

    The HbA1c of the registration day in HOSxP is used for the first test when there is one, otherwise the first test
    has no visit (value None)."""
    first = next((t for t in tests if t[0] == stop), (stop, None, {}))
    after = [t for t in tests if t[0] > stop]

    def following(prev: date) -> tuple | None:
        for t in after:
            if t[0] <= prev:
                continue
            if t[1] >= LIMIT:
                return None
            if (t[0] - prev).days >= GAP_DAYS:
                return t if not any(prev < d <= t[0] for d in drug_days) else None
        return None

    second = following(stop)
    if not second:
        return {}
    out = {1: {"visit": second[2], "tests": [first, second]}}
    if third := following(second[0]):
        out[2] = {"visit": third[2], "tests": [first, second, third]}
    return out


def check(data: dict, start: date, end: date, sent: dict[tuple[str, str], str] | None = None,
          registry: dict[str, list[dict]] | None = None) -> list[Row]:
    """registry: PID -> its e-Claim registrations (None = no registry imported yet, so it is not checked)."""
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
    for hn, (day, name) in data.get("last_drug", {}).items():  # the last drug before the window
        if hn not in last_drug or day > last_drug[hn][0]:
            last_drug[hn] = (day, name)

    rows: list[Row] = []
    for hn, tests in by_hn.items():
        tests.sort(key=lambda t: (t[0], text(t[2]["vn"])))
        # One result a day is enough (a repeat on the same day is not a new test).
        tests = [t for i, t in enumerate(tests) if i == 0 or t[0] != tests[i - 1][0]]
        cid_regs = [x for x in (registry or {}).get(text(tests[0][2].get("cid")), []) if x["status"] == "Y" and x["reg_date"]]
        since = window_start(cid_regs[0]["reg_date"]) if cid_regs else start - timedelta(days=365)
        stop = stop_day(cid_regs, end)
        hn_drugs = drugs.get(hn, [])
        if stop:
            since = stop
            hn_drugs = [x for x in hn_drugs if x > stop]
        tests = [t for t in tests if t[0] >= since]
        found_stages = stages_registered(tests, hn_drugs, stop) if stop else stages(tests, hn_drugs)
        first = next((t for t in tests if stop and t[0] == stop), None)
        if first:  # test 1: the registration day, shown (0 baht) so its HbA1c visit can be found and sent
            found_stages = {0: {"visit": first[2], "tests": [first]}, **found_stages}
        for stage, found in found_stages.items():
            visit = found["visit"]
            day = _day(visit["vstdate"])
            if not (start <= day <= end):
                continue
            vn = text(visit["vn"])
            code = f"DMR{stage}"  # this program's own key: no ADP code is sent
            row = Row(key=f"{vn}|{code}", vn=vn, code=code, item_name="ครั้งที่ 1: ลงทะเบียน e-Claim (หยุดยา)" if stage == 0 else
                      f"ขั้น {stage}: " + ("เข้าสู่เบาหวานระยะสงบ" if stage == 1 else "ยังคงเบาหวานระยะสงบ"))
            row.qty, row.amount = Decimal("1"), RATES[stage]
            row.hn, row.date, row.time = hn, day.isoformat(), str(visit.get("vsttime") or "")[:8]
            row.ptname, row.cid = text(visit.get("ptname")), text(visit.get("cid"))
            row.pttype = f"{text(visit.get('pttype'))}: {text(visit.get('pttype_name'))}".strip(": ")
            row.count = stage
            age = visit.get("age_y")
            drug = last_drug.get(hn)
            used = found["tests"]
            gaps = [(used[i][0] - used[i - 1][0]).days for i in range(1, len(used))]
            regs = (registry or {}).get(text(visit.get("cid")), [])
            reg = next((x for x in regs if x["status"] == "Y" and not (x["expire_date"] and x["expire_date"] < day.isoformat())), None)
            row.extra = {"stage": stage, "age": int(age) if age is not None else None, "gaps": gaps,
                         "registered": reg["reg_date"] if reg else "", "registry_checked": registry is not None,
                         "stopped": stop.isoformat() if stop else "",
                         "tests": [{"date": d.isoformat(), "value": "" if v is None else f"{v}", "vn": text(t.get("vn"))}
                                   for d, v, t in found["tests"]],
                         "all_tests": [{"date": d.isoformat(), "value": f"{v}"} for d, v, _ in tests],
                         "last_drug": drug[0].isoformat() if drug else "", "last_drug_name": drug[1] if drug else "",
                         "maininscl": text(visit.get("maininscl"))}
            if registry is not None and not reg:
                row.add(ERROR, "NOT_REGISTERED", "ยังไม่ลงทะเบียน E-claim")
            elif reg and reg["reg_date"] > day.isoformat():
                row.add(WARN, "LATE_REGISTER", f"ลงทะเบียนเมื่อ {_thai(date.fromisoformat(reg['reg_date']))} หลังวันที่ตรวจของขั้นนี้")
            if text(visit.get("maininscl")) not in ("UCS", "WEL"):
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


def _thai(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}/{day.year + 543}"


def cutoff(day: date) -> date:
    """The next data cut for payment on or after a day: 31 March or 15 September."""
    for c in (date(day.year, 3, 31), date(day.year, 9, 15), date(day.year + 1, 3, 31)):
        if c >= day:
            return c
    return date(day.year + 1, 3, 31)


def follow_up(tests: list[tuple[date, Decimal]], drug_days: list[date], today: date, since: date | None = None,
              stop: date | None = None) -> dict:
    """Where a registered patient stands and when the next HbA1c should be done.

    state: "done" (stage 2 reached), "wait3" (stage 1 reached, the 3rd test is due), "wait2" (one test below 6.5% off
    drugs, the 2nd is due), "not_yet" (no test below 6.5% off drugs yet), "no_test"."""
    last_drug = max(drug_days, default=None)
    if stop:  # the registration is the first test (stages_registered)
        since, drug_days = stop, [x for x in drug_days if x > stop]
    if since:
        tests = [t for t in tests if t[0] >= since]
    rows = [(d, v, {}) for d, v in tests]
    found = stages_registered(rows, drug_days, stop) if stop else stages(rows, drug_days)
    if 2 in found:
        d3 = found[2]["tests"][2][0]
        return {"state": "done", "stage": 2, "since": found[2]["tests"][1][0], "next_from": None, "next_to": None,
                "reached": d3, "last_drug": last_drug}
    if 1 in found:
        d2 = found[1]["tests"][1][0]
        later = [t for t in tests if t[0] > d2]
        nxt = d2 + timedelta(days=GAP_DAYS)
        state = "wait3"
        note = ""
        if later and later[0][1] >= LIMIT:
            state, note = "relapse", f"ผลครั้งถัดไป {later[0][1]}% ไม่ผ่าน"
        elif later and (later[0][0] - d2).days < GAP_DAYS:
            note = f"ตรวจครั้งถัดไปเร็วไป ({(later[0][0] - d2).days} วัน)"
        return {"state": state, "stage": 1, "since": d2, "next_from": nxt, "next_to": d2 + timedelta(days=183),
                "reached": d2, "last_drug": last_drug, "note": note}
    if stop:  # registered: waiting for the 2nd test, 3 months after the registration
        later = [t for t in tests if t[0] > stop]
        drug_after = any(x > stop for x in drug_days)
        note = "ยังได้ยาลดน้ำตาลหลังลงทะเบียน" if drug_after else (
            f"ผลครั้งถัดไป {later[0][1]}% ไม่ผ่าน" if later and later[0][1] >= LIMIT else "")
        return {"state": "not_yet" if note else "wait2", "stage": 0, "since": stop,
                "next_from": None if note else stop + timedelta(days=GAP_DAYS), "next_to": None, "reached": None,
                "last_drug": last_drug, "note": note}
    if not tests:
        return {"state": "no_test", "stage": 0, "since": None, "next_from": None, "next_to": None, "reached": None,
                "last_drug": last_drug}
    d, v = tests[-1]
    off_drug = not any(d - timedelta(days=DRUG_BEFORE_DAYS) <= x <= today for x in drug_days)
    if v < LIMIT and off_drug:
        return {"state": "wait2", "stage": 0, "since": d, "next_from": d + timedelta(days=GAP_DAYS), "next_to": None,
                "reached": None, "last_drug": last_drug}
    reason = f"ผลล่าสุด {v}%" if v >= LIMIT else "ยังได้ยาลดน้ำตาล"
    return {"state": "not_yet", "stage": 0, "since": d, "next_from": None, "next_to": None, "reached": None,
            "last_drug": last_drug, "note": reason}


def stage_visits(picked: list[Row]) -> list[str]:
    """The HbA1c visits that make up the picked stages, in date order: they go into the ZIP with their LAB rows."""
    seen: dict[str, str] = {}
    for r in picked:
        for t in r.extra.get("tests", []):
            if t["vn"]:  # the registration has no visit when HOSxP has no HbA1c that day
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
          SUM(IF(IFNULL(oo.qty,0)=0,0,IFNULL(oo.sum_price,0))) amount, EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode) is_drug
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn={lit(vn)}
        GROUP BY oo.icode ORDER BY d.nhso_adp_code IS NULL, oo.icode""")
    dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={lit(vn)} ORDER BY diagtype, icd10")
    return {"visit": visit, "tests": tests, "drugs": drugs, "items": items, "dx": dx}
