"""Web app: search visits, review them, choose people, and build the SSOP ZIP."""
from __future__ import annotations

import io
import logging
from collections import defaultdict
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import openpyxl
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import aipn_routes, auth
from .aipn_db import AipnDb
from .config import Settings, load_settings
from .export import ExportError, build_export, normalise_station, is_excluded, item_name, send_amounts, usage_fields
from .hosxp import Hosxp, HosxpError
from .store import Store
from .validate import dec, begin_datetime, end_datetime, explain_result_codes, review_visit, text, tmlt_problem

log = logging.getLogger("ssop")
HERE = Path(__file__).resolve().parent
MAX_UPLOAD = 20 * 1024 * 1024


def _parse_date(value: Any, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(400, f"{label} ไม่ถูกต้อง") from None


def mask_id(value: Any) -> str:
    """The review screen does not need the full national ID."""
    cid = text(value)
    return f"{'x' * (len(cid) - 4)}{cid[-4:]}" if len(cid) > 4 else cid


def mask_host(host: str) -> str:
    """Show only the last two parts of an IPv4 address, e.g. xxx.xxx.1.4."""
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return f"xxx.xxx.{parts[2]}.{parts[3]}"
    return "xxx"


def read_z_excel(data: bytes) -> list[tuple[str, str]]:
    """Read column B (code) and C (description), the layout the old program used."""
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise HTTPException(400, "เปิดไฟล์ไม่ได้ กรุณาใช้ไฟล์ .xlsx") from exc
    found: dict[str, str] = {}
    for row in book.worksheets[0].iter_rows(values_only=True):
        code = text(row[1] if len(row) > 1 else "").upper()
        if code.startswith("Z"):
            found[code] = text(row[2] if len(row) > 2 else "")
    book.close()
    return list(found.items())


def create_app(settings: Settings | None = None, hosxp: Any = None, store: Store | None = None,
               aipn_db: Any = None, moph_fetch: Any = None) -> FastAPI:
    settings = settings or load_settings()
    db = hosxp or Hosxp(settings)
    local = store or Store(settings.data_dir / "ssop.sqlite3")
    templates = Jinja2Templates(directory=str(HERE / "templates"))

    app = FastAPI(title="SSOP Export", docs_url=None, redoc_url=None, openapi_url=None)
    # Stops other websites reaching this local app through DNS rebinding.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.exception_handler(HosxpError)
    async def hosxp_error(_: Request, exc: HosxpError):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(ExportError)
    async def export_error(_: Request, exc: ExportError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def z_state() -> tuple[set[str], bool]:
        codes = local.z_codes() | db.z_codes()
        return codes, bool(codes)

    def group(rows: list[dict]) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            result[text(row.get("vn"))].append(row)
        return result

    def with_inclusions(rows: list[dict]) -> list[dict]:
        """Apply the user's choice to send items HOSxP marks as not exported. HOSxP is not changed."""
        included = local.inclusions({text(r.get("vn")) for r in rows})
        for row in rows:
            row["hosxp_excluded"] = bool(row.get("hosxp_noexport"))
            if (text(row.get("vn")), text(row.get("icode"))) in included:
                row["hosxp_noexport"] = 0
        return rows

    auth.register(app, settings, db, local, templates, **({"moph_fetch": moph_fetch} if moph_fetch else {}))
    aipn_routes.register(app, settings, aipn_db or AipnDb(db), local, templates)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"station": settings.station})

    @app.get("/api/status")
    def status():
        problems = settings.problems()
        if not settings.db_user:
            return {"settings_problems": problems, "database": None, "z_ready": False}
        host = mask_host(settings.db_host)
        try:
            return {
                "settings_problems": problems, "database": db.preflight(), "z_ready": z_state()[1],
                "db_host": host, "z_count": len(z_state()[0]),
            }
        except HosxpError as exc:
            return {"settings_problems": problems, "database": None, "z_ready": False, "db_host": host, "db_error": str(exc)}

    @app.get("/api/pttypes")
    def pttypes():
        return db.pttypes()

    @app.post("/api/search")
    def search(body: dict = Body(...)):
        # A POST body keeps HN and VN out of URLs and access logs.
        visits = db.search_visits(
            _parse_date(body.get("start"), "วันที่เริ่ม"), _parse_date(body.get("end"), "วันที่สิ้นสุด"),
            body.get("pttypes") or [], text(body.get("vn")), text(body.get("hn")),
        )
        vns = [text(v["vn"]) for v in visits]
        items = group(with_inclusions(db.fetch_items(vns)))
        excluded = local.exclusions(vns)
        sent = local.sent_vns(vns)
        z_codes, z_ready = z_state()
        code_names = db.result_code_names()
        result = []
        for visit in visits:
            vn = text(visit["vn"])
            review = review_visit(visit, items.get(vn, []), settings.hosp_main, z_codes, z_ready, vn in sent)
            begin = begin_datetime(visit)
            result.append({
                "vn": vn, "hn": text(visit.get("hn")), "cid": mask_id(visit.get("cid")),
                "ptname": text(visit.get("ptname")),
                "visit_at": begin.isoformat(sep=" ") if begin else "",
                "pdx": text(visit.get("pdx")), "clinic": text(visit.get("clinic_name")),
                "pttype": text(visit.get("pttype_name")), "hospmain": text(visit.get("hospmain")),
                "income": str(dec(visit.get("income"))),
                **send_amounts(items.get(vn, []), excluded),
                "result_codes": explain_result_codes(visit.get("chi_codes"), code_names) if review["status"] == "ติด C" else [],
                **review,
            })
        return {"visits": result, "z_ready": z_ready, "file_date": str(_parse_date(body.get("end"), "วันที่สิ้นสุด") or date.today())}

    @app.post("/api/detail")
    def detail(body: dict = Body(...)):
        vn = text(body.get("vn"))
        if not vn:
            raise HTTPException(400, "ไม่ได้ระบุ VN")
        visits = db.visits_by_vn([vn])
        if not visits:
            raise HTTPException(404, "ไม่พบ VN นี้")
        excluded = local.exclusions([vn])
        items = [
            {
                "icode": text(i.get("icode")), "name": item_name(i), "group": text(i.get("cscd_group")),
                "adp": text(i.get("nhso_adp_code")), "qty": str(dec(i.get("qty"))),
                "unitprice": str(dec(i.get("unitprice"))), "sum_price": str(dec(i.get("sum_price"))),
                "paidst": text(i.get("paidst")), "excluded": is_excluded(i, excluded),
                "hosxp_excluded": i["hosxp_excluded"], "tmlt_problem": tmlt_problem(i),
                "default_usage": usage_fields(i, settings)[1] if (
                    text(i.get("cscd_group")) in {"3", "4", "5"} and usage_fields(i, settings)[2]
                ) else "",
            }
            for i in with_inclusions(db.fetch_items([vn]))
        ]
        dx_rows = [
            {"code": text(d.get("code")), "name": text(d.get("name")), "type": text(d.get("diagtype"))}
            for d in db.fetch_dx([vn])
        ]
        end = end_datetime(visits[0])
        return {"items": items, "dx": dx_rows, "end_time": end.isoformat(sep=" ") if end else ""}

    @app.post("/api/exclusion")
    def exclusion(body: dict = Body(...)):
        vn, icode = text(body.get("vn")), text(body.get("icode"))
        if not vn or not icode:
            raise HTTPException(400, "ข้อมูลไม่ครบ")
        excluded = bool(body.get("excluded"))
        in_hosxp = any(
            text(i.get("icode")) == icode and i.get("hosxp_noexport") for i in db.fetch_items([vn])
        )
        local.set_exclusion(vn, icode, excluded and not in_hosxp)
        local.set_inclusion(vn, icode, not excluded and in_hosxp)
        return {"ok": True, **send_amounts(with_inclusions(db.fetch_items([vn])), local.exclusions([vn]))}

    @app.post("/api/zcodes")
    async def z_upload(request: Request):
        data = await request.body()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "ไฟล์ใหญ่เกินไป")
        rows = read_z_excel(data)
        if not rows:
            raise HTTPException(400, "ไม่พบรหัส Z ในคอลัมน์ B")
        return {"count": local.replace_z_codes(rows)}

    @app.post("/api/export")
    def export(request: Request, body: dict = Body(...)):
        vns = list(dict.fromkeys(text(v) for v in body.get("vns") or [] if text(v)))
        flag = text(body.get("tflag")) or "A"
        session = text(body.get("session"))
        if not vns:
            raise HTTPException(400, "ยังไม่ได้เลือกรายการ")

        # Re-check on the server using fresh data: the screen may be out of date.
        visits = db.visits_by_vn(vns)
        missing = set(vns) - {text(v["vn"]) for v in visits}
        if missing:
            raise HTTPException(400, f"ไม่พบ VN {len(missing)} รายการ")
        items = group(with_inclusions(db.fetch_items(vns)))
        dx_rows = group(db.fetch_dx(vns))
        sent = local.sent_vns(vns)
        z_codes, z_ready = z_state()
        blocked, resend = [], []
        for visit in visits:
            vn = text(visit["vn"])
            review = review_visit(visit, items.get(vn, []), settings.hosp_main, z_codes, z_ready, vn in sent)
            if not review["selectable"]:
                reason = review["special"] or next(i["message"] for i in review["issues"] if i["level"] == "error")
                blocked.append({"vn": vn, "reason": reason})
            elif review["already_sent"] or review["needs_correction"]:
                resend.append(vn)
        if blocked:
            return JSONResponse({"detail": "มีรายการที่ส่งไม่ได้", "blocked": blocked}, status_code=409)
        if resend and flag == "A":
            return JSONResponse(
                {"detail": "มีรายการที่ส่งแล้วหรือติด C ต้องเลือกประเภท E หรือ D หรือเอารายการออก", "resend": resend}, status_code=409,
            )
        if resend and not body.get("confirm_resend"):
            return JSONResponse({"detail": "ต้องยืนยันการส่งซ้ำ", "resend": resend, "needs_confirm": True}, status_code=409)

        file_date = _parse_date(body.get("file_date"), "วันที่ไฟล์") or date.today()
        station = normalise_station(text(body.get("station")) or settings.station)
        result = build_export(
            visits, items, dx_rows, replace(settings, station=station), session, flag, file_date, settings.export_dir,
            excluded=local.exclusions(vns),
        )
        export_id = local.record_export(session.zfill(4), flag, result.zip_path, vns, result.claim_total,
                                        created_by=auth.user_label(request))
        log.info("export id=%s visits=%s", export_id, len(vns))
        return {
            "id": export_id, "file": result.zip_path.name, "bill_count": result.bill_count,
            "disp_count": result.disp_count, "service_count": result.service_count,
            "claim_total": str(result.claim_total),
        }

    @app.get("/api/exports")
    def exports():
        return [{**row, "file": Path(row.pop("zip_path")).name} for row in local.history()]

    @app.get("/api/exports/{export_id}/download")
    def download(export_id: int):
        path = local.export_path(export_id)
        if path is None or not path.is_file():
            raise HTTPException(404, "ไม่พบไฟล์")
        return FileResponse(path, media_type="application/zip", filename=path.name)

    return app


def get_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    return create_app()


