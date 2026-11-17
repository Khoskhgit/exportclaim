"""PPFS 12) บริการการตรวจคัดกรองมะเร็งปากมดลูก - claimed on the Krungthai Digital Health Platform (KTB), not e-Claim.

KTB takes the same 16 files as e-Claim and knows the activity and its result from ADP TYPE + CODE ("รายละเอียดของไฟล์
การนำเข้าข้อมูลผลการเข้ารักษา 16 แฟ้ม", 4 Sep 2026):
   P51 HPV DNA test, TYPE 4, 14-type kit: 1B0046_0 negative, 1B0046_1 HPV 16/18, 1B0046_2 other high risk (370 baht)
   P53 Pap smear,    TYPE 3: 1B004N negative, 1B004P positive (250 baht)
HOSxP bills the test as a lab item (HPV 38608 / Pap 38302, TYPE 15); the ZIP sends the KTB code chosen from the lab
result (lab_items 642 HPV DNA test / 218 PAP smear) instead. The user chose (2026-11-17): a ZIP to upload to KTB by
hand (no FDH), UC rights only (UCS/WEL), the 14-type kit, other items of the visit unticked by default, ICD-10 as keyed.

Target: Thai women 30-59 (15-29 when at high risk, which HOSxP cannot show: a warning), once every 5 years.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from decimal import Decimal

from .nhso18 import lit, lit_list
from .ppfs import ERROR, SELF_PAID, WARN, Row, Select, _day, _money, self_paid_error
from .validate import text

KEY = "cervix"
HPV_LAB, PAP_LAB = 642, 218
HPV_NEG, HPV_1618, HPV_OTHER = "1B0046_0", "1B0046_1", "1B0046_2"
PAP_NEG, PAP_POS = "1B004N", "1B004P"
CODES = {
    HPV_NEG: ("HPV DNA ผลลบ (14 สายพันธุ์)", Decimal("370")),
    HPV_1618: ("HPV DNA พบ 16/18 (14 สายพันธุ์)", Decimal("370")),
    HPV_OTHER: ("HPV DNA พบ HR อื่น (14 สายพันธุ์)", Decimal("370")),
    PAP_NEG: ("Pap smear ผลลบ", Decimal("250")),
    PAP_POS: ("Pap smear ผลบวก", Decimal("250")),
}
ADP_TYPE = {HPV_NEG: "4", HPV_1618: "4", HPV_OTHER: "4", PAP_NEG: "3", PAP_POS: "3"}
AGE, RISK_AGE = (30, 59), (15, 29)
YEARS = 5
UC = "ptt.hipdata_code IN ('UCS','WEL')"
SCREEN_DX = "^(Z124|Z014)"


def hpv_code(result: str) -> str | None:
    """The KTB code of an HPV DNA result: 'Negative', 'HPV Type 16', 'HPV Type non 16,18', 'HPV Type 18 ,39,35' ..."""
    value = text(result).lower()
    if not value:
        return None
    if re.search(r"neg|not detect|ไม่พบ", value):
        return HPV_NEG
    if "non" in value or "other" in value:
        return HPV_OTHER
    if re.search(r"(?<!\d)(16|18)(?!\d)", value):
        return HPV_1618
    if re.search(r"\d|pos|detect|พบ", value):
        return HPV_OTHER
    return None


def pap_code(result: str) -> str | None:
    """The KTB code of a Pap result; 'Image' and 'Out Lab' (how this hospital keys it) give none."""
    value = text(result).lower()
    if re.search(r"nilm|negative|normal|ปกติ|ไม่พบ", value):
        return PAP_NEG
    if re.search(r"asc|lsil|hsil|agc|ais|carcinoma|abnormal|positive|ผิดปกติ", value):
        return PAP_POS
    return None


def test_icodes(q: Select) -> dict[str, int]:
    """icode -> lab_items_code of the HPV and Pap tests."""
    return {text(r["icode"]): int(r["lab_items_code"]) for r in q(
        f"SELECT lab_items_code, icode FROM lab_items WHERE lab_items_code IN ({HPV_LAB},{PAP_LAB}) AND IFNULL(icode,'')<>''")}


VISIT_COLS = """o.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name, p.cid, p.sex, p.nationality,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, v.age_y"""
VISIT_JOINS = "JOIN patient p ON p.hn=o.hn JOIN pttype ptt ON ptt.pttype=o.pttype LEFT JOIN vn_stat v ON v.vn=o.vn"


def _results(q: Select, vns: list[str]) -> dict[tuple[str, int], str]:
    if not vns:
        return {}
    out: dict[tuple[str, int], str] = {}
    for r in q(f"""
            SELECT lh.vn, lo.lab_items_code code, lo.lab_order_result result FROM lab_head lh
            JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number
            WHERE lh.vn IN ({lit_list(vns)}) AND lo.lab_items_code IN ({HPV_LAB},{PAP_LAB})
            ORDER BY lh.vn, IFNULL(lo.lab_order_result,'')=''"""):
        out.setdefault((text(r["vn"]), int(r["code"])), text(r["result"]))
    return out


def fetch(q: Select, start: date, end: date) -> dict:
    icodes = test_icodes(q)
    if not icodes:
        return {"items": [], "history": [], "results": {}, "dx": set(), "icodes": icodes}
    items = q(f"""
        SELECT {VISIT_COLS}, oo.icode, oo.paidst, CONCAT_WS(' ',d.name,d.strength) item_name, d.nhso_adp_code adp,
          oo.qty, IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode {VISIT_JOINS}
        WHERE o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND IFNULL(o.an,'')='' AND {UC}
          AND oo.icode IN ({lit_list(icodes)})""")
    hns = sorted({text(r["hn"]) for r in items})
    since = date(start.year - YEARS, start.month, min(start.day, 28))
    history = q(f"""
        SELECT DISTINCT oo.vn, o.hn, o.vstdate FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn
        WHERE o.hn IN ({lit_list(hns)}) AND o.vstdate BETWEEN {lit(since)} AND {lit(end)}
          AND oo.icode IN ({lit_list(icodes)}) AND IFNULL(oo.qty,0)>0""") if hns else []
    vns = sorted({text(r["vn"]) for r in items})
    dx = {text(r["vn"]) for r in q(
        f"SELECT DISTINCT vn FROM ovstdiag WHERE vn IN ({lit_list(vns)}) AND icd10 REGEXP {lit(SCREEN_DX)}")} if vns else set()
    return {"items": items, "history": history, "results": _results(q, vns), "dx": dx, "icodes": icodes}


def check(data: dict, sent: dict[tuple[str, str], str] | None = None) -> list[Row]:
    sent = sent or {}
    by_test: dict[tuple[str, int], dict] = {}
    for r in data["items"]:
        lab = data["icodes"].get(text(r["icode"]))
        key = (text(r["vn"]), lab)
        found = by_test.get(key)
        if found is None:
            by_test[key] = {**r, "qty": Decimal(str(r.get("qty") or 0)), "amount": _money(r.get("amount")),
                            "self_paid": text(r.get("paidst")) == SELF_PAID}
        else:
            found["qty"] += Decimal(str(r.get("qty") or 0))
            found["amount"] += _money(r.get("amount"))
            found["self_paid"] |= text(r.get("paidst")) == SELF_PAID

    done: dict[str, list[tuple[date, str]]] = {}
    for h in data["history"]:
        if day := _day(h["vstdate"]):
            done.setdefault(text(h["hn"]), []).append((day, text(h["vn"])))
    for v in done.values():
        v.sort()

    rows = []
    for (vn, lab), r in by_test.items():
        result = data["results"].get((vn, lab), "")
        method = "HPV" if lab == HPV_LAB else "Pap"
        code = (hpv_code if lab == HPV_LAB else pap_code)(result)
        row = Row(key=f"{vn}|{code or method}", vn=vn, code=code or method, item_name=text(r.get("item_name")))
        row.qty, row.amount, row.self_paid = r["qty"], r["amount"], r["self_paid"]
        day = _day(r.get("vstdate"))
        row.hn, row.date, row.time = text(r.get("hn")), day.isoformat() if day else "", str(r.get("vsttime") or "")[:8]
        row.ptname, row.cid = text(r.get("ptname")), text(r.get("cid"))
        row.pttype = f"{text(r.get('pttype'))}: {text(r.get('pttype_name'))}".strip(": ")
        age = r.get("age_y")
        earlier = [(d, v) for d, v in done.get(row.hn, []) if v != vn and day and d < day and (day - d).days < YEARS * 365.25]
        last = earlier[-1] if earlier else None
        row.extra = {"age": int(age) if age is not None else None, "method": method, "result": result, "hosxp_adp": text(r.get("adp")),
                     "adp_type": ADP_TYPE.get(code or "", ""), "dx": vn in data["dx"],
                     "last": last[0].isoformat() if last else "", "last_vn": last[1] if last else ""}
        if text(r.get("sex")) != "2":
            row.add(ERROR, "NOT_FEMALE", "ไม่ใช่เพศหญิง")
        if row.qty <= 0:
            row.add(ERROR, "NO_ITEM", "จำนวนเป็น 0 (ไม่ได้คิดค่าตรวจ)")
        if code is None:
            shown = f'"{result}" ' if result else "(ว่าง) "
            row.add(ERROR, "NO_RESULT", f"ผล {method} ใน HOSxP {shown}แปลเป็นรหัส KTB ไม่ได้"
                    f"{' (ต้องลงผลเป็น Negative / HPV Type …)' if lab == HPV_LAB else ' (ต้องลงผลเป็น NILM/Negative หรือชนิดความผิดปกติ)'}")
        self_paid_error(row)
        if age is not None and not RISK_AGE[0] <= int(age) <= AGE[1]:
            row.add(ERROR, "AGE", f"อายุ {int(age)} ปี อยู่นอก {RISK_AGE[0]}–{AGE[1]} ปี")
        if last:
            row.add(ERROR, "WITHIN_5Y", f"คัดกรองแล้วเมื่อ {last[0].isoformat()} (VN {last[1]}) ยังไม่ครบ {YEARS} ปี")
        if age is not None and RISK_AGE[0] <= int(age) <= RISK_AGE[1]:
            row.add(WARN, "RISK_AGE", f"อายุ {int(age)} ปี เบิกได้เฉพาะกลุ่มเสี่ยงสูง (มีเพศสัมพันธ์ตั้งแต่อายุน้อย มีคู่นอนหลายคน ไม่ใช้ถุงยาง)")
        if text(r.get("nationality")) != "99":
            row.add(WARN, "NOT_THAI", "ไม่ใช่คนไทย (สัญชาติไม่ใช่ 99)")
        if vn not in data["dx"]:
            row.add(WARN, "NO_DX", "ไม่มีรหัสวินิจฉัย Z12.4 / Z01.4")
        if code == HPV_1618:
            row.add(WARN, "REFER_COLPO", "พบ HPV 16/18 ต้องนัดตรวจ Colposcope")
        elif code == HPV_OTHER:
            row.add(WARN, "REFER_LBC", "พบ HPV HR ชนิดอื่น ต้องนัดตรวจ Liquid based cytology")
        row.sent = sent.get((vn, row.code), "")
        rows.append(row)
    return sorted(rows, key=lambda x: (x.date, x.time, x.vn, x.code))


def rewrite_adp(data: dict[str, list[dict]], claimed: dict[str, set[str]], hosxp_codes: dict[str, str]) -> None:
    """Send the KTB code chosen from the result in place of HOSxP's lab code (38608 / 38302), as KTB's ADP TYPE."""
    for r in data.get("ADP", []):
        vn = text(r.get("SEQ"))
        for code in claimed.get(vn, set()):
            if text(r.get("CODE")) == hosxp_codes.get(f"{vn}|{code}"):
                r["CODE"], r["TYPE"] = code, ADP_TYPE[code]
                break


def detail(q: Select, vn: str) -> dict:
    visit = (q(f"""
        SELECT o.vn, o.hn, o.vstdate, p.nationality,
          (SELECT n.name FROM nationality n WHERE n.nationality=p.nationality) nationality_name
        FROM ovst o JOIN patient p ON p.hn=o.hn WHERE o.vn={lit(vn)}""") or [None])[0]
    if visit is None:
        return {}
    icodes = test_icodes(q)
    tests = q(f"""
        SELECT o.vn, o.vstdate, oo.icode, CONCAT_WS(' ',d.name,d.strength) name, SUM(oo.qty) qty,
          SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode
        WHERE o.hn={lit(visit["hn"])} AND oo.icode IN ({lit_list(icodes)})
        GROUP BY o.vn, oo.icode ORDER BY o.vstdate DESC, o.vn""") if icodes else []
    results = _results(q, sorted({text(t["vn"]) for t in tests}))
    items = q(f"""
        SELECT oo.icode, CONCAT_WS(' ',d.name,d.strength) name, d.nhso_adp_code adp, SUM(oo.qty) qty,
          SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn={lit(vn)}
        GROUP BY oo.icode ORDER BY d.nhso_adp_code IS NULL, oo.icode""")
    dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={lit(vn)} ORDER BY diagtype, icd10")
    return {"visit": visit, "tests": tests, "results": results, "icodes": icodes, "items": items, "dx": dx}


# ------------------------------------------------------------------ KTB eligibility check file (Health Platform Format)
KTB_HEADER = ["HCODE", "CID", "NAME", "MIDDLENAME", "LASTNAME", "BIRTHDAYDATE", "NATION", "WORKPERMIT", "PASSPORTNUMBER"]


def _name(value: str) -> str:
    """KTB takes Thai, English and digits only (spaces inside a name are kept)."""
    return re.sub(r"[^0-9A-Za-z฀-๿ ]", "", text(value)).strip()[:100]


def ktb_csv(q: Select, hcode: str, hns: list[str]) -> tuple[bytes, int, int]:
    """The 9-column CSV for ตรวจสอบสิทธิประโยชน์แบบกลุ่ม: Thai patients only (others need a passport or work permit
    KTB checks against its own nation list). Returns (UTF-8 bytes, rows written, people left out)."""
    people = q(f"""
        SELECT p.hn, p.cid, p.fname, p.lname, p.birthday, p.nationality FROM patient p
        WHERE p.hn IN ({lit_list(sorted(set(hns)))})""") if hns else []
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(KTB_HEADER)
    written = skipped = 0
    seen = set()
    for p in people:
        cid, born = re.sub(r"\D", "", text(p["cid"])), _day(p.get("birthday"))
        if text(p.get("nationality")) != "99" or len(cid) != 13 or not born or cid in seen:
            skipped += 1
            continue
        seen.add(cid)
        writer.writerow([hcode, cid, _name(p["fname"]), "", _name(p["lname"]), born.strftime("%Y%m%d"), "THA", "", ""])
        written += 1
    return out.getvalue().encode("utf-8"), written, skipped
