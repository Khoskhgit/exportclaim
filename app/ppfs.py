"""PP Fee Schedule (PPFS) items claimed from สปสช. through the e-Claim program, for patients of every right.

Each service type is one entry of SERVICES (the page's drop-down). The postnatal rules are here; the others have a
module of their own (ppfs_pregtest).

7) การตรวจหลังคลอด - Thai women after giving birth, all rights:
   ADP 30015 ตรวจหลังคลอด, 150 baht, at most 3 times per birth, one in each window counted from the day after the
            birth: day 1-7, day 8-15, day 16-42
   ADP 30016 ยาเสริมธาตุเหล็ก กรดโฟลิก ไอโอดีน (90 tablets, 3 months), 135 baht, at most 2 times per birth

What HOSxP holds:
   opitemrece + s_drugitems.nhso_adp_code 30015 / 30016   the billed item (what the ZIP claims)
   person_anc_preg_care (care_date, preg_care_number, vn)  the postnatal check record; without a VN it was done
                                                            at home or at another place and is not ours to claim
   person_anc.labor_date                                   the birth (ipt_labour is not used at this hospital)

The birth an item belongs to is the patient's latest labor_date on or before the visit.

8) บริการทดสอบการตั้งครรภ์ - see ppfs_pregtest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable, Iterable

from .nhso18 import lit, lit_list
from .validate import text

ERROR, WARN = "error", "warn"
CHECK, DRUG, PREGTEST = "30015", "30016", "30014"


@dataclass(frozen=True)
class Service:
    key: str
    label: str
    codes: dict[str, tuple[str, Decimal]]  # ADP code -> (short name, rate)
    rules: tuple[str, ...]


SERVICES = {
    "dmrem": Service(
        key="dmrem", label="6) กรณีบริการเบาหวานระยะสงบ (DM Remission)",
        codes={"DMR1": ("เบาหวานระยะสงบ ขั้น 1", Decimal("4000")), "DMR2": ("เบาหวานระยะสงบ ขั้น 2", Decimal("2000"))},
        rules=(
            "กลุ่มเป้าหมาย: ผู้ป่วยเบาหวานชนิดที่ 2 (E11) อายุ 18 ปีขึ้นไป สิทธิ UC ที่ลงทะเบียนในโปรแกรม e-Claim แล้ว (โปรแกรมนี้ตรวจการลงทะเบียนไม่ได้)",
            "ขั้น 1: 4,000 บาท/ราย เมื่อ HbA1c < 6.5% ติดต่อกัน 2 ครั้ง ห่างกันอย่างน้อย 3 เดือน หลังหยุดยาลดน้ำตาล"
            " (นับว่าไม่ได้รับยาตั้งแต่ 3 เดือนก่อนผลครั้งแรก) หรือปรับพฤติกรรมอย่างเข้มงวด",
            "ขั้น 2: 2,000 บาท/ราย เมื่อยังคง HbA1c < 6.5% อีก 1 ครั้ง ห่างจากครั้งที่ 2 อย่างน้อย 3 เดือน · จ่ายขั้นละครั้งเดียว",
            "ไม่มีรหัส ADP: สปสช. ประมวลผลจากผล HbA1c ที่ส่งเข้า e-Claim (ตัดรอบ 15 ก.ย. / 31 มี.ค.) ZIP จะส่ง visit ที่ตรวจ HbA1c"
            " ของแต่ละขั้นพร้อมแฟ้ม LAB โดยไม่มีค่าใช้จ่าย (ค่ารักษาของ visit ส่งตามปกติที่หน้า 18 แฟ้ม)",
        )),
    "postnatal": Service(
        key="postnatal", label="7) การตรวจหลังคลอด",
        codes={CHECK: ("ตรวจหลังคลอด", Decimal("150")), DRUG: ("ยาธาตุเหล็ก/โฟลิก/ไอโอดีน", Decimal("135"))},
        rules=(
            "กลุ่มเป้าหมาย: หญิงหลังคลอดคนไทย ทุกสิทธิการรักษาพยาบาล",
            "ตรวจหลังคลอด (ADP 30015) 150 บาท/ครั้ง ไม่เกิน 3 ครั้ง: วันที่ 1–7, 8–15 และ 16–42 นับถัดจากวันคลอด",
            "ยาเสริมธาตุเหล็ก กรดโฟลิก ไอโอดีน (ADP 30016) ครั้งละ 3 เดือน (90 เม็ด) 135 บาท/ครั้ง ไม่เกิน 2 ครั้งต่อการคลอด",
        )),
    "pregtest": Service(
        key="pregtest", label="8) บริการทดสอบการตั้งครรภ์",
        codes={PREGTEST: ("ทดสอบการตั้งครรภ์", Decimal("75"))},
        rules=(
            "กลุ่มเป้าหมาย: หญิงวัยเจริญพันธุ์ที่สงสัยตั้งครรภ์หรือประจำเดือนไม่มาตามกำหนด ทุกสิทธิการรักษาพยาบาล",
            "ตรวจปัสสาวะทดสอบการตั้งครรภ์ หรือชุดทดสอบการตั้งครรภ์ด้วยตัวเอง (ADP 30014) 75 บาท/ครั้ง",
            "ไม่เกิน 4 ครั้งต่อปี (นับตามปีงบประมาณ ต.ค.–ก.ย.)",
        )),
    "fp": Service(
        key="fp", label="9) บริการวางแผนครอบครัวและการป้องกันการตั้งครรภ์ไม่พึงประสงค์",
        codes={"FP003_1": ("ยาเม็ดคุมฮอร์โมนรวม", Decimal("40")), "FP003_2": ("ยาเม็ดคุมฮอร์โมนเดี่ยว", Decimal("80")),
               "FP003_4": ("ยาฉีดคุมกำเนิด", Decimal("60")), "FP001": ("ใส่ห่วงอนามัย", Decimal("800")),
               "FP002_1": ("ฝังยาคุมกำเนิด", Decimal("2150")), "FP002_2": ("ถอดยาฝังคุมกำเนิด", Decimal("350")),
               "FP003_3": ("ยาคุมฉุกเฉิน", Decimal("50"))},
        rules=(
            "กลุ่มเป้าหมาย: หญิงไทยวัยเจริญพันธุ์ ทุกสิทธิการรักษาพยาบาล · นับ \"ต่อปี\" ตามปีงบประมาณ ต.ค.–ก.ย.",
            "ยาเม็ดคุมกำเนิด ครั้งละไม่เกิน 3 แผง ไม่เกิน 13 แผง/ปี: ฮอร์โมนรวม (FP003_1) 40 บาท/แผง, ฮอร์โมนเดี่ยว (FP003_2) 80 บาท/แผง"
            " เฉพาะหญิงให้นมบุตรไม่เกิน 1 ปี 6 เดือน หรือมีไมเกรนแบบมีออร่า",
            "ยาฉีดคุมกำเนิด (FP003_4) 60 บาท ไม่เกิน 5 ครั้ง/ปี · ใส่ห่วงอนามัย (FP001) 800 บาท ครั้งต่อปี",
            "ฝังยาคุมกำเนิด (FP002_1) 2,150 บาท · ถอดยาฝังคุมกำเนิด (FP002_2) 350 บาท",
            "ยาเม็ดคุมกำเนิดฉุกเฉิน (FP003_3) 50 บาท/แผง ไม่เกิน 2 แผง/ปี",
            "ส่งเฉพาะค่าบริการ (รายการ 18…) ตัวยาที่ใช้รหัสเดียวกันไม่ถูกส่ง · ถุงยางอนามัยยังไม่มีรายการใน HOSxP",
        )),
    "ncd": Service(
        key="ncd", label="15) บริการคัดกรองเบาหวานและไขมันในเลือด",
        codes={"12003": ("คัดกรองเบาหวาน FPG", Decimal("40")), "12004": ("คัดกรองไขมัน TC + HDL", Decimal("160"))},
        rules=(
            "เบาหวาน (ADP 12003): เจาะเลือดหลังอดอาหาร 8 ชม. ตรวจ FPG อายุ 35–59 ปี 40 บาท ปีละ 1 ครั้ง (ปีงบประมาณ ต.ค.–ก.ย.)"
            " ยกเว้นผู้มีประวัติรักษาเบาหวาน",
            "ไขมัน (ADP 12004): ตรวจ Total Cholesterol และ HDL อายุ 45–70 ปี 160 บาท ทุก 5 ปี"
            " ยกเว้นผู้มีประวัติรักษาไขมันในเลือดสูงหรือได้ยาลดไขมัน",
            "ไม่ได้ตรวจปัจจัยเสี่ยงเบาหวานตามหมายเหตุในประกาศ (BMI, ความดัน, ประวัติครอบครัว) ตรวจเฉพาะอายุและประวัติการรักษา",
        )),
    "anemia": Service(
        key="anemia", label="17) บริการคัดกรองโลหิตจางจากการขาดธาตุเหล็ก",
        codes={"13001": ("คัดกรองโลหิตจาง CBC", Decimal("65"))},
        rules=(
            "กลุ่มเป้าหมาย: หญิงไทยทุกคน อายุ 13–24 ปี ทุกสิทธิการรักษาพยาบาล",
            "เจาะเลือดตรวจนับเม็ดเลือดอย่างสมบูรณ์ (CBC) ADP 13001 65 บาท/คน เบิกได้คนละ 1 ครั้ง (นับทุกครั้งที่เคยคีย์ใน HOSxP)",
        )),
    "iron": Service(
        key="iron", label="18) บริการยาเม็ดเสริมธาตุเหล็ก",
        codes={"14001": ("ยาเม็ดเสริมธาตุเหล็ก", Decimal("80"))},
        rules=(
            "กลุ่มเป้าหมาย: หญิงไทยทุกคน อายุ 13–45 ปี ทุกสิทธิการรักษาพยาบาล",
            "ยาเม็ดเสริมธาตุเหล็กและกรดโฟลิก สัปดาห์ละ 1 เม็ด รวม 52 เม็ดต่อปี ADP 14001 80 บาทต่อคนต่อปี (ปีงบประมาณ ต.ค.–ก.ย.)",
        )),
    "fluoride": Service(
        key="fluoride", label="19) บริการเคลือบฟลูออไรด์",
        codes={"15001": ("เคลือบฟลูออไรด์ (กลุ่มเสี่ยง)", Decimal("100"))},
        rules=(
            "กลุ่มเป้าหมาย: ผู้มีอายุ 25–59 ปี กลุ่มเสี่ยงต่อโรคฟันผุ",
            "ให้คำปรึกษาและทาฟลูออไรด์วาร์นิช 22,600 ppm ADP 15001 100 บาทต่อครั้ง ไม่เกิน 2 ครั้งต่อคนต่อปี (ปีงบประมาณ ต.ค.–ก.ย.)",
            "ตรวจคู่กับบันทึกทันตกรรม (ทาฟลูออไรด์ผู้ใหญ่ 2387020 / 2387021) ไม่ได้ตรวจความเสี่ยงฟันผุ",
        )),
    "anemiachild": Service(
        key="anemiachild", label="26) บริการคัดกรองภาวะโลหิตจางจากการขาดธาตุเหล็ก (เด็ก)",
        codes={"10000": ("คัดกรองโลหิตจาง Hb/Hct เด็ก", Decimal("30"))},
        rules=(
            "กลุ่มเป้าหมาย: คนไทยทุกสิทธิ อายุ 6–12 เดือน 1 ครั้ง และอายุ 3–5 ปี 1 ครั้ง (นับอายุ ณ วันรับบริการ)",
            "ตรวจ Hb หรือ Hct 30 บาทต่อครั้งต่อช่วงอายุ ไม่เกิน 2 ครั้งต่อคน (นับทุกครั้งที่เคยคีย์ใน HOSxP)",
            "ค่าบริการ 3003783 (6–12 เดือน) และ 3004022 (3–5 ปี) ส่งด้วย ADP 10000 ตามที่ผูกไว้ใน HOSxP",
        )),
    "ironchild": Service(
        key="ironchild", label="27) บริการยาเสริมธาตุเหล็กเพื่อป้องกันโลหิตจาง (6–12 เดือน, 2–5 ปี)",
        codes={"689697": ("ยาน้ำเสริมธาตุเหล็ก", Decimal("50")), "776508": ("ยาเม็ดเสริมธาตุเหล็ก", Decimal("50"))},
        rules=(
            "กลุ่มเป้าหมาย: คนไทยทุกสิทธิ ตามช่วงอายุ 6–12 เดือน (ค่าบริการ 3004084) และ 2–5 ปี (ค่าบริการ 3004023) นับอายุ ณ วันรับบริการ",
            "รหัส ADP ตามยาที่จ่ายใน visit: ยาน้ำ = 689697, ยาเม็ด = 776508 (HOSxP ผูกค่าบริการไว้เป็น 10000 ZIP จะส่งรหัสตามชนิดยาแทน)",
            "50 บาทต่อครั้งต่อปีงบประมาณ (ต.ค.–ก.ย.) ในแต่ละช่วงอายุ",
        )),
}
WINDOWS = {1: "1–7", 2: "8–15", 3: "16–42"}
MAX_COUNT = {CHECK: 3, DRUG: 2}
IRON = "FERR|(^|[^A-Za-z])IRON|TRIFER|FOLIC|IODI|ธาตุเหล็ก|ไอโอดีน"  # not SPIRONOLACTONE

Select = Callable[[str], list[dict]]


def window(days: int | None) -> int | None:
    """The postnatal window of a day counted from the birth (day 0 is the birth itself)."""
    if days is None or days < 0 or days > 42:
        return None
    return 1 if days <= 7 else 2 if days <= 15 else 3


def _day(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def fiscal_year(day: date) -> int:
    """The Thai fiscal (budget) year a day falls in, as a Christian year: October 2025 - September 2026 is 2026 (งบ 2569).
    Every "per year" PPFS limit is counted by it, as the user chose."""
    return day.year + 1 if day.month >= 10 else day.year


def fiscal_range(year: int) -> tuple[date, date]:
    return date(year - 1, 10, 1), date(year, 9, 30)


def _money(value) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except ArithmeticError:
        return Decimal("0.00")


# ------------------------------------------------------------------ HOSxP (read only)
def fetch(q: Select, start: date, end: date) -> dict:
    """Everything the postnatal checks need for visits from start to end."""
    items = q(f"""
        SELECT oo.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, p.cid, p.nationality,
          d.nhso_adp_code code, oo.icode, d.name item_name, oo.qty, oo.unitprice, oo.paidst,
          IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0) amount
        FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode
        JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype
        WHERE o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND d.nhso_adp_code IN ({lit_list([CHECK, DRUG])})""")
    cares = q(f"""
        SELECT c.vn, c.care_date, c.preg_care_number care_no, c.person_anc_id, a.labor_date,
          o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name,
          CONCAT(IFNULL(p.pname,''),p.fname,' ',p.lname) ptname, p.cid, p.nationality
        FROM person_anc_preg_care c JOIN person_anc a ON a.person_anc_id=c.person_anc_id
        JOIN ovst o ON o.vn=c.vn JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype
        WHERE IFNULL(c.vn,'')<>'' AND (o.vstdate BETWEEN {lit(start)} AND {lit(end)}
          OR c.care_date BETWEEN {lit(start)} AND {lit(end)})""")
    hns = sorted({text(r["hn"]) for r in items + cares if text(r.get("hn"))})
    births = q(f"""
        SELECT p.patient_hn hn, a.person_anc_id, a.labor_date FROM person p JOIN person_anc a ON a.person_id=p.person_id
        WHERE p.patient_hn IN ({lit_list(hns)}) AND a.labor_date IS NOT NULL""") if hns else []
    first = min((_day(b["labor_date"]) for b in births), default=None)
    history = q(f"""
        SELECT oo.vn, o.hn, o.vstdate, d.nhso_adp_code code FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn
        JOIN s_drugitems d ON d.icode=oo.icode
        WHERE o.hn IN ({lit_list(hns)}) AND o.vstdate BETWEEN {lit(first)} AND {lit(end)}
          AND d.nhso_adp_code IN ({lit_list([CHECK, DRUG])}) AND IFNULL(oo.qty,0)>0
        GROUP BY oo.vn, d.nhso_adp_code""") if hns and first else []
    vns = sorted({text(r["vn"]) for r in items if text(r.get("code")) == DRUG})
    iron = {text(r["vn"]): r["qty"] for r in q(f"""
        SELECT oo.vn, SUM(oo.qty) qty FROM opitemrece oo JOIN drugitems di ON di.icode=oo.icode
        WHERE oo.vn IN ({lit_list(vns)}) AND CONCAT_WS(' ',di.name,di.generic_name) REGEXP {lit(IRON)}
        GROUP BY oo.vn""")} if vns else {}
    return {"items": items, "cares": cares, "births": births, "history": history, "iron": iron}


def visit_items(q: Select, vns: Iterable[str]) -> list[dict]:
    """Every billed item of these visits: (vn, icode, ADP code, is a drug), to leave all but the claimed fees out of
    the ZIP. A drug can carry the same ADP code as its service fee (DMPA and "ฉีดยาคุมกำเนิด"); only the fee is claimed."""
    vns = sorted(set(vns))
    if not vns:
        return []
    return q(f"""
        SELECT oo.vn, oo.icode, d.nhso_adp_code code, EXISTS(SELECT 1 FROM drugitems di WHERE di.icode=oo.icode) is_drug
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode
        WHERE oo.vn IN ({lit_list(vns)})""")



def detail(q: Select, vn: str) -> dict:
    """What the drawer shows for one visit: the birth, every postnatal record and claim of that birth, the iron
    drugs, the diagnoses and every billed item of the visit."""
    visit = (q(f"""
        SELECT o.vn, o.hn, o.vstdate, o.vsttime, o.an, o.pttype, ptt.name pttype_name, p.nationality, p.birthday,
          (SELECT n.name FROM nationality n WHERE n.nationality=p.nationality) nationality_name
        FROM ovst o JOIN patient p ON p.hn=o.hn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype
        WHERE o.vn={lit(vn)}""") or [None])[0]
    if visit is None:
        return {}
    hn, day = text(visit["hn"]), _day(visit["vstdate"])
    births = q(f"""
        SELECT a.person_anc_id, a.preg_no, a.lmp, a.labor_date, a.labour_hospcode,
          (SELECT t.person_labour_type_name FROM person_labour_type t WHERE t.person_labour_type_id=a.labour_type_id) labour_type
        FROM person p JOIN person_anc a ON a.person_id=p.person_id
        WHERE p.patient_hn={lit(hn)} AND a.labor_date IS NOT NULL ORDER BY a.labor_date""")
    own = (q(f"SELECT person_anc_id FROM person_anc_preg_care WHERE vn={lit(vn)} LIMIT 1") or [{}])[0].get("person_anc_id")
    birth = next((b for b in births if own and b["person_anc_id"] == own), None) \
        or max((b for b in births if day and _day(b["labor_date"]) <= day), key=lambda b: _day(b["labor_date"]), default=None)
    cares, claims = [], []
    if birth is not None:
        start = _day(birth["labor_date"])
        later = [_day(b["labor_date"]) for b in births if _day(b["labor_date"]) > start]
        stop = min(later) if later else None
        cares = q(f"""
            SELECT c.care_date, c.preg_care_number care_no, c.vn, l.anc_preg_care_location_name place
            FROM person_anc_preg_care c LEFT JOIN anc_preg_care_location l ON l.anc_preg_care_location_id=c.anc_preg_care_location_id
            WHERE c.person_anc_id={lit(birth["person_anc_id"])} ORDER BY c.care_date""")
        claims = q(f"""
            SELECT o.vn, o.vstdate, d.nhso_adp_code code, SUM(oo.qty) qty, SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount
            FROM ovst o JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode
            WHERE o.hn={lit(hn)} AND o.vstdate>={lit(start)} {f"AND o.vstdate<{lit(stop)}" if stop else ""}
              AND d.nhso_adp_code IN ({lit_list([CHECK, DRUG])})
            GROUP BY o.vn, d.nhso_adp_code ORDER BY o.vstdate, o.vn, d.nhso_adp_code""")
    iron = q(f"""
        SELECT CONCAT_WS(' ',di.name,di.strength) name, SUM(oo.qty) qty FROM opitemrece oo JOIN drugitems di ON di.icode=oo.icode
        WHERE oo.vn={lit(vn)} AND CONCAT_WS(' ',di.name,di.generic_name) REGEXP {lit(IRON)} GROUP BY oo.icode""")
    items = q(f"""
        SELECT oo.icode, CONCAT_WS(' ',d.name,d.strength) name, d.nhso_adp_code adp, SUM(oo.qty) qty,
          SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn={lit(vn)}
        GROUP BY oo.icode ORDER BY d.nhso_adp_code IS NULL, oo.icode""")
    dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM ovstdiag WHERE vn={lit(vn)} ORDER BY diagtype, icd10")
    return {"visit": visit, "birth": birth, "cares": cares, "claims": claims, "iron": iron, "items": items, "dx": dx}

# ------------------------------------------------------------------ rules
@dataclass
class Row:
    key: str
    vn: str
    code: str
    hn: str = ""
    date: str = ""
    time: str = ""
    ptname: str = ""
    cid: str = ""
    pttype: str = ""
    labor_date: str = ""
    days: int | None = None
    window: int | None = None
    count: int | None = None
    qty: Decimal = Decimal("0")
    amount: Decimal = Decimal("0.00")
    item_name: str = ""
    care_no: int | None = None
    care_date: str = ""
    sent: str = ""
    extra: dict = field(default_factory=dict)  # columns of one service only (age, UPT result, …)
    self_paid: bool = False  # a fee marked ชำระเองเบิกไม่ได้ (paidst 03): the 18 files would send it as QTY 0
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    def add(self, level: str, code: str, message: str) -> None:
        (self.errors if level == ERROR else self.warnings).append({"code": code, "message": message})


SELF_PAID = "03"


def self_paid_error(row: "Row") -> None:
    if row.self_paid:
        row.add(ERROR, "SELF_PAID", "ค่าบริการตั้งเป็น \"ชำระเองเบิกไม่ได้\" (paidst 03) ใน HOSxP แก้เป็นลูกหนี้สิทธิก่อนส่ง")


def code_name(code: str) -> tuple[str, Decimal]:
    for service in SERVICES.values():
        if code in service.codes:
            return service.codes[code]
    return code, Decimal("0")


def _birth_of(births: dict[str, list[date]], hn: str, day: date | None) -> date | None:
    if day is None:
        return None
    return max((b for b in births.get(hn, []) if b <= day), default=None)


def postnatal(data: dict, sent: dict[tuple[str, str], str] | None = None) -> list[Row]:
    """One row per visit and ADP code, with what stops it (errors) or needs a look (warnings)."""
    sent = sent or {}
    births: dict[str, list[date]] = {}
    for b in data["births"]:
        if d := _day(b["labor_date"]):
            births.setdefault(text(b["hn"]), []).append(d)
    care_by_vn = {}
    for c in data["cares"]:
        care_by_vn.setdefault(text(c["vn"]), c)

    rows: dict[tuple[str, str], Row] = {}
    info: dict[str, dict] = {}
    for r in data["items"]:
        vn, code = text(r["vn"]), text(r["code"])
        info.setdefault(vn, r)
        row = rows.get((vn, code))
        if row is None:
            row = rows[(vn, code)] = Row(key=f"{vn}|{code}", vn=vn, code=code)
            row.item_name = text(r.get("item_name"))
        row.qty += Decimal(str(r.get("qty") or 0))
        row.amount += _money(r.get("amount"))
        row.self_paid |= text(r.get("paidst")) == SELF_PAID
    for vn, c in care_by_vn.items():  # a check recorded, nothing billed: shown so it can be keyed in HOSxP
        info.setdefault(vn, c)
        if (vn, CHECK) not in rows:
            rows[(vn, CHECK)] = Row(key=f"{vn}|{CHECK}", vn=vn, code=CHECK, item_name="(ยังไม่คีย์ค่าบริการ)")

    # Which number each item is for its birth, from everything billed in HOSxP (also outside the date range).
    order: dict[tuple[str, date, str], list[tuple[date, str]]] = {}
    for h in data["history"]:
        hn, day = text(h["hn"]), _day(h["vstdate"])
        birth = _birth_of(births, hn, day)
        if birth is not None:
            order.setdefault((hn, birth, text(h["code"])), []).append((day, text(h["vn"])))
    for v in order.values():
        v.sort()

    for (vn, code), row in rows.items():
        r = info[vn]
        day = _day(r.get("vstdate"))
        row.hn, row.date, row.time = text(r.get("hn")), day.isoformat() if day else "", str(r.get("vsttime") or "")[:8]
        row.ptname, row.cid = text(r.get("ptname")), text(r.get("cid"))
        row.pttype = f"{text(r.get('pttype'))}: {text(r.get('pttype_name'))}".strip(": ")
        care = care_by_vn.get(vn)
        birth = _day(care["labor_date"]) if care and care.get("labor_date") else _birth_of(births, row.hn, day)
        row.labor_date = birth.isoformat() if birth else ""
        row.days = (day - birth).days if birth and day else None
        row.window = window(row.days)
        name, rate = code_name(code)
        billed = row.qty > 0

        if text(r.get("nationality")) != "99":
            row.add(ERROR, "NOT_THAI", "ไม่ใช่คนไทย (สัญชาติไม่ใช่ 99)")
        if text(r.get("an")):
            row.add(ERROR, "IPD", "visit นี้ Admit แล้ว (มี AN)")
        if not billed:
            row.add(ERROR, "NO_ITEM", f"มีบันทึกตรวจหลังคลอด แต่ยังไม่คีย์ค่าบริการ {code} ใน HOSxP")
        self_paid_error(row)
        if birth is None:
            row.add(ERROR, "NO_BIRTH", "ไม่พบวันคลอดใน HOSxP (person_anc.labor_date)")
        elif code == CHECK and row.window is None:
            row.add(ERROR, "OVER_42", f"ตรวจวันที่ {row.days} หลังคลอด เกิน 42 วัน")

        if birth is not None and billed:
            done = order.get((row.hn, birth, code), [])
            position = next((i for i, (_, v) in enumerate(done, 1) if v == vn), None)
            row.count = position
            if position and position > MAX_COUNT[code]:
                row.add(ERROR, "OVER_COUNT", f"{name}ครั้งที่ {position} เกิน {MAX_COUNT[code]} ครั้งต่อการคลอด")
            if code == CHECK and row.window and position:
                earlier = [v for d, v in done[:position - 1] if window((d - birth).days) == row.window]
                if earlier:
                    row.add(ERROR, "SAME_WINDOW", f"ช่วงวันที่ {WINDOWS[row.window]} เบิกไปแล้วที่ VN {earlier[0]}")
        if billed and code == CHECK and care is None:
            row.add(WARN, "NO_CARE", "ไม่มีบันทึกตรวจหลังคลอด (person_anc_preg_care) ของ visit นี้")
        if care is not None and code == CHECK:
            row.care_no = care.get("care_no")
            row.care_date = _day(care.get("care_date")).isoformat() if _day(care.get("care_date")) else ""
            if row.window and row.care_no and int(row.care_no) != row.window:
                row.add(WARN, "CARE_NO", f"บันทึกเป็นครั้งที่ {row.care_no} แต่ตรวจในช่วงวันที่ {WINDOWS[row.window]}")
            if row.care_date and row.care_date != row.date:
                row.add(WARN, "CARE_DATE", "วันที่ตรวจหลังคลอดไม่ตรงกับวันที่ของ VN")
        if billed and (row.qty != 1 or row.amount != rate):
            row.add(WARN, "PRICE", f"ยอด {row.amount} บาท (จำนวน {row.qty:g}) ไม่ตรงอัตรา {rate} บาท/ครั้ง")
        if billed and code == DRUG and vn not in data["iron"]:
            row.add(WARN, "NO_IRON", "ไม่พบการจ่ายยาธาตุเหล็ก/โฟลิก/ไอโอดีนใน visit นี้")
        row.sent = sent.get((vn, code), "")
    return sorted(rows.values(), key=lambda x: (x.date, x.time, x.vn, x.code))


def only_claimed(data: dict[str, list[dict]], claimed: dict[str, set[str]], keep_lab: bool = False) -> dict[str, list[dict]]:
    """Keep the 18 files to the PP items being sent: ADP rows of those codes, no lab or accident rows. Drugs the user
    ticked stay in DRU (the others were left out of the build already)."""
    out = dict(data)
    out["ADP"] = [r for r in data.get("ADP", []) if text(r.get("CODE")) in claimed.get(text(r.get("SEQ")), set())]
    for name in ("AER",) if keep_lab else ("LAB", "AER"):
        out[name] = []
    return out
