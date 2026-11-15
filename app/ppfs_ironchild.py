"""PPFS 27) บริการยาเสริมธาตุเหล็กเพื่อป้องกันโลหิตจางจากการขาดธาตุเหล็ก - iron supplement for Thai children, all
rights, 50 baht once a fiscal year (October-September) in each age band:

   6-12 months   fee 3004084 "18จ่ายยาเสริมธาตุเหล็ก 6เดือน-1ปี6เดือน"
   2-5 years     fee 3004023 "18จ่ายยาเสริมธาตุเหล็ก 2-5 ปี"

The e-Claim code follows the iron drug given at the visit, as the user set: syrup (drugitems.dosageform SYRUPS /
SUSPENSIONS / SOLUTIONS) is ADP 689697 (PPFS_IRON_SYRUP_ADP), tablets (TABLETS) ADP 776508 (PPFS_IRON_TABLET_ADP).
A visit with no iron drug, or with both forms, cannot be sent. HOSxP keys both fees under the placeholder ADP 10000
(named "NONE"), which other items share, so the fees are read by icode and the ZIP sends the chosen code in place of
10000. Ages are the age at the visit (vn_stat); "2-5 ปี" is 2 years 0 months to 5 years 11 months.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .nhso18 import lit, lit_list
from .ppfs import ERROR, SELF_PAID, WARN, Row, Select, _day, _money, fiscal_range, fiscal_year, self_paid_error
from .validate import text

HOSXP_CODE = "10000"
RATE = Decimal("50")
IRON = "FERR|(^|[^A-Za-z])IRON|TRIFER"  # not SPIRONOLACTONE
TABLET = "TAB|CAP"
SYRUP = "SYRUP|SUSP|SOLUTION|DROP|ELIXIR"


@dataclass(frozen=True)
class Band:
    label: str
    adp: str  # the code of the band when it has one fixed code (26); 27) picks it by the drug form
    icodes: tuple[str, ...]
    months: tuple[int, int]  # first and last month of age, inclusive


def bands(settings) -> list[Band]:
    return [Band("6–12 เดือน", "", settings.ppfs_iron_child_icodes, (6, 12)),
            Band("2–5 ปี", "", settings.ppfs_iron_child2_icodes, (24, 71))]


def form_codes(settings) -> dict[str, str]:
    return {"syrup": settings.ppfs_iron_syrup_adp, "tablet": settings.ppfs_iron_tablet_adp}


def all_icodes(band_list: list[Band]) -> tuple[str, ...]:
    return tuple(i for b in band_list for i in b.icodes)


VISIT_COLS = """o.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, p.cid, p.nationality, p.sex, v.age_y, v.age_m, v.age_d"""
VISIT_JOINS = """JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype LEFT JOIN vn_stat v ON v.vn=o.vn"""


def _form(dosageform: str) -> str:
    upper = (dosageform or "").upper()
    if re.search(TABLET, upper):
        return "tablet"
    if re.search(SYRUP, upper):
        return "syrup"
    return ""


def _iron(q: Select, vns: list[str]) -> dict[str, dict]:
    """VN -> {"name": the iron drugs given, "qty", "forms": {"syrup", "tablet"}} (injections are not counted)."""
    if not vns:
        return {}
    out: dict[str, dict] = {}
    for r in q(f"""
            SELECT oo.vn, CONCAT_WS(' ',di.name,di.strength) name, di.dosageform, SUM(oo.qty) qty FROM opitemrece oo
            JOIN drugitems di ON di.icode=oo.icode
            WHERE oo.vn IN ({lit_list(vns)}) AND CONCAT_WS(' ',di.name,di.generic_name) REGEXP {lit(IRON)}
            GROUP BY oo.vn, oo.icode"""):
        form = _form(text(r.get("dosageform")))
        if not form:
            continue
        entry = out.setdefault(text(r["vn"]), {"names": [], "qty": Decimal("0"), "forms": set()})
        entry["names"].append(f"{text(r['name'])} × {Decimal(str(r['qty'] or 0)):g}")
        entry["qty"] += Decimal(str(r["qty"] or 0))
        entry["forms"].add(form)
    return {vn: {"name": ", ".join(e["names"]), "qty": e["qty"], "forms": e["forms"]} for vn, e in out.items()}


def fetch(q: Select, start: date, end: date, band_list: list[Band]) -> dict:
    icodes = all_icodes(band_list)
    items = q(f"""
        SELECT {VISIT_COLS}, oo.icode, oo.paidst, d.name item_name, oo.qty, IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode {VISIT_JOINS}
        WHERE o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND oo.icode IN ({lit_list(icodes)})""")
    hns = sorted({text(r["hn"]) for r in items})
    history = q(f"""
        SELECT oo.vn, o.hn, o.vstdate, oo.icode FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn
        WHERE o.hn IN ({lit_list(hns)}) AND oo.icode IN ({lit_list(icodes)}) AND IFNULL(oo.qty,0)>0
          AND o.vstdate BETWEEN {lit(fiscal_range(fiscal_year(start))[0])} AND {lit(fiscal_range(fiscal_year(end))[1])}
        GROUP BY oo.vn, oo.icode""") if hns else []
    return {"items": items, "history": history, "iron": _iron(q, sorted({text(r["vn"]) for r in items}))}


def months_old(r: dict) -> int | None:
    if r.get("age_y") is None:
        return None
    return int(r["age_y"]) * 12 + int(r.get("age_m") or 0)


def _band_of(icode: str, band_list: list[Band]) -> int:
    return next(i for i, b in enumerate(band_list) if icode in b.icodes)


FORM_LABEL = {"syrup": "ยาน้ำ", "tablet": "ยาเม็ด"}


def check(data: dict, band_list: list[Band], codes: dict[str, str],
          sent: dict[tuple[str, str], str] | None = None) -> list[Row]:
    """codes: {"syrup": ADP, "tablet": ADP}."""
    sent = sent or {}
    rows: dict[tuple[str, int], Row] = {}
    info: dict[str, dict] = {}
    for r in data["items"]:
        vn, b = text(r["vn"]), _band_of(text(r["icode"]), band_list)
        info.setdefault(vn, r)
        row = rows.get((vn, b))
        if row is None:
            forms = data["iron"].get(vn, {}).get("forms", set())
            code = codes.get(next(iter(forms)), "") if len(forms) == 1 else ""
            row = rows[(vn, b)] = Row(key=f"{vn}|{code or f'{HOSXP_CODE}-{b + 1}'}", vn=vn, code=code or f"{HOSXP_CODE}-{b + 1}",
                                      item_name=text(r.get("item_name")))
        row.qty += Decimal(str(r.get("qty") or 0))
        row.amount += _money(r.get("amount"))
        row.self_paid |= text(r.get("paidst")) == SELF_PAID

    claims: dict[tuple[str, int, int], list[tuple[date, str]]] = {}
    for h in data["history"]:
        if day := _day(h["vstdate"]):
            key = (text(h["hn"]), _band_of(text(h["icode"]), band_list), fiscal_year(day))
            claims.setdefault(key, []).append((day, text(h["vn"])))
    for v in claims.values():
        v.sort()

    for (vn, b), row in rows.items():
        band = band_list[b]
        r = info[vn]
        day = _day(r.get("vstdate"))
        row.hn, row.date, row.time = text(r.get("hn")), day.isoformat() if day else "", str(r.get("vsttime") or "")[:8]
        row.ptname, row.cid = text(r.get("ptname")), text(r.get("cid"))
        row.pttype = f"{text(r.get('pttype'))}: {text(r.get('pttype_name'))}".strip(": ")
        months = months_old(r)
        given = data["iron"].get(vn)
        forms = given["forms"] if given else set()
        form = next(iter(forms)) if len(forms) == 1 else ""
        adp = codes.get(form, "") if form else ""
        year = fiscal_year(day) if day else None
        earlier = [(d, v) for d, v in claims.get((row.hn, b, year), []) if (d, v) < (day, vn)] if day else []
        first = earlier[0] if earlier else None
        row.extra = {"months": months, "age_text": f"{r.get('age_y')} ปี {r.get('age_m') or 0} เดือน" if months is not None else "",
                     "band": band.label, "form": FORM_LABEL.get(form, ""), "drug_name": given["name"] if given else "",
                     "adp": adp, "year": year,
                     "first": first[0].isoformat() if first else "", "first_vn": first[1] if first else ""}
        billed = row.qty > 0
        row.count = len(earlier) + 1 if billed else None

        if not forms:
            row.add(ERROR, "NO_IRON", "ไม่พบยาเสริมธาตุเหล็ก (ยาน้ำหรือยาเม็ด) ใน visit นี้ จึงเลือกรหัส ADP ไม่ได้")
        elif len(forms) > 1:
            row.add(ERROR, "BOTH_FORMS", "จ่ายทั้งยาน้ำและยาเม็ดใน visit เดียวกัน เลือกรหัส ADP ไม่ได้")
        elif not adp:
            row.add(ERROR, "NO_ADP", f"ยังไม่ได้ตั้งรหัส ADP ของ{FORM_LABEL[form]}")
        if text(r.get("nationality")) != "99":
            row.add(ERROR, "NOT_THAI", "ไม่ใช่คนไทย (สัญชาติไม่ใช่ 99)")
        if text(r.get("an")):
            row.add(ERROR, "IPD", "visit นี้ Admit แล้ว (มี AN)")
        self_paid_error(row)
        low, high = band.months
        if months is not None and not low <= months <= high:
            row.add(ERROR, "AGE", f"อายุ {row.extra['age_text']} ค่าบริการช่วง {band.label} เบิกไม่ได้")
        if billed and first:
            row.add(ERROR, "OVER_YEAR", f"ปีงบ {year + 543} เบิกช่วง {band.label} ไปแล้วเมื่อ {_thai(first[0])} (VN {first[1]}) ครั้งเดียวต่อปีงบ")
        if billed and (row.qty != 1 or row.amount != RATE):
            row.add(WARN, "PRICE", f"ยอด {row.amount} บาท (จำนวน {row.qty:g}) ไม่ตรงอัตรา {RATE} บาท/ครั้ง/ปี")
        row.sent = sent.get((vn, row.code), "")
    return sorted(rows.values(), key=lambda x: (x.date, x.time, x.vn, x.code))


def _thai(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}/{day.year + 543}"


def keep_icodes(band_list: list[Band], codes: set[str]) -> set[str]:
    """The fee icodes to keep in the ZIP: all of the service's fees when the band has no fixed code (27), else the
    fees of the bands whose code is claimed (26)."""
    return {i for b in band_list if not b.adp or b.adp in codes for i in b.icodes}


def rewrite_adp(data: dict[str, list[dict]], claimed: dict[str, set[str]]) -> None:
    """Send the e-Claim code chosen for each visit in place of HOSxP's placeholder 10000."""
    for r in data.get("ADP", []):
        codes = claimed.get(text(r.get("SEQ")), set())
        if text(r.get("CODE")) == HOSXP_CODE and len(codes) == 1:
            r["CODE"] = next(iter(codes))


def detail(q: Select, vn: str, band_list: list[Band], codes: dict[str, str]) -> dict:
    visit = (q(f"""
        SELECT o.vn, o.hn, o.vstdate, p.nationality, v.age_y,
          (SELECT n.name FROM nationality n WHERE n.nationality=p.nationality) nationality_name
        FROM ovst o JOIN patient p ON p.hn=o.hn LEFT JOIN vn_stat v ON v.vn=o.vn WHERE o.vn={lit(vn)}""") or [None])[0]
    if visit is None:
        return {}
    claims = q(f"""
        SELECT o.vn, o.vstdate, v.age_y, v.age_m, oo.icode, SUM(oo.qty) qty, SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn LEFT JOIN vn_stat v ON v.vn=o.vn
        WHERE o.hn={lit(visit["hn"])} AND oo.icode IN ({lit_list(all_icodes(band_list))})
        GROUP BY o.vn, oo.icode ORDER BY o.vstdate, o.vn""")
    iron = _iron(q, sorted({text(c["vn"]) for c in claims} | {vn}))
    for c in claims:
        forms = iron.get(text(c["vn"]), {}).get("forms", set())
        form = next(iter(forms)) if len(forms) == 1 else ""
        c["band"] = band_list[_band_of(text(c["icode"]), band_list)].label
        c["form"], c["adp"] = FORM_LABEL.get(form, ""), codes.get(form, "") if form else ""
    items = q(f"""
        SELECT oo.icode, CONCAT_WS(' ',d.name,d.strength) name, d.nhso_adp_code adp, SUM(oo.qty) qty,
          SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount, EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode) is_drug
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn={lit(vn)}
        GROUP BY oo.icode ORDER BY d.nhso_adp_code IS NULL, oo.icode""")
    dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={lit(vn)} ORDER BY diagtype, icd10")
    return {"visit": visit, "claims": claims, "iron": iron, "items": items, "dx": dx}
