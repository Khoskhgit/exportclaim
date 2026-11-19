"""NTIP: TB screening of high-risk groups (บริการคัดกรองและค้นหาวัณโรคในกลุ่มเสี่ยงสูง), all rights.

Two parts, as the user asked (2026-11-19):
  screening  the visits coded Z11.1 (screening for respiratory TB), exported in the layout of NTIP Plus's
             Template_ImportDataNTIPPlus.xlsx (sheet TEMPLATE-IMPORT, 32 columns, dates YYYYMMDD and CID as text).
             HOSxP has no risk group and the CXR report text is empty, so the risk group (guessed from the visit's
             diagnoses and age), the symptom screen, the CXR result, DX and the extra fields some groups need are
             picked per visit on the page and kept in the app.
  labs       the TB labs (Molecular, AFB, Culture) checked against the payment rules: a molecular test once a year
             (TB-LAMP 200, Xpert MTB/RIF 500, MTB/MDR 600, LPA 600; a drug-resistance test after a positive TB-LAMP is
             allowed), the second-line test 700 only after first-line resistance, AFB 20 at most 3 a year, culture once a
             year (solid 200, liquid 300, picked per order). A year is the fiscal year (October to September).
HOSxP is only read.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable, Iterable

from .nhso18 import lit, lit_list
from .validate import text

Select = Callable[[str], list[dict]]
ERROR, WARN = "error", "warn"
SCREEN_ICD = "^Z11\\.?1"
HBA1C_LAB = 198

# ------------------------------------------------------------------ the template's code lists (sheet MS_DATA)
COLUMNS = ["RISK_TYPE", "TITLE_ID", "FNAME", "LNAME", "CID", "GENDER", "BORN", "AGE_Y", "AGE_M", "ADDR", "MU",
           "PROVINCE_ID", "AMPHUR_ID", "TAMBOL_ID", "PEOPLE_TYPE", "RACE_ID", "HN", "INSCL_ID", "CONTACT_DATE",
           "SYMPTOM_SCREEN", "CXR_DATE", "CXR_RESULT", "CXR_ABNORMAL_RESULT", "DX", "TB_CID_INDEX", "ICD10", "HbA1C",
           "IMMUNNO_DISEASE", "B24", "HOSP_ID_PRISON", "PRISONDATE_STD", "PRISONDATE_END"]
RISKS = {
    "RST1": "ผู้สัมผัสใกล้ชิด (นอกบ้าน)**", "RST23": "ผู้สัมผัสร่วมบ้าน**", "RST35": "ผู้ป่วย B24 ที่สัมผัสร่วมบ้าน **",
    "RST36": "ผู้ป่วย B24 ที่สัมผัสใกล้ชิด (นอกบ้าน)**", "RST37": "ผู้ป่วย B24 ที่สัมผัสร่วมบ้านวัณโรคดื้อยา**",
    "RST38": "ผู้ป่วย B24 ที่สัมผัสใกล้ชิดวัณโรคดื้อยา**", "RST24": "ผู้สัมผัสร่วมบ้านวัณโรคดื้อยา**",
    "RST2": "ผู้สัมผัสใกล้ชิดวัณโรคดื้อยา**", "RST20": "ผู้ป่วยผ่าตัดกระเพาะ ตัดต่อลำใส้", "RST21": "อื่นๆ",
    "RST22": "ผู้ป่วยโรคมะเร็ง", "RST15": "ผู้มีประวัติเป็นวัณโรค", "RST18": "ผู้ป่วย Silicosis",
    "RST19": "ผู้ป่วยโรคไตเรื้อรัง**", "RST4": "โรคปอดอักเสบจากฝุ่นทราย", "RST42": "ผู้ป่วยจิตเวช", "RST3": "ผู้ป่วย B24**",
    "RST30": "โรคเบาหวานที่คุมไม่ได้ (HbA1C ≥ 7 mg% หรือ FBS**", "RST31": "ผู้ติดยาเสพติด**", "RST32": "ผู้ที่สูบบุหรี่**",
    "RST33": "ผู้ที่ได้รับยา Anti-TNF", "RST34": "ผู้ป่วยปลูกถ่ายอวัยวะ", "RST5": "โรคเบาหวาน**",
    "RST6": "โรคปอดอุดกั้นเรื้อรัง (COPD)**", "RST7": "ผู้มีความผิดปกติจากการติดสุรา**", "RST8": "ผู้ป่วยทุพโภชนาการ",
    "RST9": "โรคที่ได้รับยากดภูมิคุ้มกัน**", "RST16": "อื่นๆ (กลุ่มประชากรกลุ่มเสี่ยง)", "RST10": "ผู้สูงอายุ",
    "RST11": "บุคลากรสาธารณสุขดูแลผู้ป่วย**", "RST12": "ผู้อาศัยในชุมชนแออัด/ค่ายอพยพ", "RST14": "ประชากรข้ามชาติ",
    "RST26": "ผู้ที่อยู่สถานสงเคราะห์/ไร้ที่พึ่ง**", "RST27": "คนเร่ร่อนไร้ที่อยู่", "RST28": "ผู้ป่วยติดบ้านติดเตียง",
    "RST29": "ผู้อาศัยในสถานพินิจ**", "RST43": "ผู้ต้องขังในเรือนจำ (รายเก่า) **", "RST44": "ผู้ต้องขังเรือนจำ (ก่อนพ้นโทษ) **",
    "RST13": "ผู้ต้องขังในเรือนจำ (รายใหม่) **",
}
CONTACT_RISKS = {"RST1", "RST23", "RST35", "RST36", "RST37", "RST38", "RST24", "RST2"}
PRISON_RISKS = {"RST13", "RST43", "RST44"}
TITLES = {"ด.ช.": "ด.ช.", "ด.ญ.": "ด.ญ.", "น.ส.": "น.ส.", "นางสาว": "น.ส.", "นาง": "นาง", "นาย": "นาย",
          "เด็กชาย": "ด.ช.", "เด็กหญิง": "ด.ญ."}
RACES = {"44": "6=จีน", "45": "7=อินเดีย", "46": "4=เวียดนาม", "48": "1=พม่า", "50": "5=มาเลเซีย", "52": "8=ปากีสถาน",
         "56": "3=ลาว", "57": "2=กัมพูชา", "99": "0=ไทย"}
OTHER_RACE = "9=อื่นๆ"
INSCL = {"UCS": "สิทธิหลักประกันสุขภาพถ้วนหน้า", "WEL": "สิทธิหลักประกันสุขภาพถ้วนหน้า", "SSS": "สิทธิประกันสังคม",
         "SSI": "สิทธิประกันสังคม", "OFC": "ข้าราชการ/รัฐวิสาหกิจ", "LGO": "ข้าราชการ/รัฐวิสาหกิจ",
         "BKK": "ข้าราชการ/รัฐวิสาหกิจ", "BMT": "ข้าราชการ/รัฐวิสาหกิจ", "NRD": "ต่างด้าว", "NRH": "ต่างด้าว"}
SYMPTOMS = {"01": "สงสัยวัณโรค (คะแนน >=3)", "02": "ไม่สงสัยวัณโรค (คะแนน <3)"}
CXR_RESULTS = {"01": "Normal", "02": "Abnormal"}
CXR_ABNORMAL = {"01": "N/A", "02": "Cavity", "03": "No Cavity", "04": "Miliary Pattern", "05": "Abnormal Non-TB"}
DXS = {"01": "Normal", "03": "TB", "07": "ABNormal non-TB"}

# The fields picked per visit on the page, with what each may hold.
CHOICES: dict[str, dict | str] = {
    "risk": RISKS, "symptom": SYMPTOMS, "cxr_result": {**CXR_RESULTS, "": ""}, "cxr_abnormal": {**CXR_ABNORMAL, "": ""},
    "dx": DXS, "index_cid": r"\d{13}|", "icd10": r"[A-Z]\d{2,4}|", "hba1c": r"\d{1,2}(\.\d{1,2})?|", "cd4": r"\d{1,5}|",
    "immuno": r".{0,50}", "prison_hosp": r"\d{5}|", "prison_start": r"\d{4}-\d{2}-\d{2}|", "prison_end": r"\d{4}-\d{2}-\d{2}|",
}

# Guessing the risk group from the visit's diagnoses (first match wins), then age 65+, then nationality.
RISK_BY_ICD = [
    (r"^B2[0-4]", "RST3"), (r"^Z20\.?1", "RST23"), (r"^E1[0-4]", "RST5"), (r"^J44", "RST6"), (r"^N18", "RST19"),
    (r"^F10", "RST7"), (r"^F1[1-69]", "RST31"), (r"^(F17|Z72\.?0)", "RST32"), (r"^C[0-8]|^C9[0-7]", "RST22"),
    (r"^(B90|Z86\.?1)", "RST15"), (r"^J62", "RST18"), (r"^F[23]", "RST42"), (r"^E4[0-6]", "RST8"),
]
TB_ICD = r"^A1[5-9]"


class ChoiceError(ValueError):
    pass


def valid_choice(name: str, value: str) -> str:
    rule = CHOICES.get(name)
    if rule is None:
        raise ChoiceError("ไม่รู้จักช่องนี้")
    value = text(value).strip()
    if isinstance(rule, dict):
        if value not in rule:
            raise ChoiceError("ค่าที่เลือกไม่อยู่ในรายการของ NTIP")
    elif not re.fullmatch(rule, value):
        raise ChoiceError({"index_cid": "เลขบัตรประชาชน 13 หลัก", "icd10": "รหัส ICD-10 เช่น E119", "hba1c": "ตัวเลข เช่น 8.5",
                           "cd4": "จำนวนเต็ม (ไม่ใส่ - * / .)", "prison_hosp": "รหัสหน่วยบริการเรือนจำ 5 หลัก",
                           "immuno": "ไม่เกิน 50 ตัวอักษร"}.get(name, "วันที่ไม่ถูกต้อง"))
    return value


def fiscal_start(day: date) -> date:
    return date(day.year if day.month >= 10 else day.year - 1, 10, 1)


def ymd(value) -> str:
    raw = str(value or "")[:10]
    return raw.replace("-", "") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) else ""


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def age(born: date | None, on: date | None) -> tuple[int | None, int | None]:
    if not born or not on:
        return None, None
    months = (on.year - born.year) * 12 + on.month - born.month - (on.day < born.day)
    return months // 12, months % 12


def guess_risk(icds: Iterable[str], years: int | None, nationality: str) -> tuple[str, str]:
    """(RISK_TYPE, the ICD-10 that gave it, '' for age or nationality)."""
    icds = [text(i).upper() for i in icds]
    for pattern, risk in RISK_BY_ICD:
        for icd in icds:
            if re.match(pattern, icd):
                return risk, icd
    if years is not None and years >= 65:
        return "RST10", ""
    if nationality and nationality != "99":
        return "RST14", ""
    return "", ""


# ------------------------------------------------------------------ HOSxP
def fetch_screening(q: Select, start: date, end: date) -> dict:
    """The Z11.1 visits from the fiscal year's start (to count repeats) to end, with what the export needs."""
    since = fiscal_start(start)
    visits = q(f"""
        SELECT o.vn, o.hn, o.vstdate, p.pname, p.fname, p.lname, p.cid, p.sex, p.birthday, p.addrpart, p.moopart,
          p.chwpart, p.amppart, p.tmbpart, p.nationality, ptt.hipdata_code, ptt.name pttype_name, o.pttype
        FROM ovst o JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype
        WHERE o.vstdate BETWEEN {lit(since)} AND {lit(end)}
          AND o.vn IN (SELECT d.vn FROM ovstdiag d WHERE d.vstdate BETWEEN {lit(since)} AND {lit(end)} AND d.icd10 REGEXP {lit(SCREEN_ICD)})
        ORDER BY o.vstdate, o.vn""")
    vns = [text(v["vn"]) for v in visits]
    hns = sorted({text(v["hn"]) for v in visits})
    dx: dict[str, list[str]] = {}
    cxr: dict[str, dict] = {}
    labs: dict[str, list[dict]] = {}
    if vns:
        for r in q(f"SELECT vn, icd10 FROM ovstdiag WHERE vn IN ({lit_list(vns)}) ORDER BY diagtype, icd10"):
            dx.setdefault(text(r["vn"]), []).append(text(r["icd10"]))
        for r in q(f"""
            SELECT r.vn, r.xray_items_code, i.xray_items_name name, IFNULL(r.examined_date, r.request_date) day,
              r.confirm, LEFT(r.report_text, 4000) report FROM xray_report r JOIN xray_items i ON i.xray_items_code=r.xray_items_code
            WHERE r.vn IN ({lit_list(vns)}) AND i.xray_items_name REGEXP '^ *chest' ORDER BY r.vn, day"""):
            cxr.setdefault(text(r["vn"]), dict(r))
        for r in q(f"""
            SELECT lh.hn, lh.order_date day, lo.lab_items_code code, i.lab_items_name name, lo.lab_order_result result
            FROM lab_head lh JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number
            JOIN lab_items i ON i.lab_items_code=lo.lab_items_code
            WHERE lh.hn IN ({lit_list(hns)}) AND lh.order_date BETWEEN {lit(date(since.year - 1, since.month, 1))} AND {lit(end)}
              AND (lo.lab_items_code={HBA1C_LAB} OR i.lab_items_name REGEXP 'CD4') AND IFNULL(lo.lab_order_result,'')<>''
            ORDER BY lh.order_date"""):
            labs.setdefault(text(r["hn"]), []).append(dict(r))
    return {"visits": visits, "dx": dx, "cxr": cxr, "labs": labs, "start": start, "end": end}


@dataclass
class Screen:
    vn: str
    hn: str
    date: str
    ptname: str = ""
    cid: str = ""
    pttype: str = ""
    nth: int = 1
    values: dict = field(default_factory=dict)  # the template's columns
    picked: dict = field(default_factory=dict)  # what the page saved for the visit
    guess: dict = field(default_factory=dict)  # what the app filled in by itself
    dx: list = field(default_factory=list)
    cxr: dict = field(default_factory=dict)
    exported: str = ""
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def add(self, level: str, code: str, message: str) -> None:
        (self.errors if level == ERROR else self.warnings).append({"code": code, "message": message})


def _last_lab(labs: list[dict], on: date | None, test: Callable[[dict], bool]) -> str:
    """The newest result up to the day (within a year) that passes the test, as a number string."""
    found = ""
    for r in labs:
        day = _day(r["day"])
        if test(r) and day and on and day <= on and (on - day).days <= 365:
            match = re.search(r"\d+(\.\d+)?", text(r["result"]))
            if match:
                found = match.group(0)
    return found


def check_screening(data: dict, picked: dict[str, dict], exported: dict[str, str] | None = None) -> list[Screen]:
    exported = exported or {}
    seen: dict[str, int] = {}
    out = []
    for v in data["visits"]:
        vn, hn = text(v["vn"]), text(v["hn"])
        day = _day(v["vstdate"])
        seen[hn] = seen.get(hn, 0) + 1
        if not day or day < data["start"]:
            continue
        mine = picked.get(vn, {})
        born = _day(v.get("birthday"))
        years, months = age(born, day)
        nationality = text(v.get("nationality"))
        icds = data["dx"].get(vn, [])
        risk_guess, risk_icd = guess_risk(icds, years, nationality)
        cxr = data["cxr"].get(vn, {})
        labs = data["labs"].get(hn, [])
        hba1c = _last_lab(labs, day, lambda r: r["code"] == HBA1C_LAB)
        cd4 = _last_lab(labs, day, lambda r: "CD4" in text(r["name"]).upper())
        guess = {"risk": risk_guess, "symptom": "02", "cxr_result": "01" if cxr else "", "cxr_abnormal": "",
                 "dx": "03" if any(re.match(TB_ICD, i.upper()) for i in icds) else "01", "index_cid": "",
                 "icd10": risk_icd if risk_guess in ("RST5", "RST7", "RST30") else "", "hba1c": hba1c, "cd4": cd4,
                 "immuno": "", "prison_hosp": "", "prison_start": "", "prison_end": ""}
        pick = {k: mine[k] if k in mine else g for k, g in guess.items()}
        if pick["cxr_result"] != "02":
            pick["cxr_abnormal"] = ""
        risk = pick["risk"]
        s = Screen(vn=vn, hn=hn, date=day.isoformat(), ptname=" ".join(x for x in (text(v.get("pname")) + text(v.get("fname")), text(v.get("lname"))) if x),
                   cid=text(v.get("cid")), pttype=f"{text(v.get('pttype'))}: {text(v.get('pttype_name'))}".strip(": "),
                   nth=seen[hn], picked=pick, guess=guess, dx=icds, exported=exported.get(vn, ""),
                   cxr={"name": text(cxr.get("name")), "date": str(cxr.get("day") or "")[:10], "report": text(cxr.get("report"))} if cxr else {})
        chw, amp, tmb = text(v.get("chwpart")), text(v.get("amppart")), text(v.get("tmbpart"))
        sex = text(v.get("sex"))
        pname = text(v.get("pname"))
        s.values = {
            "RISK_TYPE": risk, "TITLE_ID": TITLES.get(pname, "ไม่ระบุ (หญิง)" if sex == "2" else "ไม่ระบุ (ชาย)"),
            "FNAME": text(v.get("fname")), "LNAME": text(v.get("lname")), "CID": s.cid,
            "GENDER": {"1": "M", "2": "F"}.get(sex, ""), "BORN": ymd(born), "AGE_Y": "" if years is None else str(years),
            "AGE_M": "" if months is None else str(months), "ADDR": text(v.get("addrpart"))[:100], "MU": text(v.get("moopart"))[:2],
            "PROVINCE_ID": chw, "AMPHUR_ID": chw + amp if chw and amp else "", "TAMBOL_ID": chw + amp + tmb if chw and amp and tmb else "",
            "PEOPLE_TYPE": "ไทย" if nationality == "99" else "ไม่ใช่คนไทย", "RACE_ID": RACES.get(nationality, OTHER_RACE), "HN": hn,
            "INSCL_ID": INSCL.get(text(v.get("hipdata_code")).upper(), ""), "CONTACT_DATE": ymd(day),
            "SYMPTOM_SCREEN": SYMPTOMS.get(pick["symptom"], ""), "CXR_DATE": ymd(cxr.get("day")) if pick["cxr_result"] else "",
            "CXR_RESULT": CXR_RESULTS.get(pick["cxr_result"], ""), "CXR_ABNORMAL_RESULT": CXR_ABNORMAL.get(pick["cxr_abnormal"], ""),
            "DX": DXS.get(pick["dx"], ""), "TB_CID_INDEX": pick["index_cid"] if risk in CONTACT_RISKS else "",
            "ICD10": pick["icd10"] if risk in ("RST5", "RST7", "RST30") else "", "HbA1C": pick["hba1c"] if risk in ("RST5", "RST30") else "",
            "IMMUNNO_DISEASE": pick["immuno"] if risk == "RST9" else "", "B24": pick["cd4"] if risk in ("RST3", "RST35", "RST36", "RST37", "RST38") else "",
            "HOSP_ID_PRISON": pick["prison_hosp"] if risk in PRISON_RISKS else "",
            "PRISONDATE_STD": ymd(pick["prison_start"]) if risk in PRISON_RISKS else "",
            "PRISONDATE_END": ymd(pick["prison_end"]) if risk in PRISON_RISKS else "",
        }
        _check_screen(s, bool(cxr))
        out.append(s)
    return out


def _check_screen(s: Screen, has_cxr: bool) -> None:
    v, risk = s.values, s.values["RISK_TYPE"]
    if not risk:
        s.add(ERROR, "NO_RISK", "ยังไม่ได้เลือกกลุ่มเสี่ยง (เดาจากรหัสโรคและอายุไม่ได้)")
    if not re.fullmatch(r"\d{13}", s.cid):
        s.add(ERROR, "CID", "เลขบัตรประชาชนไม่ครบ 13 หลัก")
    missing = [c for c in ("FNAME", "LNAME", "GENDER", "BORN", "PROVINCE_ID", "AMPHUR_ID", "TAMBOL_ID", "INSCL_ID") if not v[c]]
    if missing:
        s.add(ERROR, "MISSING", f"ข้อมูลที่ต้องมีว่าง: {', '.join(missing)} (แก้ในทะเบียนผู้ป่วย HOSxP)")
    if risk in CONTACT_RISKS and not v["TB_CID_INDEX"]:
        s.add(ERROR, "INDEX_CID", "กลุ่มผู้สัมผัสต้องใส่เลขบัตรประชาชนของผู้ป่วยวัณโรค (INDEX)")
    if risk in ("RST7", "RST30") and not v["ICD10"]:
        s.add(ERROR, "ICD10", "กลุ่มนี้ต้องใส่ ICD-10")
    if risk == "RST30" and not v["HbA1C"]:
        s.add(ERROR, "HBA1C", "เบาหวานที่คุมไม่ได้ต้องใส่ค่า HbA1C")
    if risk == "RST9" and not v["IMMUNNO_DISEASE"]:
        s.add(ERROR, "IMMUNO", "ต้องใส่โรคที่ได้รับยากดภูมิคุ้มกัน")
    if v["B24"] == "" and risk in ("RST3", "RST35", "RST36", "RST37", "RST38"):
        s.add(ERROR, "CD4", "กลุ่ม B24 ต้องใส่ค่า CD4 (ไม่พบผล CD4 ใน 1 ปีก่อนคัดกรอง)")
    if risk in PRISON_RISKS and not v["HOSP_ID_PRISON"]:
        s.add(ERROR, "PRISON", "กลุ่มผู้ต้องขังต้องใส่รหัสหน่วยบริการเรือนจำ")
    if v["CXR_RESULT"] == "Abnormal" and not v["CXR_ABNORMAL_RESULT"]:
        s.add(ERROR, "CXR_ABNORMAL", "ผล CXR Abnormal ต้องเลือกผล Abnormal")
    if not has_cxr:
        s.add(WARN, "NO_CXR", "ไม่พบการถ่ายภาพรังสีทรวงอก (Chest) ใน visit นี้")
    limit = 3 if risk in PRISON_RISKS else 1
    if s.nth > limit:
        s.add(WARN, "REPEAT", f"คัดกรองครั้งที่ {s.nth} ในปีงบ (CXR เบิกได้{' 3 ครั้ง' if limit == 3 else 'ปีละครั้ง'})")
    if s.exported:
        s.add(WARN, "EXPORTED", "ส่งออกไฟล์ NTIP แล้ว")


def screening_xlsx(rows: list[Screen]) -> bytes:
    """Sheet TEMPLATE-IMPORT with the template's header row; every cell is text, as NTIP Plus needs."""
    import openpyxl
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "TEMPLATE-IMPORT"
    sheet.append(COLUMNS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for r in rows:
        sheet.append([text(r.values.get(c)) for c in COLUMNS])
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.number_format = "@"
    for i, name in enumerate(COLUMNS, 1):
        sheet.column_dimensions[get_column_letter(i)].width = max(10, len(name) + 2)
    sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# ------------------------------------------------------------------ TB labs and the payment rules
@dataclass(frozen=True)
class Method:
    key: str
    group: str  # molecular, second, afb, culture
    label: str
    rate: Decimal | None
    pattern: str


METHODS = [
    Method("second", "second", "Second-line (LPA FQ, AG/CP หรือ MTB/XDR)", Decimal(700), r"XDR|\bFQ\b|second.?line"),
    Method("lamp", "molecular", "TB-LAMP", Decimal(200), r"LAMP"),
    Method("xpert", "molecular", "Real-time PCR (Xpert MTB/RIF)", Decimal(500), r"xpert|gene\s*expert"),
    Method("lpa", "molecular", "Line Probe Assay (LPA)", Decimal(600), r"\bLPA\b|line\s*probe"),
    Method("mtbmdr", "molecular", "Real-time PCR MTB/MDR", Decimal(600), r"MDR|PCR.*\b(TB|MTB)\b"),
    Method("afb", "afb", "AFB", Decimal(20), r"sputum\s*afb"),
    Method("culture", "culture", "Culture", None, r"\bTB\s*culture|mycobac\w*\s*culture|culture.*\b(TB|mycobac)"),
]
CULTURE = {"solid": ("Solid media", Decimal(200)), "liquid": ("Liquid media", Decimal(300))}
LIMITS = {"molecular": 1, "afb": 3, "culture": 1}
GROUP_LABEL = {"molecular": "Molecular", "second": "Second-line", "afb": "AFB", "culture": "Culture"}
LAB_NAME_FILTER = "LAMP|xpert|gene ?expert|LPA|line ?probe|MDR|XDR|PCR.*(TB|MTB)|sputum ?afb|culture"


def method_of(name: str) -> Method | None:
    for m in METHODS:
        if re.search(m.pattern, name or "", re.I):
            return m
    return None


def positive(result: str) -> bool:
    r = text(result).lower()
    return bool(re.search(r"positive|scanty|\bdetect|พบเชื้อ", r)) and not re.search(r"not\s*detect|negative|no\s*afb|ไม่พบ", r)


def resistant(result: str) -> bool:
    r = text(result).lower()
    return bool(re.search(r"\brr\b|resist|rif\w*\s*(resistance\s*)?detected|ดื้อ", r)) and not re.search(r"not\s*detect|sensitive|ไม่ดื้อ", r)


def fetch_labs(q: Select, start: date, end: date) -> list[dict]:
    since = fiscal_start(start)
    return q(f"""
        SELECT lh.lab_order_number order_no, lo.lab_items_code code, i.lab_items_name name, lh.vn, lh.hn, lh.order_date day,
          lo.lab_order_result result, p.pname, p.fname, p.lname, p.cid, ptt.hipdata_code, IFNULL(o.pttype, ip.pttype) pttype,
          ptt.name pttype_name, IF(o.vn IS NULL AND ip.an IS NOT NULL, 'IPD', 'OPD') kind
        FROM lab_head lh JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number
        JOIN lab_items i ON i.lab_items_code=lo.lab_items_code JOIN patient p ON p.hn=lh.hn
        LEFT JOIN ovst o ON o.vn=lh.vn LEFT JOIN ipt ip ON ip.an=lh.vn
        LEFT JOIN pttype ptt ON ptt.pttype=IFNULL(o.pttype, ip.pttype)
        WHERE lh.order_date BETWEEN {lit(since)} AND {lit(end)} AND i.lab_items_name REGEXP {lit(LAB_NAME_FILTER)}
        ORDER BY lh.hn, lh.order_date, lh.order_time, lh.lab_order_number, lo.lab_items_code""")


@dataclass
class LabRow:
    key: str
    vn: str
    hn: str
    date: str
    code: str
    name: str
    result: str
    method: str
    group: str
    label: str
    ptname: str = ""
    cid: str = ""
    pttype: str = ""
    kind: str = "OPD"
    nth: int = 1
    rate: Decimal | None = None
    technique: str = ""
    claimable: bool = True
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def add(self, level: str, code: str, message: str) -> None:
        (self.errors if level == ERROR else self.warnings).append({"code": code, "message": message})


def check_labs(rows: list[dict], start: date, techniques: dict[str, str] | None = None) -> list[LabRow]:
    """Each TB lab order in the period with its method, rate and whether the payment rules allow it. Counts run per
    person over the fiscal year, from its first day, so orders before the period count too."""
    techniques = techniques or {}
    out: list[LabRow] = []
    state: dict[tuple[str, date], dict] = {}
    for r in rows:
        m = method_of(text(r["name"]))
        day = _day(r["day"])
        if m is None or day is None:
            continue
        hn = text(r["hn"])
        year = state.setdefault((hn, fiscal_start(day)), {"molecular": 0, "afb": 0, "culture": 0, "lamp_positive": False,
                                                          "resistance_test": False, "first_line_rr": False})
        key = f"{text(r['order_no'])}:{text(r['code'])}"
        row = LabRow(key=key, vn=text(r["vn"]), hn=hn, date=day.isoformat(), code=text(r["code"]), name=text(r["name"]),
                     result=text(r["result"]), method=m.key, group=m.group, label=m.label, rate=m.rate,
                     ptname=" ".join(x for x in (text(r.get("pname")) + text(r.get("fname")), text(r.get("lname"))) if x),
                     cid=text(r.get("cid")), pttype=f"{text(r.get('pttype'))}: {text(r.get('pttype_name'))}".strip(": "),
                     kind=text(r.get("kind")) or "OPD")
        if m.group == "molecular":
            if year["molecular"] >= 1 and year["lamp_positive"] and m.key != "lamp" and not year["resistance_test"]:
                year["resistance_test"] = True  # rule 3: after a positive TB-LAMP, one drug-resistance test
                row.add(WARN, "AFTER_LAMP", "ตรวจหาเชื้อดื้อยาหลังผล TB-LAMP บวก (เบิกได้ตามข้อ 3)")
            else:
                year["molecular"] += 1
                row.nth = year["molecular"]
                if row.nth > LIMITS["molecular"]:
                    row.claimable = False
                    row.add(ERROR, "OVER_LIMIT", f"Molecular ครั้งที่ {row.nth} ในปีงบ (เบิกได้ 1 ครั้งต่อคนต่อปี ไม่ว่าวิธีใด)")
            if m.key == "lamp" and positive(row.result):
                year["lamp_positive"] = True
                row.add(WARN, "LAMP_POSITIVE", "ผล TB-LAMP บวก ให้ตรวจหาเชื้อดื้อยา Rifampicin หรือ INH+Rifampicin ต่อ")
            if resistant(row.result):
                year["first_line_rr"] = True
        elif m.group == "second":
            if not year["first_line_rr"]:
                row.add(WARN, "NO_RR", "ไม่พบผลดื้อยา first-line ก่อนหน้าในปีงบ (Second-line เบิกได้เฉพาะ RR/MDR-TB)")
        else:
            year[m.group] += 1
            row.nth = year[m.group]
            if row.nth > LIMITS[m.group]:
                row.claimable = False
                row.add(ERROR, "OVER_LIMIT", f"{GROUP_LABEL[m.group]} ครั้งที่ {row.nth} ในปีงบ (เบิกได้ไม่เกิน {LIMITS[m.group]} ครั้งต่อคนต่อปี)")
        if m.group == "culture":
            row.technique = techniques.get(key, "")
            if row.technique in CULTURE:
                row.label, row.rate = f"Culture · {CULTURE[row.technique][0]}", CULTURE[row.technique][1]
            else:
                row.add(WARN, "TECHNIQUE", "เลือกเทคนิค Culture (Solid 200 / Liquid 300 บาท)")
        if not row.result:
            row.add(WARN, "NO_RESULT", "ยังไม่มีผลตรวจใน HOSxP")
        if day >= start:
            out.append(row)
    return out


def labs_xlsx(rows: list[LabRow], start: date, end: date, hname: str) -> bytes:
    import openpyxl
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Lab วัณโรค"
    sheet.append([f"ตรวจเงื่อนไขเบิก Lab วัณโรค (NTIP) {hname}".strip()])
    sheet.append([f"วันที่สั่งตรวจ {start.isoformat()} ถึง {end.isoformat()}"])
    head = ["วันที่", "VN", "HN", "เลขบัตร", "ชื่อ-สกุล", "สิทธิ", "รายการ LAB", "ผล", "กลุ่ม", "วิธี", "ครั้งที่ในปีงบ",
            "อัตรา (บาท)", "เบิกได้", "ปัญหา"]
    sheet.append(head)
    for cell in sheet[3]:
        cell.font = Font(bold=True)
    for r in rows:
        sheet.append([r.date, r.vn, r.hn, r.cid, r.ptname, r.pttype, r.name, r.result, GROUP_LABEL[r.group], r.label, r.nth,
                      float(r.rate) if r.rate is not None else None, "ได้" if r.claimable else "ไม่ได้",
                      " / ".join(x["message"] for x in r.errors + r.warnings)])
    for row in sheet.iter_rows(min_row=4, min_col=1, max_col=4):
        for cell in row:
            cell.number_format = "@"
    for i, width in enumerate([11, 14, 10, 16, 26, 26, 26, 20, 11, 30, 9, 10, 8, 60], 1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    sheet.freeze_panes = "A4"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
