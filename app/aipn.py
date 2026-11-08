"""AIPN (in-patient) claim rules and XML/ZIP building. Pure functions: no database access.

Layout and rules come from aipn.txt: AddData (checks), ReadData (XML), CalData (field cleaning).
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .validate import ERROR, WARN, Issue, as_date, clock, dec, text

CRLF = "\r\n"
ENCODING = "cp874"
APPEALS = {"": "", "ADJ": "-ADJ", "ADD": "-ADD", "AUD": "-AUD"}
CARE_AS = {"M", "B", "A", "X"}
NOT_CLAIMED = {"01", "03"}
PF_CODES = re.compile(r"^(4018|4828|4837|4904|4905|7142|8491|8492)")
LAB_COVID = {
    "351123", "351121", "350998", "350506", "351015", "351016", "350507",
    "351024", "350508", "351017", "351139", "350509", "351122",
}
CANCER_PDX = re.compile(r"^(C|D[0-4])", re.I)


class AipnError(RuntimeError):
    """The export cannot be completed; nothing should be sent."""


# ------------------------------------------------------------------ helpers
def money(value) -> str:
    return f"{dec(value):.2f}"


def number(value) -> str:
    d = dec(value)
    return str(int(d)) if d == d.to_integral_value() else format(d.normalize(), "f")


def field_text(value) -> str:
    """CalData: this format has no escaping, so delimiters and markup are replaced."""
    data = "" if value is None else str(value)
    data = data.replace("<", "น้อยกว่า").replace(">", "มากกว่า").replace("&", " and ").replace("|", " ")
    data = data.replace("\r", "").replace("\n", "")
    return data.strip()


def row(*fields) -> str:
    return "|".join(field_text(f) for f in fields) + CRLF


def moment(day, time) -> datetime | None:
    d, t = as_date(day), clock(time)
    if d is None:
        return None
    return datetime.combine(d, t if t is not None else datetime.min.time())


def iso(value: datetime | None) -> str:
    return f"{value:%Y-%m-%dT%H:%M:%S}" if value else ""


def admit_at(adm: dict) -> datetime | None:
    return moment(adm.get("regdate"), adm.get("regtime"))


def discharge_at(adm: dict) -> datetime | None:
    return moment(adm.get("dchdate"), adm.get("dchtime"))


def main_inscl(adm: dict) -> str:
    return text(adm.get("maininscl")) or text(adm.get("hipdata_code"))


def hmain(adm: dict) -> str:
    """HospMain for the file. The old program sent it empty for SSI (disability) claims."""
    return "" if main_inscl(adm) == "SSI" or text(adm.get("hipdata_code")) == "SSI" else text(adm.get("hospmain"))


def pay_plan(adm: dict) -> str:
    """UPayPlan (aipn.txt:1336)."""
    upp = text(adm.get("upp_code"))
    if upp:
        return upp
    if text(adm.get("hipdata_code")) == "SSI":
        return "86"
    eclaim = text(adm.get("pttype_eclaim_id"))
    table = {"17": "10", "19": "20", "20": "21", "33": "31", "16": "40", "18": "40",
             "10": "80", "13": "81", "11": "95", "14": "85"}
    if eclaim in table:
        return table[eclaim]
    if eclaim in {"12", "15"}:
        return "95" if text(adm.get("copttype")) else "85"
    if eclaim and "01" <= eclaim <= "09":
        return "90"
    return "00"


def default_care_as(hospmain: str, hcode: str, hosp_main: Iterable[str]) -> str:
    """CareAs the old program chose before the user changed it (aipn.txt:1085)."""
    contracts = ",".join(hosp_main)
    if not hospmain:
        return "B"
    if hcode in contracts and hospmain not in hcode:
        return "A"
    if hcode in contracts or hcode == hospmain:
        return "M"
    if hospmain not in contracts:
        return "A"
    return "B"


def auth(adm: dict) -> tuple[str, str]:
    code = text(adm.get("auth_code"))
    if not code:
        return "", "0000-00-00T00:00:00"
    when = adm.get("auth_datetime")
    return code, iso(when if isinstance(when, datetime) else admit_at(adm))


# -------------------------------------------------------------- bill items
def item_type(item: dict) -> str:
    return "H" if text(item.get("group1")) in {"3", "4"} and text(item.get("item_type")) == "H" else ""


def bill_group(item: dict) -> str:
    if item_type(item) == "H":
        return "04"
    return text(item.get("group1")).zfill(2)


def item_key(item: dict) -> str:
    """What the user ticks to leave an item out: icode plus the home-medication marker."""
    return f"{text(item.get('icode'))}:{item_type(item)}"


def exported(item: dict) -> bool:
    """The old export skipped items with no name or named 'ยา.' (aipn.txt:1528)."""
    name = item.get("name")
    return name is not None and not str(name).startswith("ยา.")


def is_excluded(item: dict, excluded: set[str], included: set[str]) -> bool:
    key = item_key(item)
    if key in excluded:
        return True
    return bool(item.get("hosxp_noexport")) and key not in included


def _claimcat_for(icode: str, day, catalog: list[dict]) -> dict | None:
    for c in catalog:
        if text(c.get("icode")) == icode and c.get("start_date") and c.get("finish_date"):
            if as_date(c["start_date"]) <= day <= as_date(c["finish_date"]):
                return c
    return None


def claim_sys(inscl: str) -> str:
    if inscl == "UCS":
        return "UC"
    if re.search("OFC|LGO|BKK|PTY|KKT", inscl):
        return "CS"
    if inscl.startswith("SS"):
        return "SS"
    if inscl == "INS":
        return "RTAA"
    return "ST" if inscl.startswith("ST") else "XX"


@dataclass
class BillRow:
    servdate: date
    income: str
    icode: str
    name: str
    qty: Decimal
    unitprice: Decimal
    amount: Decimal
    discount: Decimal
    claim_sys: str
    billgroup: str
    cscode: str
    codesys: str
    stdcode: str
    claimcat: str
    daterev: str
    claimup: Decimal
    claimamt: Decimal
    pttype: str
    key: str
    sort_group: str = ""

    @property
    def claim_money(self) -> Decimal:
        return min(self.amount, self.claimamt)


def bill_rows(items: list[dict], catalog: list[dict], diag: str = "", cancer: bool = False,
              excluded: set[str] | None = None, included: set[str] | None = None) -> list[BillRow]:
    """Group items and apply claim rules (aipn.txt:1476-1609). Returns every row, including zero amounts."""
    excluded, included = excluded or set(), included or set()
    groups: "OrderedDict[tuple, list[dict]]" = OrderedDict()
    for item in items:
        if not exported(item) or is_excluded(item, excluded, included):
            continue
        key = (text(item.get("icode")), text(item.get("pttype")), text(item.get("paidst")), item_type(item))
        groups.setdefault(key, []).append(item)

    rows = []
    for (icode, pttype, paidst, _), members in groups.items():
        first = members[0]
        servdate = max(as_date(m.get("rxdate")) for m in members)
        qty = sum((dec(m.get("qty")) for m in members), Decimal("0"))
        amount = sum((dec(m.get("sum_price")) for m in members), Decimal("0"))
        discount = sum((dec(m.get("sum_price")) for m in members if text(m.get("paidst")) == "04"), Decimal("0"))
        group = bill_group(first)
        drug = text(first.get("group1")) in {"3", "4"}
        cscode = text(first.get("tmtid")) if drug else text(first.get("nhso_adp_code"))
        pre_cat = "X" if paidst in NOT_CLAIMED else ("T" if first.get("ssocac_item") else "D")
        if qty < 0:
            pass
        elif amount < 0:
            qty = -qty
        cc = _claimcat_for(icode, servdate, catalog)
        rate = dec(cc.get("claim_amount")) if cc and text(cc.get("claim_amount")) else Decimal("0")
        md = text(cc.get("income")) if cc else ""
        remark = text(cc.get("remark")) if cc else ""
        if pre_cat == "X":
            claimcat = "X"
        elif cc and cc.get("claim_cat") is not None:
            if re.search("SSOCAC", remark):
                claimcat = "T" if cancer else "D"
            elif re.search("COVID", remark):
                claimcat = "T" if diag == "U071" else "D"
            else:
                claimcat = "T"
        else:
            claimcat = pre_cat
        if group == "04":
            final_group = "04"
        elif cc and cc.get("bill_code") is not None:
            final_group = md
        else:
            final_group = group
        if md == "02" and claimcat == "D":
            final_group = "05"
        unit = abs(dec(first.get("unitprice")))
        claimup = unit if paidst in NOT_CLAIMED else (rate if rate > 0 else unit)
        rev = cc.get("rev_date") if cc else None
        rows.append(BillRow(
            servdate=servdate, income=text(first.get("income")), icode=icode,
            name=" ".join(p for p in (text(first.get("name")), text(first.get("strength"))) if p),
            qty=qty, unitprice=unit, amount=amount, discount=discount,
            claim_sys=claim_sys(text(first.get("maininscl"))), billgroup=final_group,
            cscode="" if group in {"03", "04"} else cscode,
            codesys="TMT" if group in {"03", "04"} else ("TMLT" if group in {"06", "07"} else ""),
            stdcode=cscode if group in {"03", "04"} else (text(first.get("tmlt_code")) if group in {"06", "07"} else ""),
            claimcat=claimcat,
            daterev=f"{as_date(rev):%Y-%m-%d}" if as_date(rev) else "0000-00-00",
            claimup=claimup, claimamt=qty * claimup, pttype=pttype, key=item_key(first), sort_group=group,
        ))
    # The old query ordered by the bill group before claimcat.income could change it.
    rows.sort(key=lambda r: (r.sort_group, r.icode, r.servdate))
    return rows


def amounts(rows: list[BillRow]) -> dict[str, str]:
    drg = sum((r.amount for r in rows if r.claimcat == "D"), Decimal("0"))
    xdrg = sum((r.claim_money for r in rows if r.claimcat == "T"), Decimal("0"))
    sent = sum((r.amount for r in rows), Decimal("0"))
    return {"send_amount": money(sent), "drg_charge": money(drg), "xdrg_claim": money(xdrg)}


# ------------------------------------------------------------------ prices
def chi_price(item: dict) -> Decimal | None:
    """Unit price in the สกส. catalog: drug catalog first, then the lab catalog."""
    for name in ("catalog_price", "lab_price"):
        if item.get(name) is not None:
            return dec(item[name])
    return None


def price_wrong(item: dict) -> bool:
    unit = dec(item.get("unitprice"))
    return any(item.get(n) is not None and unit != dec(item[n]) for n in ("catalog_price", "lab_price"))


def apply_prices(items: list[dict], prices: dict[str, Decimal] | None) -> list[dict]:
    """Use the unit price the user set in this program. HOSxP keeps its own price.

    sum_price becomes qty x new price; the HOSxP values stay in hosxp_unitprice/hosxp_sum_price.
    """
    out = []
    for item in items:
        item = dict(item)
        item.setdefault("hosxp_unitprice", item.get("unitprice"))
        item.setdefault("hosxp_sum_price", item.get("sum_price"))
        price = (prices or {}).get(item_key(item))
        if price is not None:
            item["unitprice"] = price
            item["sum_price"] = (dec(item.get("qty")) * price).quantize(Decimal("0.01"))
            item["price_edited"] = True
        out.append(item)
    return out


def price_label(item: dict) -> str:
    chi = chi_price(item)
    hosxp = dec(item.get("unitprice"))
    return (f"{text(item.get('icode'))} {text(item.get('name'))}: ราคา สกส. {money(chi)} บาท "
            f"(ส่ง {money(hosxp)} บาท)")


# ------------------------------------------------------------------ checks
def _names(items: Iterable[dict]) -> list[str]:
    seen = OrderedDict()
    for i in items:
        label = " ".join(p for p in (text(i.get("icode")), text(i.get("name"))) if p)
        seen[label] = None
    return list(seen)


def check_admission(adm: dict, items: list[dict], ops: list[dict], hosp_main: Iterable[str],
                    missing_debt: bool = False, excluded: set[str] | None = None,
                    included: set[str] | None = None) -> tuple[list[Issue], dict[str, list[str]]]:
    """Rules from aipn.txt:1168-1198. Returns the issues and the items behind each one.

    The old program checked every item; item checks here skip items that will not be sent,
    so a problem item can be left out instead of blocking the whole admission.
    """
    all_items = items
    items = [i for i in items if not is_excluded(i, excluded or set(), included or set())]
    issues: list[Issue] = []
    detail: dict[str, list[str]] = {}

    def add(code: str, level: str, message: str, related: Iterable[str] = ()) -> None:
        issues.append(Issue(code, level, message))
        related = list(related)
        if related:
            detail[code] = related

    if not text(adm.get("cid")):
        add("CID_EMPTY", ERROR, "ไม่มีเลขที่บัตรประชาชน")
    clinic = text(adm.get("clinic")).zfill(2)
    if not "01" <= clinic <= "12":
        add("CLINIC_INVALID", ERROR, "รหัสคลินิกไม่ตรงมาตรฐาน")
    inscl = main_inscl(adm)
    if not text(adm.get("hospmain")) and inscl != "SSI":
        add("HOSPMAIN_EMPTY", ERROR, "ไม่มี HospMain")
    income = dec(adm.get("income"))
    if income == 0:
        add("INCOME_ZERO", ERROR, "ค่าใช้จ่ายเป็น 0")
    pdx = text(adm.get("pdx")).upper()
    if not pdx:
        add("PDX_EMPTY", ERROR, "ไม่มี PDx")
    elif pdx[:1] in "VWXY":
        add("PDX_EXTERNAL", ERROR, "PDx ผิด (เป็นรหัส V-Y)")
    # HOSxP's own amounts: a price changed in this program does not change an_stat.
    total = sum((dec(i.get("hosxp_sum_price", i.get("sum_price"))) for i in all_items), Decimal("0"))
    if income != total:
        add("INCOME_MISMATCH", ERROR, "ค่าใช้จ่ายรวมไม่เท่ากับรายการ กรุณาตรวจรายละเอียดค่าใช้จ่าย")

    start, end = admit_at(adm), discharge_at(adm)
    no_date = [o for o in ops if o.get("opdate") is None or o.get("enddate") is None]
    if no_date:
        add("OP_DATE_EMPTY", ERROR, "ไม่ระบุวันที่ทำหัตถการ", [text(o.get("icd9")) for o in no_date])
    if start and end:
        outside = []
        for o in ops:
            begin, finish = moment(o.get("opdate"), o.get("optime")), moment(o.get("enddate"), o.get("endtime"))
            inside = any(t is not None and start <= t <= end for t in (begin, finish))
            if not inside and (begin or finish):
                outside.append(text(o.get("icd9")))
        if outside:
            add("OP_OUTSIDE", ERROR, "ทำหัตถการนอกช่วงเวลา Admit", outside)
    no_op_doctor = [o for o in ops if not text(o.get("doctor"))]
    if no_op_doctor:
        add("OP_DOCTOR_EMPTY", ERROR, "ไม่พบรหัสแพทย์ที่ทำหัตถการ", [text(o.get("icd9")) for o in no_op_doctor])

    priced_drugs = [i for i in items if i.get("is_drug") and dec(i.get("sum_price")) != 0]
    not_in_catalog = [i for i in priced_drugs if not i.get("in_catalog")]
    if not_in_catalog:
        add("DRUG_NOT_IN_CATALOG", ERROR, "ยาไม่อยู่ใน Drug Catalog ของ สกส.", _names(not_in_catalog))
    tmt_wrong = [
        i for i in priced_drugs
        if exported(i) and text(i.get("tpu_code_list")) != text(i.get("tmtid"))
    ]
    if tmt_wrong:
        add("TMT_MISMATCH", ERROR, "Map TMT ไม่ตรงกับ สกส.", _names(tmt_wrong))
    no_group = [i for i in items if text(i.get("group1")).zfill(2) == "00"]
    if no_group:
        add("BILLGROUP_EMPTY", ERROR, "ยัง Map BillGroup ไม่ครบถ้วน", _names(no_group))
    no_tmlt = [i for i in items if text(i.get("group1")) in {"6", "7"} and not i.get("in_labitem")]
    if no_tmlt:
        add("TMLT_INCOMPLETE", ERROR, "Map TMLT ยังไม่ครบ", _names(no_tmlt))
    no_name = [i for i in items if i.get("name") is None or text(i.get("name")) == ""]
    if no_name:
        add("ITEM_NAME_EMPTY", ERROR, "ไม่มีชื่อรายการบางรหัส", [text(i.get("icode")) for i in no_name])
    if not text(adm.get("doctor")):
        add("DOCTOR_EMPTY", ERROR, "ไม่พบรหัสแพทย์ที่วินิจฉัย")
    if not text(adm.get("dchstts_name")):
        add("DCHSTTS_EMPTY", ERROR, "ไม่พบสถานะจำหน่าย")
    if not text(adm.get("dchtype_name")):
        add("DCHTYPE_EMPTY", ERROR, "ไม่พบประเภทจำหน่าย")
    if text(adm.get("pttype_eclaim_id")) not in {"10", "11", "12", "13", "14", "15"}:
        add("PTTYPE_WRONG", ERROR, "สิทธิการเบิกผิดพลาด (pttype_eclaim_id ไม่ใช่ 10-15)")
    other_hn = [i for i in all_items if text(i.get("hn")) != text(adm.get("hn"))]
    if other_hn:
        add("OTHER_HN", ERROR, "พบ HN อื่นในรายการ", _names(other_hn))

    if start and end:
        outside_rx = [
            i for i in items
            if as_date(i.get("rxdate")) and not start.date() <= as_date(i.get("rxdate")) <= end.date()
        ]
        if outside_rx:
            add("RX_OUTSIDE", WARN, "ให้บริการนอกช่วงเวลา Admit", _names(outside_rx))
    if missing_debt:
        add("DEBT_EMPTY", WARN, "กรุณาระบุวงเงินของสิทธิร่วม")
    wrong = OrderedDict((price_label(i), None) for i in items if exported(i) and price_wrong(i))
    if wrong:
        add("UNITPRICE_WRONG", WARN, "ราคาต่อหน่วยไม่ตรงกับราคา สกส.", list(wrong))
    return issues, detail


def special_case(adm: dict, items: list[dict]) -> str:
    """Claims this version cannot build: they need fields the user set by hand in the old program."""
    if text(adm.get("ods")) and text(adm.get("ods")) not in {"0", "000"}:
        return "ODS"
    if any(PF_CODES.match(text(i.get("nhso_adp_code"))) and text(i.get("nhso_adp_type_id")) in {"2", "19"} for i in items):
        return "ฟอกเลือด (PF)"
    if CANCER_PDX.match(text(adm.get("pdx"))) and text(adm.get("cancer_caseno")):
        return "มะเร็ง (SSOCAC)"
    codes = {text(i.get("nhso_adp_code")) for i in items}
    if codes & LAB_COVID or text(adm.get("pdx")).upper().startswith("U071"):
        return "โควิด"
    return ""


def send_status(adm: dict, local_sent: bool) -> str:
    status = text(adm.get("rcm_status"))
    if status:
        return status
    return "ส่งออกแล้ว" if local_sent else ""


def review(adm: dict, items: list[dict], ops: list[dict], hosp_main: Iterable[str],
           local_sent: bool = False, missing_debt: bool = False,
           excluded: set[str] | None = None, included: set[str] | None = None) -> dict:
    issues, detail = check_admission(adm, items, ops, hosp_main, missing_debt, excluded, included)
    special = special_case(adm, items)
    status = send_status(adm, local_sent)
    levels = {i.level for i in issues}
    selectable = ERROR not in levels and not special
    return {
        "issues": [{**i.__dict__, "items": detail.get(i.code, [])} for i in issues],
        "special": special,
        "status": status,
        "selectable": selectable,
        "already_sent": status in {"ส่งแล้ว", "ชดเชยแล้ว", "ส่งออกแล้ว"},
        "default_selected": selectable and not status,
        "has_error": ERROR in levels,
        "has_warn": WARN in levels,
    }


# --------------------------------------------------------------------- XML
@dataclass
class Claim:
    adm: dict
    adt: dict
    dx: list[dict]
    ops: list[dict]
    rows: list[BillRow]
    copay: list[dict] = field(default_factory=list)
    care_as: str = "B"


def adt_line(adt: dict) -> str:
    keys = ("an", "hn", "idtype", "pidpat", "title", "namepat", "dob", "sex", "marry", "changwat", "ampur",
            "nation", "admtype", "admsource", "dtadm", "dtdisch", "leaveday", "dischs", "discht", "bw",
            "ward", "clinic")
    return row(*(adt.get(k) for k in keys))


def dx_lines(dx: list[dict]) -> list[str]:
    lines = []
    for n, d in enumerate(dx, 1):
        klass = "ICD-10-TM" if d.get("is_tm") or text(d.get("codeset")) == "TT" else "ICD-10"
        lines.append(row(n, d.get("subclass"), klass, d.get("code"), d.get("name"), d.get("doctor"), ""))
    return lines


def op_lines(ops: list[dict]) -> list[str]:
    """One row per procedure code; the first entered row wins (old query: GROUP BY code)."""
    seen: "OrderedDict[str, dict]" = OrderedDict()
    for o in ops:
        code = "+".join(p for p in (text(o.get("icd9")), text(o.get("ext_code"))) if p)
        seen.setdefault(code, o)
    lines = []
    for n, (code, o) in enumerate(seen.items(), 1):
        admit = moment(o.get("regdate"), o.get("regtime"))
        begin = moment(o.get("opdate"), o.get("optime")) if o.get("opdate") is not None else admit
        finish = moment(o.get("enddate"), o.get("endtime")) if o.get("enddate") is not None else admit
        lines.append(row(n, "ICD9CM", code, o.get("name"), o.get("doctor"), iso(begin), iso(finish), "XXXX:Undefine"))
    return lines


def bill_lines(rows: list[BillRow]) -> list[str]:
    out = []
    for n, r in enumerate((r for r in rows if r.amount != 0), 1):
        out.append(row(
            n, f"{r.servdate:%Y-%m-%d}", r.income, r.icode, r.name, number(r.qty), money(r.unitprice),
            money(r.amount), money(r.discount), "0", "0", r.claim_sys, r.billgroup, r.cscode, r.codesys,
            r.stdcode, r.claimcat, r.daterev, money(r.claimup), money(r.claimamt),
        ))
    return out


def ins_type(copay: dict, pdx: str) -> str:
    code, ident = text(copay.get("code")), text(copay.get("id"))
    social = ident in {"10", "11", "12", "13", "14", "15"} or code == "SS"
    if ident == "28" or code in {"A9", "INS"}:
        return "RTAA"
    if ident == "14":
        return "SSEM72"
    if social and pdx.upper().startswith("O"):
        return "MSSDLV"
    return "SSEC" if social else ""


def coinsurance(claim: Claim) -> list[str]:
    lines = []
    for c in claim.copay:
        kind = ins_type(c, text(claim.adm.get("pdx")))
        if not kind:
            continue
        total = c.get("total")
        if total is None:
            total = sum((r.amount for r in claim.rows if r.pttype == text(c.get("pttype")) and r.claimcat != "X"),
                        Decimal("0"))
        lines += [
            "\t<Insurance>", f"\t\t<InsTypeCode>{kind}</InsTypeCode>", f"\t\t<InsTotal>{money(total)}</InsTotal>",
            "\t\t<InsRoomBoard>0.00</InsRoomBoard>", "\t\t<InsProfFee>0.00</InsProfFee>",
            f"\t\t<InsOther>{money(total)}</InsOther>", "\t</Insurance>",
        ]
    return lines


def claim_xml(claim: Claim, hcode: str, hname: str, now: datetime, appeal: str = "") -> bytes:
    adm = claim.adm
    code, auth_dt = auth(adm)
    totals = amounts(claim.rows)
    dx, ops, bills = dx_lines(claim.dx), op_lines(claim.ops), bill_lines(claim.rows)
    head = CRLF.join([
        "<CIPN>", "<Header>",
        "\t<DocClass>IPClaim</DocClass>",
        f'\t<DocSysID version="2.1">AIPN{APPEALS[appeal]}</DocSysID>',
        "\t<serviceEvent>ADT</serviceEvent>",
        f"\t<authorID>{hcode}</authorID>",
        f"\t<authorName>{field_text(hname)}</authorName>",
        f"\t<effectiveTime>{iso(now)}</effectiveTime>",
        "\t<Prgs>RCM</Prgs>", "</Header>", "<ClaimAuth>",
        f"\t<AuthCode>{field_text(code)}</AuthCode>",
        f"\t<AuthDT>{auth_dt}</AuthDT>",
        f"\t<UPayPlan>{pay_plan(adm)}</UPayPlan>",
        "\t<ServiceType>IP</ServiceType>",
        "\t<ProjectCode></ProjectCode>", "\t<EventCode></EventCode>", "\t<UserReserve></UserReserve>",
        f"\t<Hmain>{hmain(adm)}</Hmain>",
        f"\t<Hcare>{hcode}</Hcare>",
        f"\t<CareAs>{claim.care_as}</CareAs>",
        "\t<ServiceSubType></ServiceSubType>", "</ClaimAuth>", "<IPADT>",
    ])
    body = (
        head + CRLF + adt_line(claim.adt) + "</IPADT>" + CRLF
        + f'<IPDx Reccount="{len(dx)}">' + CRLF + "".join(dx) + "</IPDx>" + CRLF
        + f'<IPOp Reccount="{len(ops)}">' + CRLF + "".join(ops) + "</IPOp>" + CRLF
        + "<Invoices>" + CRLF
        + f"<InvNumber>{text(adm.get('an'))}</InvNumber>" + CRLF
        + f"<InvDT>{iso(now)}</InvDT>" + CRLF
        + f'<BillItems Reccount="{len(bills)}">' + CRLF + "".join(bills) + "</BillItems>" + CRLF
        + "<InvAddDiscount>0.00</InvAddDiscount>" + CRLF
        + f"<DRGCharge>{totals['drg_charge']}</DRGCharge>" + CRLF
        + f"<XDRGClaim>{totals['xdrg_claim']}</XDRGClaim>" + CRLF
        + "</Invoices>" + CRLF
        + CRLF.join(["<Coinsurance>", *coinsurance(claim), "</Coinsurance>", "</CIPN>"]) + CRLF
    )
    try:
        encoded = body.encode(ENCODING)
    except UnicodeEncodeError as exc:
        raise AipnError(f"AN {text(adm.get('an'))} มีอักขระที่ใช้กับ windows-874 ไม่ได้: {exc.object[exc.start]!r}") from exc
    checksum = hashlib.md5(encoded).hexdigest().upper()
    return ('<?xml version="1.0" encoding="windows-874"?>' + CRLF).encode(ENCODING) + encoded + \
        f'<?EndNote HMAC="{checksum}"?>'.encode(ENCODING)


def normalise_session(session: str) -> str:
    value = text(session)
    if not value.isdigit() or not 10000 <= int(value) <= 99999:
        raise AipnError("Session No ของ AIPN ต้องเป็นตัวเลข 5 หลัก ตั้งแต่ 10000 ถึง 99999")
    return value


@dataclass
class AipnResult:
    zip_path: Path
    files: list[Path]
    count: int
    total: Decimal


def build_zip(claims: list[Claim], hcode: str, hname: str, session: str, appeal: str,
              output_root: Path, now: datetime | None = None) -> AipnResult:
    if not claims:
        raise AipnError("ไม่มีรายการสำหรับส่งออก")
    if not hcode or not hname:
        raise AipnError("ยังไม่ได้ตั้งค่า HCODE หรือ HNAME")
    if appeal not in APPEALS:
        raise AipnError("ประเภทการส่งไม่ถูกต้อง")
    session = normalise_session(session)
    now = (now or datetime.now()).replace(microsecond=0)
    zip_name = f"{hcode}AIPN{session}.ZIP"
    folder = output_root / f"AIPN_{session}_{now:%Y%m%d-%H%M%S}"
    files, total = [], Decimal("0")
    try:
        folder.mkdir(parents=True, exist_ok=False)
        for claim in claims:
            an = text(claim.adm.get("an")).replace("/", "=")
            data = claim_xml(claim, hcode, hname, now, appeal)
            path = folder / f"{hcode}-AIPN-{an}-{now:%Y%m%d%H%M%S}{APPEALS[appeal]}.XML"
            path.write_bytes(data)
            files.append(path)
            total += sum((r.amount for r in claim.rows), Decimal("0"))
        zip_path = folder / zip_name
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, path.name)
        with zipfile.ZipFile(zip_path) as archive:
            if archive.testzip() is not None or len(archive.namelist()) != len(files):
                raise AipnError("สร้างไฟล์ ZIP ไม่สำเร็จ")
    except (OSError, zipfile.BadZipFile) as exc:
        raise AipnError(f"บันทึกไฟล์ส่งออกไม่สำเร็จ: {exc.__class__.__name__}") from exc
    return AipnResult(zip_path, files, len(files), total)
