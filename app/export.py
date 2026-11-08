"""Build the SSOP claim files and ZIP. Layout comes from ssop.txt ExportData/ReadData/CalData.

Only normal claims are built here: special claim types are stopped earlier by
``validate.special_case``.
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .config import Settings
from .validate import PATIENT_PAID, begin_datetime, clinic_code, clinic_valid, clock, dec, end_datetime, pay_plan, text

CRLF = "\r\n"
ENCODING = "cp874"
DOCTOR_PREFIXES = "วทพภ-"


class ExportError(RuntimeError):
    """The export cannot be completed; nothing should be sent."""


@dataclass
class ExportResult:
    zip_path: Path
    folder: Path
    files: list[Path] = field(default_factory=list)
    bill_count: int = 0
    disp_count: int = 0
    service_count: int = 0
    claim_total: Decimal = Decimal("0")


def money(value) -> str:
    return f"{dec(value):.2f}"


def number(value) -> str:
    """Plain number with no trailing zeros (quantities)."""
    d = dec(value)
    return str(int(d)) if d == d.to_integral_value() else format(d.normalize(), "f")


def clean(value, name: str = "") -> str:
    """Sanitise one field (CalData): this format has no escaping, so remove delimiters."""
    data = "" if value is None else str(value)
    data = data.replace("<", "น้อยกว่า").replace(">", "มากกว่า").replace("&", " and ")
    data = data.replace("|", " ")
    data = re.sub(r"[\"'\r\n\t]", "", data)
    if name == "STDCODE":
        data = data.replace(".", "")
    data = re.sub(r"\.{2,}", ".", data)
    data = re.sub(r" {2,}", " ", data)
    return data.strip()


def line(*fields) -> str:
    return "|".join(clean(f) for f in fields) + CRLF


def doctor_license(value, default: str) -> str:
    license_no = text(value)
    if len(license_no) > 1 and license_no[0] in DOCTOR_PREFIXES and license_no[1].isdigit():
        return license_no
    if license_no and "ก" <= license_no[0] <= "ฮ":
        return "-99999"
    return default


def clean_id(value) -> str:
    return re.sub(r"[\s-]", "", text(value))


def inv_no(visit: dict, settings: Settings) -> str:
    value = text(visit.get("vn")) if settings.use_vn_as_invno else text(visit.get("debt_id"))
    return value.split("#")[0].strip()


def bill_no(visit: dict) -> str:
    return re.sub(r"[\"']", "", text(visit.get("rcpno")).split("#")[0]).replace(":", "/").strip()


def type_in(code) -> str:
    return text(code) if text(code) in {"1", "2", "3", "4"} else "9"


def type_out(code) -> str:
    value = text(code)
    if value in {"1", "2", "3", "4"}:
        return value
    return {"5": "4", "6": "4", "8": "5"}.get(value, "9")


def std_code(item: dict) -> str:
    adp, group = text(item.get("nhso_adp_code")), text(item.get("cscd_group"))
    if adp in {"DRUGP", "991001"}:
        return "991001"
    if group in {"6", "7"}:
        return adp
    if group in {"3", "4"}:
        return text(item.get("sks_drug_code")) or text(item.get("tpu_code_list")) or (
            "0000000" if "HERB" in adp.upper() else ""
        )
    return adp or text(item.get("tpu_code_list"))


def item_name(item: dict) -> str:
    return " ".join(p for p in (text(item.get("name")), text(item.get("strength"))) if p)


def is_excluded(item: dict, excluded: set[tuple[str, str]]) -> bool:
    return bool(item.get("hosxp_noexport")) or (text(item.get("vn")), text(item.get("icode"))) in excluded


def claim_cont(item: dict) -> str:
    group, account = text(item.get("cscd_group")), text(item.get("drugaccount"))
    if group not in {"3", "4"} or (account and account != "-"):
        return "OD"
    return (text(item.get("ned")) or "EC").split(":")[0]


def product_category(item: dict) -> str:
    category = text(item.get("sks_product_category_id"))
    if category:
        return category
    if re.search(r"Thai|สมุนไพร|แผนโบราณ", text(item.get("drugcategory")), re.I):
        return "3"
    return "6" if text(item.get("cscd_group")) == "5" else "1"


def usage_fields(item: dict, settings: Settings) -> tuple[str, str, bool]:
    """Usage code and text for a drug; the bool says a configured default was used."""
    has_special = bool(text(item.get("sp_use_code")))
    code = text(item.get("sp_use_code")) or text(item.get("usage_code"))
    words = text(item.get("sp_use_text")) if has_special else text(item.get("usage_text"))
    return (
        code or settings.default_usage_code,
        words or settings.default_usage_name,
        not code or not words,
    )


def _rx_datetime(item: dict, visit: dict) -> str:
    day = item.get("rxdate")
    moment = clock(item.get("rxtime")) if item.get("rxtime") is not None else clock(visit.get("vsttime"))
    if day is None or moment is None:
        return ""
    return f"{day:%Y-%m-%d} {moment:%H:%M:%S}"


def send_amounts(items: list[dict], excluded: set) -> dict[str, str | int]:
    """Totals for what will actually be sent, using the same rule as the BillTran line."""
    kept = [i for i in items if not is_excluded(i, excluded)]
    income = sum((dec(i.get("sum_price")) for i in kept), Decimal("0"))
    over = sum((dec(i.get("over_debt")) for i in kept), Decimal("0"))
    paid = sum((dec(i.get("sum_price")) for i in kept if text(i.get("paidst")) in PATIENT_PAID), Decimal("0"))
    return {
        "send_amount": money(income), "claim_amount": money(income - paid - over),
        "excluded_count": len(items) - len(kept),
    }


def bill_lines(visit: dict, items: list[dict], settings: Settings, flag: str, excluded: set) -> tuple[str, str, Decimal]:
    kept = [i for i in items if not is_excluded(i, excluded)]
    income = sum((dec(i.get("sum_price")) for i in kept), Decimal("0"))
    over = sum((dec(i.get("over_debt")) for i in kept), Decimal("0"))
    paid = sum((dec(i.get("sum_price")) for i in kept if text(i.get("paidst")) in PATIENT_PAID), Decimal("0"))
    claim = income - paid - over
    begin = begin_datetime(visit)
    inv = inv_no(visit, settings)
    bill = line(
        settings.station, "", f"{begin:%Y-%m-%dT%H:%M:%S}", settings.hcode, inv, bill_no(visit),
        visit.get("hn"), "", money(income), money(paid + over), "", flag,
        clean_id(visit.get("cid")), visit.get("ptname"), visit.get("hospmain"), pay_plan(visit),
        money(claim), "", money(0),
    )
    rows = []
    for item in kept:
        if dec(item.get("sum_price")) <= 0:
            continue
        adp = text(item.get("nhso_adp_code"))
        reimbursable = text(item.get("paidst")) not in PATIENT_PAID
        charge = dec(item.get("qty")) * dec(item.get("unitprice"))
        svref = ("HS:" if adp in {"DRUGP", "991001"} else "") + text(visit.get("vn"))
        rows.append((
            inv, text(item.get("cscd_group")), text(item.get("icode")),
            line(
                inv, f"{begin:%Y-%m-%d}", item.get("cscd_group"), item.get("icode"),
                clean(std_code(item), "STDCODE"), item_name(item), number(item.get("qty")),
                money(item.get("unitprice")), money(charge),
                money(item.get("unitprice") if reimbursable else 0),
                money(charge if reimbursable else 0), svref, "OP1",
            ),
        ))
    return bill, "".join(r[3] for r in sorted(rows, key=lambda r: r[:3])), claim


def disp_lines(visit: dict, items: list[dict], settings: Settings, excluded: set) -> tuple[str, str]:
    kept = [i for i in items if not is_excluded(i, excluded) and dec(i.get("sum_price")) > 0]
    head_rows = [i for i in kept if text(i.get("cscd_group")) in {"3", "5"}]
    head = ""
    vn = text(visit.get("vn"))
    if head_rows:
        # The old query took an arbitrary row (GROUP BY with no aggregate); use the earliest.
        moment = min((_rx_datetime(i, visit) for i in head_rows), default="")
        charge = sum((dec(i.get("sum_price")) for i in head_rows), Decimal("0"))
        paid = sum((dec(i.get("sum_price")) for i in head_rows if text(i.get("paidst")) in PATIENT_PAID), Decimal("0"))
        head = line(
            settings.hcode, vn, inv_no(visit, settings), visit.get("hn"), clean_id(visit.get("cid")),
            moment, moment, doctor_license(visit.get("doctor"), settings.default_doctor),
            sum(1 for i in head_rows if i.get("name") is not None),
            money(charge), money(charge - paid), money(paid), money(0),
            "HP", "SS", 1, vn, "",
        )
    rows = []
    for item in kept:
        if text(item.get("cscd_group")) not in {"3", "4", "5"}:
            continue
        category = product_category(item)
        reimbursable = text(item.get("paidst")) not in PATIENT_PAID
        sig_code, sig_text, _ = usage_fields(item, settings)
        rows.append((
            text(item.get("icode")),
            line(
                vn, category, item.get("icode"),
                text(item.get("sks_drug_code")) or text(item.get("tpu_code_list")),
                item.get("sks_dfs_code"), item_name(item),
                text(item.get("units")) or text(item.get("nd_unit")) or "-",
                sig_code, sig_text, number(item.get("qty")),
                money(item.get("unitprice")), money(item.get("sum_price")),
                money(item.get("unitprice") if reimbursable else 0),
                money(item.get("sum_price") if reimbursable else 0),
                "", claim_cont(item), "OP1", "", "",
            ),
        ))
    return head, "".join(r[1] for r in sorted(rows, key=lambda r: r[0]))


def service_lines(visit: dict, items: list[dict], settings: Settings, excluded: set) -> tuple[str, int]:
    vn = text(visit.get("vn"))
    begin, end = begin_datetime(visit), end_datetime(visit)
    end = max(begin, end)
    code = clinic_code(visit)
    appoint = visit.get("appoint_date")

    def service(sv_id, klass, lc_code, code_set, std) -> str:
        return line(
            inv_no(visit, settings), sv_id, klass, settings.hcode, visit.get("hn"),
            clean_id(visit.get("cid")), "1", "01", type_in(visit.get("typein_code")),
            type_out(visit.get("typeout_code")), f"{appoint:%Y-%m-%d}" if appoint else "",
            doctor_license(visit.get("doctor"), settings.default_doctor),
            code if clinic_valid(code) else "01",
            f"{begin:%Y-%m-%dT%H:%M:%S}", f"{end:%Y-%m-%dT%H:%M:%S}",
            lc_code, code_set, clean(std, "STDCODE"), money(0), "Y", "", "OP1",
        )

    rows = [service(f"{vn}0001", "EC", "", "", "")]
    sequence = 1
    for item in items:
        if is_excluded(item, excluded):
            continue
        icd9 = text(item.get("icd9cm"))
        if icd9[:1].isdigit():
            sequence += 1
            rows.append(service(
                f"{vn}{sequence:04d}", "OP", item.get("icode"),
                "TT" if len(icd9) > 5 else "IN", icd9,
            ))
    return "".join(rows), len(rows)


def dx_lines(visit: dict, dx_rows: list[dict], settings: Settings) -> str:
    vn = text(visit.get("vn"))
    sv_id = f"{vn}0001"

    def codeset(row: dict) -> str:
        if text(row.get("tm_code")):
            return "TT"
        return text(row.get("codeset")) or "IT"

    if settings.pdx_only:
        pdx = text(visit.get("pdx"))
        match = next((r for r in dx_rows if text(r.get("code")) == pdx), {})
        return line("EC", sv_id, "1", codeset(match), pdx, match.get("name"))
    return "".join(
        line("EC", sv_id, r.get("diagtype"), codeset(r), r.get("code"), r.get("name"))
        for r in dx_rows
    )


def wrap(section1: str, data1: str, section2: str, data2: str, settings: Settings,
         flag: str, session: str, when: datetime, count: int) -> bytes:
    """Build one file. The checksum covers everything before the EndNote line."""
    body = CRLF.join([
        '<?xml version="1.0" encoding="windows-874"?>',
        f'<ClaimRec System="OP" PayPlan="SS" Version="0.93" Prgs="RCM" TFlag="{flag}">',
        "<Header>",
        f"<HCODE>{settings.hcode}</HCODE>",
        f"<HNAME>{clean(settings.hname)}</HNAME>",
        f"<DATETIME>{when:%Y-%m-%dT%H:%M:%S}</DATETIME>",
        f"<SESSNO>{session}</SESSNO>",
        f"<RECCOUNT>{count}</RECCOUNT>",
        "</Header>",
        f"<{section1}>",
        f"{data1}</{section1}>",
        f"<{section2}>",
        f"{data2}</{section2}>",
        "</ClaimRec>" + CRLF,
    ])
    try:
        encoded = body.encode(ENCODING)
    except UnicodeEncodeError as exc:
        raise ExportError(f"พบอักขระที่ใช้กับ windows-874 ไม่ได้: {exc.object[exc.start]!r}") from exc
    checksum = hashlib.md5(encoded).hexdigest()
    return encoded + f'<?EndNote Checksum="{checksum}"?>'.encode(ENCODING)


def normalise_session(session: str) -> str:
    value = text(session)
    if not value.isdigit() or len(value) > 4 or int(value) == 0:
        raise ExportError("Session No ต้องเป็นตัวเลข 1-4 หลัก และไม่เป็น 0")
    return value.zfill(4)


def normalise_station(station: str) -> str:
    value = text(station)
    if not value.isdigit() or len(value) > 2 or int(value) == 0:
        raise ExportError("Station ต้องเป็นตัวเลข 01-99")
    return value.zfill(2)


def line_flag(requested: str) -> str:
    if requested not in {"A", "E", "D"}:
        raise ExportError("TFlag ต้องเป็น A, E หรือ D")
    return requested


def build_export(
    visits: Iterable[dict], items_by_vn: dict[str, list[dict]], dx_by_vn: dict[str, list[dict]],
    settings: Settings, session: str, flag: str, file_date, output_root: Path,
    excluded: set[tuple[str, str]] | None = None, now: datetime | None = None,
) -> ExportResult:
    visits = list(visits)
    if not visits:
        raise ExportError("ไม่มีรายการสำหรับส่งออก")
    problems = settings.problems()
    if problems:
        raise ExportError("; ".join(problems))
    session = normalise_session(session)
    flag = line_flag(flag)
    excluded = excluded or set()
    now = now or datetime.now()

    bills, bill_items, disps, disp_items, services, dxs = [], [], [], [], [], []
    disp_count = service_count = 0
    claim_total = Decimal("0")
    for visit in visits:
        vn = text(visit.get("vn"))
        items, dx_rows = items_by_vn.get(vn, []), dx_by_vn.get(vn, [])
        if begin_datetime(visit) is None:
            raise ExportError(f"VN {vn} ไม่มีวันเวลามาตรวจ")
        bill, bill_detail, claim = bill_lines(visit, items, settings, flag, excluded)
        claim_total += claim
        bills.append(bill)
        bill_items.append(bill_detail)
        head, disp_detail = disp_lines(visit, items, settings, excluded)
        if head:
            disp_count += 1
            disps.append(head)
        disp_items.append(disp_detail)
        service, count = service_lines(visit, items, settings, excluded)
        service_count += count
        services.append(service)
        dxs.append(dx_lines(visit, dx_rows, settings))

    stamp = f"{file_date:%Y%m%d}"
    contents = {
        f"BillTran{stamp}.TXT": wrap("BILLTRAN", "".join(bills), "BillItems", "".join(bill_items),
                                     settings, flag, session, now, len(visits)),
        f"BillDisp{stamp}.TXT": wrap("Dispensing", "".join(disps), "DispensedItems", "".join(disp_items),
                                     settings, flag, session, now, disp_count),
        f"OPServices{stamp}.TXT": wrap("OPServices", "".join(services), "OPDx", "".join(dxs),
                                       settings, flag, session, now, service_count),
    }

    zip_name = f"{settings.hcode}_SSOPBIL_{session}_{settings.station}_{now:%Y%m%d-%H%M%S}.ZIP"
    folder = output_root / zip_name[:-4]
    zip_path = folder / zip_name
    files = []
    try:
        folder.mkdir(parents=True, exist_ok=False)
        for name, data in contents.items():
            path = folder / name
            path.write_bytes(data)
            files.append(path)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, path.name)
        with zipfile.ZipFile(zip_path) as archive:
            if archive.testzip() is not None or len(archive.namelist()) != len(files):
                raise ExportError("สร้างไฟล์ ZIP ไม่สำเร็จ")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ExportError(f"บันทึกไฟล์ส่งออกไม่สำเร็จ: {exc.__class__.__name__}") from exc
    return ExportResult(zip_path, folder, files, len(visits), disp_count, service_count, claim_total)
