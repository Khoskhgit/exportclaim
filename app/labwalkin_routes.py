"""LAB Walk-in: out-lab items done for UC patients whose main hospital is another hospital in our province,
shown by date range and exported to Excel for billing those hospitals. HOSxP is only read."""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

import openpyxl
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .config import Settings
from .store import Store
from .validate import dec, text

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEADERS = ["VN", "icode", "เลขบัตรประชาชน", "HN", "ชื่อ-สกุล", "วันที่รับบริการ", "สถานพยาบาลหลัก", "รหัส รพ.หลัก",
           "รายการ LAB", "จำนวน", "ราคาต่อหน่วย", "รวมเงิน"]


def thai_date(value: Any) -> str:
    """yyyy-mm-dd (or a date) to dd/mm/yyyy in the Buddhist year."""
    raw = str(value or "")[:10]
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return raw
    return f"{day.day:02d}/{day.month:02d}/{day.year + 543}"


def mask_cid(cid: str) -> str:
    return f"{'x' * (len(cid) - 4)}{cid[-4:]}" if len(cid) > 4 else cid


def clean(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        code = text(row.get("hospmain"))
        result.append({
            "vn": text(row.get("vn")), "icode": text(row.get("icode")), "cid": text(row.get("cid")),
            "hn": text(row.get("hn")), "ptname": text(row.get("ptname")), "vstdate": str(row.get("vstdate") or "")[:10],
            "hospmain": code, "hospmain_name": f"{code} - {text(row.get('hospmain_name'))}",
            "pttype": " ".join(x for x in (text(row.get("pttype")), text(row.get("pttype_name"))) if x), "pdx": text(row.get("pdx")),
            "lab_name": text(row.get("lab_name")), "qty": dec(row.get("qty")), "unitcost": dec(row.get("unitcost")),
            "sum_price": dec(row.get("sum_price")),
        })
    return result


def summarise(rows: list[dict]) -> list[dict]:
    """Per main hospital: visits, people, items and money."""
    groups: dict[str, dict] = {}
    for row in rows:
        g = groups.setdefault(row["hospmain"], {"hospmain": row["hospmain"], "hospmain_name": row["hospmain_name"],
                                               "vns": set(), "hns": set(), "items": 0, "amount": Decimal(0)})
        g["vns"].add(row["vn"]); g["hns"].add(row["hn"])
        g["items"] += 1; g["amount"] += row["sum_price"]
    return [{"hospmain": g["hospmain"], "hospmain_name": g["hospmain_name"], "visits": len(g["vns"]),
             "people": len(g["hns"]), "items": g["items"], "amount": str(g["amount"])} for g in groups.values()]


def workbook(rows: list[dict], start: date, end: date, hname: str) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "LAB Walk-in"
    bold = Font(bold=True)
    sheet.append([f"LAB Walk-in ในจังหวัด (UC ผู้ป่วยนอก สถานพยาบาลหลักอื่น) {hname}".strip()])
    sheet.append([f"วันที่รับบริการ {thai_date(start)} ถึง {thai_date(end)}"])
    sheet.append(HEADERS)
    for cell in sheet[3]:
        cell.font = bold
    for r in rows:
        sheet.append([r["vn"], r["icode"], r["cid"], r["hn"], r["ptname"], thai_date(r["vstdate"]), r["hospmain_name"],
                      r["hospmain"], r["lab_name"], float(r["qty"]), float(r["unitcost"]), float(r["sum_price"])])
    sheet.append(["รวม", "", "", "", "", "", "", "", f"{len(rows)} รายการ", "", "",
                  float(sum((r["sum_price"] for r in rows), Decimal(0)))])
    for cell in sheet[sheet.max_row]:
        cell.font = bold
    widths = [14, 9, 16, 11, 26, 13, 34, 10, 34, 8, 12, 12]
    for i, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    for row in sheet.iter_rows(min_row=4, min_col=10, max_col=12):
        for cell in row:
            cell.number_format = "#,##0.00"
    for row in sheet.iter_rows(min_row=4, min_col=1, max_col=4):
        for cell in row:
            cell.number_format = "@"
    sheet.freeze_panes = "A4"

    summary = book.create_sheet("สรุปตาม รพ.หลัก")
    summary.append(["สถานพยาบาลหลัก", "จำนวน visit", "จำนวนคน", "จำนวนรายการ", "รวมเงิน"])
    for cell in summary[1]:
        cell.font = bold
    totals = summarise(rows)
    for g in totals:
        summary.append([g["hospmain_name"], g["visits"], g["people"], g["items"], float(g["amount"])])
    summary.append(["รวม", sum(g["visits"] for g in totals), sum(g["people"] for g in totals),
                    sum(g["items"] for g in totals), float(sum((Decimal(g["amount"]) for g in totals), Decimal(0)))])
    for cell in summary[summary.max_row]:
        cell.font = bold
    for i, width in enumerate([40, 12, 12, 14, 14], 1):
        summary.column_dimensions[get_column_letter(i)].width = width
    for row in summary.iter_rows(min_row=2, min_col=5, max_col=5):
        for cell in row:
            cell.number_format = "#,##0.00"

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def register(app: FastAPI, settings: Settings, hosxp: Any, local: Store, templates: Jinja2Templates) -> None:
    def hcode() -> str:
        code = settings.hcode or (settings.hosp_main[0] if settings.hosp_main else "")
        if not code:
            raise HTTPException(400, "ยังไม่ได้ตั้งค่า HCODE")
        return code

    def period(body: dict) -> tuple[date, date]:
        try:
            start, end = date.fromisoformat(str(body.get("start"))), date.fromisoformat(str(body.get("end")))
        except ValueError:
            raise HTTPException(400, "กรุณาเลือกช่วงวันที่") from None
        if start > end:
            raise HTTPException(400, "วันที่เริ่มต้องไม่เกินวันที่สิ้นสุด")
        if (end - start).days > 366:
            raise HTTPException(400, "เลือกช่วงวันที่ได้ไม่เกิน 1 ปี")
        return start, end

    @app.get("/labwalkin", response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse(request, "labwalkin.html", {"hname": settings.hname})

    @app.post("/api/labwalkin")
    def search(body: dict = Body(...)):
        start, end = period(body)
        rows = clean(hosxp.lab_walkin(start, end, hcode()))
        return {
            "rows": [{**r, "cid": mask_cid(r["cid"]), "qty": str(r["qty"]), "unitcost": str(r["unitcost"]),
                      "sum_price": str(r["sum_price"])} for r in rows],
            "hospitals": summarise(rows),
        }

    @app.post("/api/labwalkin/excel")
    def excel(body: dict = Body(...)):
        start, end = period(body)
        rows = clean(hosxp.lab_walkin(start, end, hcode()))
        name = f"LAB_WalkIn_{start:%Y%m%d}_{end:%Y%m%d}.xlsx"
        return Response(workbook(rows, start, end, settings.hname), media_type=XLSX,
                        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
