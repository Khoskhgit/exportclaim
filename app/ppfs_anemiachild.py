"""PPFS 26) บริการคัดกรองภาวะโลหิตจางจากการขาดธาตุเหล็ก (เด็ก) - an Hb or Hct test for Thai children, all rights,
30 baht once in each age band, so at most 2 per child:

   6-12 months   fee 3003783 "คัดกรองโลหิตจาง…Lab Hb/Hct 6-12 เดือน"
   3-5 years     fee 3004022 "คัดกรองโลหิตจาง…Lab Hb/Hct 3-6 ปี"     (3 years 0 months to 5 years 11 months)

Both are sent as ADP 776508 (PPFS_ANEMIA_CHILD_ADP, given by the user). HOSxP keys them under the placeholder
ADP 10000 ("NONE"), so they are read by icode and the ZIP sends 776508 in place of 10000, as for 27). "Once" is
for life: every fee of the band ever billed in HOSxP counts. The labs are lab_items 3 Hb and 4 Hct.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .nhso18 import lit, lit_list
from .ppfs import ERROR, SELF_PAID, WARN, Row, Select, _day, _money, self_paid_error
from .ppfs_anemia import _labs
from .ppfs_ironchild import Band, months_old
from .validate import text

HOSXP_CODE = "10000"
RATE = Decimal("30")

VISIT_COLS = """o.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, p.cid, p.nationality, p.sex, v.age_y, v.age_m, v.age_d"""
VISIT_JOINS = """JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype LEFT JOIN vn_stat v ON v.vn=o.vn"""


def bands(settings) -> list[Band]:
    adp = settings.ppfs_anemia_child_adp
    return [Band("6–12 เดือน", adp, settings.ppfs_anemia_child_icodes, (6, 12)),
            Band("3–5 ปี", adp, settings.ppfs_anemia_child2_icodes, (36, 71))]


def all_icodes(band_list: list[Band]) -> tuple[str, ...]:
    return tuple(i for b in band_list for i in b.icodes)


def _band_of(icode: str, band_list: list[Band]) -> int:
    return next(i for i, b in enumerate(band_list) if icode in b.icodes)


def fetch(q: Select, start: date, end: date, band_list: list[Band]) -> dict:
    icodes = all_icodes(band_list)
    items = q(f"""
        SELECT {VISIT_COLS}, oo.icode, oo.paidst, d.name item_name, oo.qty, IF(IFNULL(oo.qty,0)=0,0,IFNULL(oo.sum_price,0)) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode {VISIT_JOINS}
        WHERE o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND oo.icode IN ({lit_list(icodes)})""")
    hns = sorted({text(r["hn"]) for r in items})
    history = q(f"""
        SELECT oo.vn, o.hn, o.vstdate, oo.icode FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn
        WHERE o.hn IN ({lit_list(hns)}) AND oo.icode IN ({lit_list(icodes)}) AND IFNULL(oo.qty,0)>0
        GROUP BY oo.vn, oo.icode""") if hns else []
    return {"items": items, "history": history, "labs": _labs(q, sorted({text(r["vn"]) for r in items}))}


def check(data: dict, band_list: list[Band], sent: dict[tuple[str, str], str] | None = None) -> list[Row]:
    sent = sent or {}
    rows: dict[str, Row] = {}
    info: dict[str, dict] = {}
    band_of_vn: dict[str, int] = {}
    for r in data["items"]:
        vn = text(r["vn"])
        info.setdefault(vn, r)
        band_of_vn.setdefault(vn, _band_of(text(r["icode"]), band_list))
        row = rows.get(vn)
        if row is None:
            code = band_list[band_of_vn[vn]].adp or HOSXP_CODE
            row = rows[vn] = Row(key=f"{vn}|{code}", vn=vn, code=code, item_name=text(r.get("item_name")))
        row.qty += Decimal(str(r.get("qty") or 0))
        row.amount += _money(r.get("amount"))
        row.self_paid |= text(r.get("paidst")) == SELF_PAID

    claims: dict[tuple[str, int], list[tuple[date, str]]] = {}
    for h in data["history"]:
        if day := _day(h["vstdate"]):
            claims.setdefault((text(h["hn"]), _band_of(text(h["icode"]), band_list)), []).append((day, text(h["vn"])))
    for v in claims.values():
        v.sort()

    for vn, row in rows.items():
        b = band_of_vn[vn]
        band = band_list[b]
        r = info[vn]
        day = _day(r.get("vstdate"))
        row.hn, row.date, row.time = text(r.get("hn")), day.isoformat() if day else "", str(r.get("vsttime") or "")[:8]
        row.ptname, row.cid = text(r.get("ptname")), text(r.get("cid"))
        row.pttype = f"{text(r.get('pttype'))}: {text(r.get('pttype_name'))}".strip(": ")
        months = months_old(r)
        labs = data["labs"].get(vn, {})
        earlier = [(d, v) for d, v in claims.get((row.hn, b), []) if day and (d, v) < (day, vn)]
        first = earlier[0] if earlier else None
        other = [(d, v) for (hn, ob), entries in claims.items() if hn == row.hn and ob != b for d, v in entries]
        row.extra = {"months": months, "age_text": f"{r.get('age_y')} ปี {r.get('age_m') or 0} เดือน" if months is not None else "",
                     "band": band.label, "adp": band.adp, "labs": {"Hb": labs.get("Hb", ""), "Hct": labs.get("Hct", "")},
                     "first": first[0].isoformat() if first else "", "first_vn": first[1] if first else "",
                     "other_band": min(other)[0].isoformat() if other else ""}
        billed = row.qty > 0
        row.count = len(earlier) + 1 if billed else None

        if not band.adp:
            row.add(ERROR, "NO_ADP", "ยังไม่ได้ตั้งรหัส ADP ของบริการนี้ (PPFS_ANEMIA_CHILD_ADP) HOSxP ผูกไว้เป็น 10000 = NONE")
        if text(r.get("nationality")) != "99":
            row.add(ERROR, "NOT_THAI", "ไม่ใช่คนไทย (สัญชาติไม่ใช่ 99)")
        if text(r.get("an")):
            row.add(ERROR, "IPD", "visit นี้ Admit แล้ว (มี AN)")
        self_paid_error(row)
        low, high = band.months
        if months is not None and not low <= months <= high:
            row.add(ERROR, "AGE", f"อายุ {row.extra['age_text']} ค่าบริการช่วง {band.label} เบิกไม่ได้")
        if billed and first:
            row.add(ERROR, "DONE_BEFORE", f"เคยเบิกช่วง {band.label} แล้วเมื่อ {_thai(first[0])} (VN {first[1]}) ช่วงอายุละ 1 ครั้ง")
        if billed and not (labs.get("Hb") or labs.get("Hct")):
            row.add(WARN, "NO_LAB", "ไม่พบผล Hb หรือ Hct ใน visit นี้")
        if billed and (row.qty != 1 or row.amount != RATE):
            row.add(WARN, "PRICE", f"ยอด {row.amount} บาท (จำนวน {row.qty:g}) ไม่ตรงอัตรา {RATE} บาท/ครั้ง")
        row.sent = sent.get((vn, row.code), "")
    return sorted(rows.values(), key=lambda x: (x.date, x.time, x.vn))


def _thai(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}/{day.year + 543}"


def detail(q: Select, vn: str, band_list: list[Band]) -> dict:
    visit = (q(f"""
        SELECT o.vn, o.hn, o.vstdate, p.nationality, v.age_y,
          (SELECT n.name FROM nationality n WHERE n.nationality=p.nationality) nationality_name
        FROM ovst o JOIN patient p ON p.hn=o.hn LEFT JOIN vn_stat v ON v.vn=o.vn WHERE o.vn={lit(vn)}""") or [None])[0]
    if visit is None:
        return {}
    claims = q(f"""
        SELECT o.vn, o.vstdate, v.age_y, v.age_m, oo.icode, SUM(oo.qty) qty, SUM(IF(IFNULL(oo.qty,0)=0,0,IFNULL(oo.sum_price,0))) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn LEFT JOIN vn_stat v ON v.vn=o.vn
        WHERE o.hn={lit(visit["hn"])} AND oo.icode IN ({lit_list(all_icodes(band_list))})
        GROUP BY o.vn, oo.icode ORDER BY o.vstdate, o.vn""")
    for c in claims:
        band = band_list[_band_of(text(c["icode"]), band_list)]
        c["band"], c["adp"] = band.label, band.adp
    items = q(f"""
        SELECT oo.icode, CONCAT_WS(' ',d.name,d.strength) name, d.nhso_adp_code adp, SUM(oo.qty) qty,
          SUM(IF(IFNULL(oo.qty,0)=0,0,IFNULL(oo.sum_price,0))) amount, EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode) is_drug
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn={lit(vn)}
        GROUP BY oo.icode ORDER BY d.nhso_adp_code IS NULL, oo.icode""")
    dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={lit(vn)} ORDER BY diagtype, icd10")
    return {"visit": visit, "claims": claims, "labs": _labs(q, sorted({text(c["vn"]) for c in claims} | {vn})),
            "items": items, "dx": dx}
