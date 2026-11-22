"""Diabetes in pregnancy, UC right, paid once per case through e-Claim with no ADP code (the user, 2026-11-22):

2) กรณีบริการหญิงตั้งครรภ์ที่ถูกวินิจฉัยว่าเป็นเบาหวาน (GDM) - O24.4 / O24.9, 2,620 baht.
   Services: self-care teaching, capillary glucose (DTX), an OGTT after the birth.
3) กรณีบริการหญิงตั้งครรภ์ที่ถูกวินิจฉัยว่าเป็นเบาหวานก่อนตั้งครรภ์ (PDM) - O24.0 / O24.1 / O24.3, paid by the
   gestational age at the first visit (8,660 baht at 0-4 weeks down to 1,940 baht after 36 weeks; the notice is still
   being revised, so the bands are in PDM_BANDS only). Services: self-care teaching, DTX, HbA1c 4 times.

Both need the patient registered in the e-Claim program (not in HOSxP: the user checks) and the code as principal or
secondary diagnosis. A missing DTX, OGTT or HbA1c is only a warning; teaching has no fixed place in HOSxP.

The ZIP sends the diagnosis visit with its ticked charges and its LAB rows, and สปสช. pays from the diagnosis and the
registration. One claim per pregnancy: diagnosis visits more than PREGNANCY_DAYS after the first of a group start a
new pregnancy. The row is the group's first visit; if any visit of the group was already put in a ZIP, the row is that
one, marked sent, so the case is not claimed twice. The birth is the patient's person_anc.labor_date after the first
visit. The gestational age is person_anc_service.pa_week of the first visit, else counted from the pregnancy's LMP.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .nhso18 import lit, lit_list
from .ppfs import ERROR, WARN, Row, Select, _day
from .validate import text

DTX_LABS = (298,)        # Glucose, capillaries (DTX)
OGTT_LABS = (749, 751)   # glucose by 1 hr / 2 hr 75g (the postpartum OGTT)
HBA1C_LABS = (198,)
HBA1C_TIMES = 4
PREGNANCY_DAYS = 300
# (last week of the band, baht): the gestational age in completed weeks at the first visit
PDM_BANDS = ((4, Decimal("8660")), (9, Decimal("7820")), (13, Decimal("6980")), (17, Decimal("6140")), (21, Decimal("5300")),
             (26, Decimal("4460")), (31, Decimal("3620")), (36, Decimal("2780")), (99, Decimal("1940")))


@dataclass(frozen=True)
class Kind:
    key: str
    code: str
    icd10: tuple[str, ...]
    name: str
    rate: Decimal | None  # None: by gestational age (PDM_BANDS)


GDM = Kind("gdm", "GDM", ("O244", "O249"), "เหมาจ่าย GDM (ครั้งเดียวต่อการตั้งครรภ์)", Decimal("2620"))
PDM = Kind("pdm", "PDM", ("O240", "O241", "O243"), "เหมาจ่าย PDM ตามอายุครรภ์ครั้งแรก", None)
KINDS = {k.key: k for k in (GDM, PDM)}

VISIT_COLS = """o.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name, seekhipdatas(o.pttype,0) maininscl,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, p.cid, p.sex, v.age_y"""
VISIT_JOINS = """JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype LEFT JOIN vn_stat v ON v.vn=o.vn"""


def pdm_rate(weeks: int) -> Decimal:
    return next(rate for last, rate in PDM_BANDS if weeks <= last)


def _labs(q: Select, hns: list[str], codes: tuple[int, ...], since: date) -> list[dict]:
    if not hns:
        return []
    return q(f"""
        SELECT o.hn, o.vn, o.vstdate, lo.lab_items_code code, li.lab_items_name name, lo.lab_order_result result
        FROM lab_head lh JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number JOIN ovst o ON o.vn=lh.vn
        LEFT JOIN lab_items li ON li.lab_items_code=lo.lab_items_code
        WHERE o.hn IN ({lit_list(hns)}) AND lo.lab_items_code IN ({','.join(str(c) for c in codes)})
          AND o.vstdate >= {lit(since)} AND IFNULL(lo.lab_order_result,'')<>'' ORDER BY o.vstdate""")


def _anc(q: Select, hns: list[str]) -> list[dict]:
    if not hns:
        return []
    return q(f"""
        SELECT p.patient_hn hn, a.labor_date, a.lmp FROM person_anc a JOIN person p ON p.person_id=a.person_id
        WHERE p.patient_hn IN ({lit_list(hns)}) AND (a.labor_date IS NOT NULL OR a.lmp IS NOT NULL)""")


def _weeks(q: Select, vns: list[str]) -> dict[str, int]:
    if not vns:
        return {}
    return {text(r["vn"]): int(r["pa_week"]) for r in q(f"""
        SELECT vn, MAX(pa_week) pa_week FROM person_anc_service WHERE vn IN ({lit_list(vns)}) AND pa_week IS NOT NULL
        GROUP BY vn""")}


def fetch(q: Select, kind: Kind, start: date, end: date) -> dict:
    """The diagnosis visits from PREGNANCY_DAYS before the range (to find each pregnancy's first visit) to its end, of
    the patients with one in the range, with their labs, births, LMPs and the visits' gestational ages."""
    since = start - timedelta(days=PREGNANCY_DAYS)
    found = q(f"""
        SELECT {VISIT_COLS}, GROUP_CONCAT(DISTINCT d.icd10 ORDER BY d.icd10) icd10 FROM ovst o
        JOIN ovstdiag d ON d.vn=o.vn AND d.icd10 IN ({lit_list(kind.icd10)}) {VISIT_JOINS}
        WHERE o.vstdate BETWEEN {lit(since)} AND {lit(end)} GROUP BY o.vn""")
    hns = sorted({text(r["hn"]) for r in found if start <= (_day(r["vstdate"]) or since) <= end})
    visits = [r for r in found if text(r["hn"]) in hns]
    lab_since = since - timedelta(days=PREGNANCY_DAYS)  # an HbA1c early in the pregnancy, before the diagnosis
    return {"visits": visits, "dtx": _labs(q, hns, DTX_LABS, lab_since), "ogtt": _labs(q, hns, OGTT_LABS, since),
            "hba1c": _labs(q, hns, HBA1C_LABS, lab_since) if kind is PDM else [], "anc": _anc(q, hns),
            "weeks": _weeks(q, [text(v["vn"]) for v in visits]) if kind is PDM else {}}


def pregnancies(visits: list[dict]) -> list[list[dict]]:
    """One patient's diagnosis visits split into pregnancies (a gap of more than PREGNANCY_DAYS from a group's first)."""
    groups: list[list[dict]] = []
    for v in sorted(visits, key=lambda r: (_day(r["vstdate"]), text(r.get("vsttime")), text(r["vn"]))):
        if groups and (_day(v["vstdate"]) - _day(groups[-1][0]["vstdate"])).days <= PREGNANCY_DAYS:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


def _thai(day: date) -> str:
    return f"{day.day}/{day.month}/{day.year + 543}"


def check(data: dict, kind: Kind, start: date, end: date, sent: dict[tuple[str, str], str] | None = None) -> list[Row]:
    sent = sent or {}
    by_hn: dict[str, list[dict]] = {}
    for v in data["visits"]:
        if _day(v["vstdate"]):
            by_hn.setdefault(text(v["hn"]), []).append(v)
    labs: dict[str, dict[str, list[dict]]] = {"dtx": {}, "ogtt": {}, "hba1c": {}}
    for name in labs:
        for r in data.get(name, []):
            if day := _day(r["vstdate"]):
                labs[name].setdefault(text(r["hn"]), []).append({"date": day.isoformat(), "vn": text(r["vn"]),
                                                                  "name": text(r["name"]), "result": text(r["result"])})
    births: dict[str, list[date]] = {}
    lmps: dict[str, list[date]] = {}
    for a in data["anc"]:
        if day := _day(a.get("labor_date")):
            births.setdefault(text(a["hn"]), []).append(day)
        if day := _day(a.get("lmp")):
            lmps.setdefault(text(a["hn"]), []).append(day)

    rows: list[Row] = []
    for hn, visits in by_hn.items():
        for group in pregnancies(visits):
            first_visit = group[0]
            first = _day(first_visit["vstdate"])
            done = next((v for v in group if sent.get((text(v["vn"]), kind.code))), None)
            visit = done or first_visit
            day = _day(visit["vstdate"])
            if not (start <= day <= end):
                continue
            vn = text(visit["vn"])
            last = first + timedelta(days=PREGNANCY_DAYS)
            birth = min((b for b in births.get(hn, []) if first <= b <= last), default=None)
            lmp = max((x for x in lmps.get(hn, []) if x <= first and (first - x).days <= PREGNANCY_DAYS), default=None)
            begin = lmp or first
            during = lambda x: begin.isoformat() <= x["date"] <= (birth or last).isoformat()
            dtx = [x for x in labs["dtx"].get(hn, []) if during(x)]
            ogtt = [x for x in labs["ogtt"].get(hn, []) if birth and x["date"] > birth.isoformat()]
            hba1c = [x for x in labs["hba1c"].get(hn, []) if during(x)]
            row = Row(key=f"{vn}|{kind.code}", vn=vn, code=kind.code, item_name=kind.name)
            row.hn, row.date, row.time = hn, day.isoformat(), str(visit.get("vsttime") or "")[:8]
            row.ptname, row.cid = text(visit.get("ptname")), text(visit.get("cid"))
            row.pttype = f"{text(visit.get('pttype'))}: {text(visit.get('pttype_name'))}".strip(": ")
            row.labor_date = birth.isoformat() if birth else ""
            row.qty = Decimal("1")
            age = visit.get("age_y")
            row.extra = {"age": int(age) if age is not None else None, "icd10": text(visit.get("icd10")),
                         "first": first.isoformat(), "first_vn": text(first_visit["vn"]), "visits": len(group),
                         "dtx": dtx, "ogtt": ogtt, "hba1c": hba1c, "lmp": lmp.isoformat() if lmp else "",
                         "maininscl": text(visit.get("maininscl"))}
            if kind.rate is not None:
                row.amount = kind.rate
            else:
                weeks = data["weeks"].get(text(first_visit["vn"]))
                source = "บันทึกฝากครรภ์ (pa_week)"
                if weeks is None and lmp:
                    weeks, source = (first - lmp).days // 7, "นับจาก LMP"
                row.extra.update(weeks=weeks, weeks_from=source if weeks is not None else "")
                if weeks is None:
                    row.add(ERROR, "NO_GA", "ไม่พบอายุครรภ์ของ visit แรก (pa_week หรือ LMP ในทะเบียนฝากครรภ์) จึงคิดอัตราจ่ายไม่ได้")
                else:
                    row.amount = pdm_rate(weeks)
            if text(visit.get("maininscl")) not in ("UCS", "WEL"):
                row.add(ERROR, "NOT_UC", f"visit นี้ไม่ใช่สิทธิ UC ({text(visit.get('maininscl')) or 'ไม่ทราบ'})")
            if text(visit.get("sex")) not in ("", "2"):
                row.add(ERROR, "NOT_FEMALE", "ผู้ป่วยไม่ใช่เพศหญิง ตรวจรหัสโรคใน HOSxP")
            if text(visit.get("an")):
                row.add(ERROR, "IPD", "visit นี้ Admit แล้ว (มี AN) เลือก visit ผู้ป่วยนอก")
            if not dtx:
                row.add(WARN, "NO_DTX", "ยังไม่พบผลตรวจน้ำตาลปลายนิ้ว (DTX) ระหว่างตั้งครรภ์")
            if kind is GDM:
                if not birth:
                    row.add(WARN, "NO_OGTT", "ยังไม่มีผล OGTT หลังคลอด (ยังไม่พบวันคลอดใน HOSxP)")
                elif not ogtt:
                    row.add(WARN, "NO_OGTT", f"ยังไม่มีผล OGTT หลังคลอด (คลอด {_thai(birth)})")
            elif len(hba1c) < HBA1C_TIMES:
                row.add(WARN, "HBA1C_COUNT", f"ตรวจ HbA1c ระหว่างตั้งครรภ์ {len(hba1c)} ครั้ง (ต้อง {HBA1C_TIMES} ครั้ง)")
            row.sent = sent.get((vn, kind.code), "")
            rows.append(row)
    return sorted(rows, key=lambda x: (x.date, x.time, x.vn))


def all_vns(data: dict) -> list[str]:
    return sorted({text(v["vn"]) for v in data["visits"]})


def detail(q: Select, kind: Kind, vn: str) -> dict:
    visit = (q(f"""
        SELECT o.vn, o.hn, o.vstdate, p.nationality, (SELECT n.name FROM nationality n WHERE n.nationality=p.nationality) nationality_name
        FROM ovst o JOIN patient p ON p.hn=o.hn WHERE o.vn={lit(vn)}""") or [None])[0]
    if visit is None:
        return {}
    hn = text(visit["hn"])
    since = (_day(visit["vstdate"]) or date.today()) - timedelta(days=PREGNANCY_DAYS)
    visits = q(f"""
        SELECT o.vn, o.vstdate, GROUP_CONCAT(DISTINCT d.icd10 ORDER BY d.icd10) icd10 FROM ovst o JOIN ovstdiag d ON d.vn=o.vn
        WHERE o.hn={lit(hn)} AND d.icd10 IN ({lit_list(kind.icd10)}) AND o.vstdate >= {lit(since)} GROUP BY o.vn ORDER BY o.vstdate""")
    items = q(f"""
        SELECT oo.icode, CONCAT_WS(' ',d.name,d.strength) name, d.nhso_adp_code adp, SUM(oo.qty) qty,
          SUM(IF(IFNULL(oo.qty,0)=0,0,IFNULL(oo.sum_price,0))) amount, EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode) is_drug
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn={lit(vn)}
        GROUP BY oo.icode ORDER BY d.nhso_adp_code IS NULL, oo.icode""")
    dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={lit(vn)} ORDER BY diagtype, icd10")
    codes = DTX_LABS + (OGTT_LABS if kind is GDM else HBA1C_LABS)
    return {"visit": visit, "visits": visits, "labs": _labs(q, [hn], codes, since),
            "births": [a for a in _anc(q, [hn]) if a.get("labor_date")], "items": items, "dx": dx}
