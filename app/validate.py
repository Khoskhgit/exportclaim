"""Pre-send checks. Pure functions: no database access, so they are easy to test.

Rules come from ssop.txt AddData (lines 1768-1822).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable

ERROR, WARN, WARN_C, SPECIAL = "error", "warn", "warn_c", "special"
PATIENT_PAID = {"00", "01", "03"}
TELEMED_START, TELEMED_END = date(2026, 5, 1), date(2026, 7, 31)  # ssop.txt:1754
COVID_END = date(2022, 10, 1)  # ssop.txt:1693
PF_CODES = re.compile(r"^(4018|4828|4837|4904|4905|7142|8491|8492)")
SLEEP_CODES = {"51120", "51121", "3012", "3013"}
SI_CODES = {"9902", "55080"}
LAB_COVID = {
    "351123", "351121", "350998", "350506", "351015", "351016", "350507",
    "351024", "350508", "351017", "351139", "350509", "351122",
}
DENTAL_CODES = {
    "62101", "67201", "67202", "67203", "67204", "67210", "67211", "67212", "67213",
    "71840", "71841", "72610",
}
CANCER_PDX = re.compile(r"^(C|D[0-4])", re.I)
PDX_NOT_CLAIMABLE = re.compile(r"[V-Y]", re.I)


@dataclass(frozen=True)
class Issue:
    code: str
    level: str
    message: str


def dec(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def text(value) -> str:
    return "" if value is None else str(value).strip()


def as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def clock(value):
    """Convert the timedelta PyMySQL returns for TIME columns into a time of day."""
    if isinstance(value, timedelta):
        return (datetime.min + value).time()
    return value


def begin_datetime(visit: dict) -> datetime | None:
    day, moment = as_date(visit.get("vstdate")), clock(visit.get("vsttime"))
    return datetime.combine(day, moment) if day and moment is not None else None


def end_datetime(visit: dict) -> datetime | None:
    """Latest of visit time, current-department time, and department exit (MaxTime)."""
    begin = begin_datetime(visit)
    if begin is None:
        return None
    candidates = [begin]
    current = clock(visit.get("cur_dep_time"))
    if current is not None:
        candidates.append(datetime.combine(begin.date(), current))
    if isinstance(visit.get("depart_out"), datetime):
        candidates.append(visit["depart_out"])
    return max(candidates)


def clinic_code(visit: dict) -> str:
    return text(visit.get("clinic")).zfill(2)


def clinic_valid(code: str) -> bool:
    return code == "99" or "01" <= code <= "12"


def doctor_valid(license_no: str) -> bool:
    # Mirrors the old check, which looked only at the first character after the prefix.
    return len(license_no) > 1 and license_no[0] in "วทพภ-" and license_no[1].isdigit()


def main_inscl(visit: dict) -> str:
    """Insurance group from SeekHipData(), or the raw code when the database lacks it."""
    return text(visit.get("maininscl")) or text(visit.get("hipdata_code"))


def is_social_security(visit: dict) -> bool:
    # This HOSxP returns SSS; the old FoxPro test m.maininscl='SS' is a prefix match.
    return main_inscl(visit).startswith("SS")


def pay_plan(visit: dict) -> str:
    """Claim pay plan (ssop.txt:2062). 'XX' means it could not be determined."""
    upp = text(visit.get("upp_code"))
    if upp:
        return upp
    eclaim = text(visit.get("pttype_eclaim_id"))
    if eclaim == "13":
        return "81"
    if eclaim in {"18", "19", "20"}:
        return "20"
    inscl = main_inscl(visit)
    if inscl == "OFC":
        return "10"
    if inscl == "SSI":
        return "86"
    return "80" if is_social_security(visit) else "XX"


def special_case(visit: dict, items: Iterable[dict]) -> str:
    """Name a claim type this version cannot send, or '' for a normal claim."""
    items = list(items)
    codes = {text(i.get("nhso_adp_code")) for i in items}
    pdx = text(visit.get("pdx"))
    day = as_date(visit.get("vstdate"))

    has_pf = any(
        PF_CODES.match(text(i.get("nhso_adp_code"))) and text(i.get("nhso_adp_type_id")) in {"2", "19"}
        for i in items
    )
    if has_pf:
        return "เตรียมเส้นฟอกไต"
    if codes & SLEEP_CODES:
        return "Sleep Test"
    if CANCER_PDX.match(pdx):
        cancer_item = bool(codes & DENTAL_CODES) or any(
            i.get("ssocac_item") or re.search(r"CANCER|SSOCAC|^C\d", text(i.get("nhso_adp_code")))
            for i in items
        )
        if cancer_item:
            return "มะเร็ง (SSOCAC)"
    if day and TELEMED_START <= day <= TELEMED_END and (
        text(visit.get("typein_code")) == "5" or "TELMED" in codes
    ):
        return "TeleMed"
    if day and day < COVID_END and (
        codes & SI_CODES or codes & LAB_COVID or re.search(r"Z115|U071|U11", pdx, re.I)
    ):
        return "โควิด"
    return ""


RESULT_HINTS = {
    "C02": "ตรวจสิทธิประกันสังคมของผู้ป่วยในวันที่รักษา แล้วแก้สิทธิหรือวันที่ใน HOSxP",
    "R01": "ระบบรับ ID นี้แล้ว ให้ใช้ VN เดิมและส่งใหม่ด้วยประเภท E: แก้ไข",
    "S01": "ระบบรับ ID นี้แล้ว ให้ใช้ VN เดิมและส่งใหม่ด้วยประเภท E: แก้ไข",
    "T02": "ระบบรับ ID นี้แล้ว ให้ใช้ VN เดิมและส่งใหม่ด้วยประเภท E: แก้ไข",
    "S18": "ตรวจรหัสวินิจฉัยใน HOSxP และ codeset ของรหัสนั้น",
    "S21": "ตรวจรหัสและราคาของรายการหมวด B (ทำหัตถการและวิสัญญี)",
    "T31": "ตรวจว่า visit มีรายการค่าใช้จ่ายที่ส่งออกได้",
    "T33": "ตรวจยอดรวม รายการที่ติ๊กไม่ส่ง และยอดที่ผู้ป่วยจ่ายเอง",
    "T45": "ตรวจยอดรวม รายการที่ติ๊กไม่ส่ง และยอดที่ผู้ป่วยจ่ายเอง",
    "T55": "ตรวจรายการที่จำนวนเป็น 0",
    "W01": "เพิ่มยาใน Drug Catalog",
    "W05": "ปรับราคายาใน Drug Catalog ให้ตรงกับ HOSxP",
    "W07": "ตรวจรหัส TMT ของยาใน HOSxP ให้ตรงกับ Drug Catalog",
    "W09": "เพิ่มรหัส TMT ของยาใน Drug Catalog",
    "Z99": "รอ สกส. แจ้ง แล้วจึงส่งข้อมูลใหม่",
}
RESULT_GROUPS = {"S21": ["B"]}


def explain_result_codes(codes, names: dict[str, str]) -> list[dict]:
    """Describe a claim's result codes. W codes also appear on accepted claims, so they are warnings."""
    result = []
    for code in (part.strip().upper() for part in text(codes).split(",")):
        if code:
            result.append({
                "code": code, "warning": code.startswith("W"),
                "name": names.get(code, "ไม่มีคำอธิบายใน HOSxP"),
                "hint": RESULT_HINTS.get(code, "ตรวจกับคู่มือ SSOP หรือสอบถามผู้รับไฟล์"),
                "groups": RESULT_GROUPS.get(code, []),
            })
    return sorted(result, key=lambda r: r["warning"])


def send_status(visit: dict, local_sent: bool) -> str:
    if text(visit.get("rep")):
        return "ชดเชยแล้ว"
    chi = text(visit.get("chi_status"))
    if chi == "A":
        return "ส่งแล้ว"
    if chi:
        return "ติด C"
    if visit.get("hi_registered"):
        return "HI"
    return "ส่งออกแล้ว" if local_sent else ""


def tmlt_problem(item: dict) -> str:
    """Why a lab item's TMLT mapping is incomplete, or '' when it is fine or not a lab item."""
    if text(item.get("drg_chrgitem_id")) not in {"6", "7"}:
        return ""
    chi, code = text(item.get("lab_chi")), text(item.get("tmlt_code"))
    if not chi and not code:
        return "ไม่พบใน labitem"
    if chi != "1":
        return "labitem.chi ไม่เป็น 1"
    if not code:
        return "ไม่มีรหัส TMLT"
    return "รหัส TMLT มีตัว X" if "X" in code.upper() else ""


def check_pdx(visit: dict, z_codes: set[str], z_ready: bool) -> list[Issue]:
    pdx = text(visit.get("pdx")).upper()
    if not pdx:
        return [Issue("PDX_EMPTY", ERROR, "ไม่มี PDx")]
    issues = []
    if PDX_NOT_CLAIMABLE.search(pdx):
        issues.append(Issue("PDX_EXTERNAL", ERROR, "PDx ผิด (เป็นรหัส V-Y)"))
    if not text(visit.get("nhso_code")):
        issues.append(Issue("PDX_NOT_FOUND", WARN, "ไม่พบรหัส Dx ในตาราง icd10_nhso"))
    if z_ready and pdx.startswith("Z") and pdx not in z_codes:
        issues.append(Issue("PDX_Z_NOT_ALLOWED", WARN_C, "ไม่สามารถเบิก PDx นี้ได้"))
    return issues


def check_visit(
    visit: dict, items: Iterable[dict], hosp_main: Iterable[str],
    z_codes: set[str], z_ready: bool,
) -> list[Issue]:
    items = list(items)
    issues = list(check_pdx(visit, z_codes, z_ready))

    def add(code: str, level: str, message: str) -> None:
        issues.append(Issue(code, level, message))

    income = dec(visit.get("income"))
    cents = Decimal("0.01")
    line_total = sum((dec(i.get("qty")) * dec(i.get("unitprice")) for i in items), Decimal("0")).quantize(cents)
    paid = sum((dec(i.get("sum_price")) for i in items if text(i.get("paidst")) in PATIENT_PAID), Decimal("0")).quantize(cents)
    line_mismatch = any(dec(i.get("qty")) * dec(i.get("unitprice")) != dec(i.get("sum_price")) for i in items)

    if income == 0:
        add("INCOME_ZERO", ERROR, "ค่าใช้จ่ายเป็น 0")
    if line_total != income.quantize(cents) or line_mismatch:
        add("INCOME_MISMATCH", ERROR, "ค่าใช้จ่ายไม่เท่ากัน")
    if dec(visit.get("rcpt_money")).quantize(cents) != paid:
        add("PAYMENT_MISMATCH", ERROR, "การชำระเงินผิดพลาด")

    if not clinic_valid(clinic_code(visit)):
        add("CLINIC_INVALID", ERROR, "รหัสคลินิกไม่ตรงมาตรฐาน")
    if "ADMIT" in text(visit.get("typeout_name")).upper():
        add("ADMITTED", ERROR, "ผลการรักษา Admit")
    if text(visit.get("an")):
        add("HAS_AN", ERROR, "เป็นผู้ป่วยใน (มี AN)")

    hospmain = text(visit.get("hospmain"))
    if not hospmain:
        add("HOSPMAIN_EMPTY", ERROR, "ไม่มี HospMain")
    elif is_social_security(visit) and hospmain not in set(hosp_main):
        add("HOSPMAIN_WRONG", ERROR, "รหัส HospMain ผิด")
    if pay_plan(visit) == "XX":
        add("PAYPLAN_UNKNOWN", ERROR, "ระบุ PayPlan ของสิทธินี้ไม่ได้")

    if not text(visit.get("cid")):
        add("CID_EMPTY", ERROR, "ไม่มีเลขที่บัตรประชาชน")
    if any(not text(i.get("cscd_group")) for i in items):
        add("BILLMUAD_EMPTY", ERROR, "ไม่มี BILLMUAD")
    if any(text(i.get("cscd_group")) == "3" and not text(i.get("icode")).startswith("1") for i in items):
        add("GROUP_MAP_WRONG", ERROR, "Map หมวดผิด")
    if any(not text(i.get("name")) for i in items):
        add("DRUG_NAME_EMPTY", ERROR, "ไม่มีชื่อยา")
    if any(text(i.get("hn")) != text(visit.get("hn")) for i in items):
        add("OTHER_HN", ERROR, "พบ HN อื่นในรายการ")

    begin, end = begin_datetime(visit), end_datetime(visit)
    if end is None:
        add("END_TIME_EMPTY", ERROR, "ไม่มีเวลาสิ้นสุด")
    elif begin is not None and end < begin:
        add("END_BEFORE_BEGIN", WARN, "เวลาสิ้นสุดน้อยกว่าวันที่มาตรวจ")

    visit_day = as_date(visit.get("vstdate"))
    datein, dateexp = as_date(visit.get("datein")), as_date(visit.get("dateexp"))
    if datein is None:
        add("DATEIN_EMPTY", WARN, "ไม่มีวันที่เริ่มใช้")
    elif visit_day and datein > visit_day:
        add("DATEIN_AFTER_VISIT", WARN, "วันที่เริ่มใช้มากกว่าวันที่มาตรวจ")
    if dateexp is None or (visit_day and dateexp < visit_day):
        add("DATEEXP_BEFORE_VISIT", WARN, "วันหมดอายุน้อยกว่าวันที่มาตรวจ")

    if not doctor_valid(text(visit.get("doctor"))):
        add("DOCTOR_INVALID", WARN, "รหัส ว.แพทย์ไม่ถูกต้อง")
    if any(tmlt_problem(i) for i in items):
        add("TMLT_INCOMPLETE", WARN, "Map TMLT ยังไม่ครบ")
    return issues


def review_visit(
    visit: dict, items: Iterable[dict], hosp_main: Iterable[str],
    z_codes: set[str], z_ready: bool, local_sent: bool = False,
) -> dict:
    """Summarise a visit for the review screen."""
    items = list(items)
    issues = check_visit(visit, items, hosp_main, z_codes, z_ready)
    special = special_case(visit, items)
    status = send_status(visit, local_sent)
    levels = {i.level for i in issues}
    selectable = ERROR not in levels and not special
    already_sent = status in {"ส่งแล้ว", "ชดเชยแล้ว", "ส่งออกแล้ว"}
    return {
        "issues": [i.__dict__ for i in issues],
        "special": special,
        "status": status,
        "selectable": selectable,
        "already_sent": already_sent,
        "needs_correction": status == "ติด C",
        "default_selected": selectable and not status and WARN_C not in levels,
        "has_error": ERROR in levels,
        "has_warn": WARN in levels,
        "has_warn_c": WARN_C in levels,
    }
