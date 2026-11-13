"""NHSO 18-file page and API: search, review, leave items out, build the ZIP for New e-Claim upload."""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import auth, eclaim_api, fdh, nhso18, nhso18_service as svc
from .config import Settings
from .store import Store
from .aipn import number
from .validate import text

log = logging.getLogger("ssop.nhso18")
ECLAIM_DB = "eclaimdb"


def _date(value: Any, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(400, f"{label} ไม่ถูกต้อง") from None


def _mask(value: Any) -> str:
    cid = text(value)
    return f"{'x' * (len(cid) - 4)}{cid[-4:]}" if len(cid) > 4 else cid


def register(app: FastAPI, settings: Settings, hosxp: Any, local: Store, templates: Jinja2Templates,
             approvals: Any = None, results: Any = None) -> None:
    def q(sql: str) -> list[dict]:
        return hosxp._select(sql, [])

    def with_approve_codes(rows: list[dict]) -> None:
        """OPD visits under the approve rights take the imported Krungthai Approve Code as their claim code."""
        if approvals is None:
            return
        mine = [r for r in rows if r["kind"] == "OPD" and text(r.get("pttype")) in approvals.pttypes]
        if not mine:
            return
        found = approvals.for_visits(mine)
        for r in mine:
            payment = found.get(text(r.get("vn")))
            r["approve_required"] = True
            r["claimcode_hosxp"] = text(r.get("claimcode"))
            r["claimcode"] = payment["appr_code"] if payment else ""
            r["approve_amount"] = payment["amount"] if payment else ""

    @app.exception_handler(nhso18.Nhso18Error)
    async def nhso18_error(_: Request, exc: nhso18.Nhso18Error):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def review(rows: list[dict]) -> list[dict]:
        keys = [svc.key_of(r) for r in rows]
        with_approve_codes(rows)
        found = svc.checks(q, settings.rcm_db, rows)
        sent = local.nhso18_sent(keys)
        fdh_sent = local.fdh_sent(keys)
        eclaim_sent = local.eclaim_sent(keys)
        excluded = local.nhso18_excluded(keys)
        uuc_set = local.nhso18_uuc(keys)
        imported = results.latest(keys) if results is not None else {}
        merge = nhso18.same_day_walkin(rows, svc.walkin_pttypes(q))
        firsts = set(merge.values())
        out = []
        for r in rows:
            k = svc.key_of(r)
            f = found[k]
            label = "ส่ง FDH แล้ว" if k in fdh_sent else "ส่ง e-Claim แล้ว" if k in eclaim_sent else ""
            st = svc.status(r, k in sent, label, imported.get(k))
            moment = r.get("date")
            out.append({
                "key": k, "kind": r["kind"], "vn": text(r.get("vn")), "an": text(r.get("an")), "hn": text(r.get("hn")),
                "cid": _mask(r.get("cid")), "ptname": text(r.get("ptname")),
                "date": f"{moment:%Y-%m-%d}" if moment else "", "pttype": f"{text(r.get('pttype'))}: {text(r.get('pttype_name'))}",
                "maininscl": text(r.get("maininscl")), "pdx": text(r.get("pdx")), "claimcode": text(r.get("claimcode")),
                "approve_required": bool(r.get("approve_required")), "claimcode_hosxp": text(r.get("claimcode_hosxp")),
                "money": svc.money(r.get("money_total")), "fdh_amount": svc.money(r.get("fdh_amount")) if r.get("fdh_amount") else "",
                "errors": f["errors"], "warns": f["warns"], "status": st,
                "excluded_count": sum(1 for s, _ in excluded if s == k), "uuc_set": uuc_set.get(k, ""),
                "selectable": not f["errors"], "has_error": bool(f["errors"]), "has_warn": bool(f["warns"]),
                "already_sent": bool(st), "default_selected": not f["errors"] and not st,
                "walkin_first": merge.get(k, ""), "walkin_group": merge.get(k) or (k if k in firsts else ""),
            })
        return out

    @app.get("/nhso18", response_class=HTMLResponse)
    def page(request: Request):
        return templates.TemplateResponse(request, "nhso18.html", {"hname": settings.hname})

    @app.get("/api/nhso18/pttypes")
    def pttypes():
        return svc.pttypes(q)

    @app.post("/api/nhso18/search")
    def search(body: dict = Body(...)):
        opd, ipd = bool(body.get("opd", True)), bool(body.get("ipd", False))
        vn, an, hn = text(body.get("vn")), text(body.get("an")), text(body.get("hn"))
        start, end = _date(body.get("start"), "วันที่เริ่ม"), _date(body.get("end"), "วันที่สิ้นสุด")
        rights = [text(p) for p in body.get("pttypes") or [] if text(p)]
        if not (vn or an or hn) and not (start and end and rights):
            raise HTTPException(400, "ต้องระบุช่วงวันที่และสิทธิ หรือค้นด้วย VN / AN / HN")
        if not opd and not ipd and not (vn or an):
            raise HTTPException(400, "ต้องเลือก OPD หรือ IPD อย่างน้อย 1 อย่าง")
        rows = svc.search(q, settings.rcm_db, start, end, rights, opd or bool(vn), ipd or bool(an), vn, an, hn)
        return {"rows": review(rows)}

    @app.post("/api/nhso18/detail")
    def detail(body: dict = Body(...)):
        key, kind = text(body.get("key")), text(body.get("kind"))
        if not key:
            raise HTTPException(400, "ไม่ได้ระบุรายการ")
        col = "an" if kind == "IPD" else "vn"
        excluded = local.nhso18_excluded([key])
        items = q(f"""
        SELECT oo.icode, CONCAT_WS(' ',d.name,d.strength) name, inc.drg_chrgitem_id billmaud, MAX(drg.drg_chrgitem_name) billmaud_name,
          d.nhso_adp_code adp,
          SUM(oo.qty) qty, SUM(IFNULL(oo.qty,0)*IFNULL(oo.unitprice,0)) amount,
          MAX(EXISTS(SELECT 1 FROM opitemrece_noexport no WHERE no.seq=oo.{col} AND no.icode=oo.icode)) hosxp_noexport
        FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode LEFT JOIN income inc ON inc.income=oo.income
        LEFT JOIN drg_chrgitem drg ON drg.drg_chrgitem_id=inc.drg_chrgitem_id
        WHERE oo.{col}={nhso18.lit(key)}
        GROUP BY oo.icode ORDER BY inc.drg_chrgitem_id, oo.icode""")
        table = "iptdiag" if kind == "IPD" else "ovstdiag"
        dx = q(f"SELECT diagtype, icd10, icdname(icd10) name FROM {table} WHERE {col}={nhso18.lit(key)} ORDER BY diagtype, icd10")
        return {
            "uuc_auto": auto_uuc(key, kind), "uuc_set": local.nhso18_uuc([key]).get(key, ""),
            "items": [{"icode": text(i["icode"]), "name": text(i["name"]), "billmaud": text(i["billmaud"]),
                       "billmaud_name": text(i.get("billmaud_name")), "adp": text(i["adp"]),
                       "qty": number(i["qty"]), "amount": svc.money(i["amount"]),
                       "hosxp_excluded": bool(i["hosxp_noexport"]), "excluded": (key, text(i["icode"])) in excluded}
                      for i in items],
            "dx": [{"type": text(d["diagtype"]), "code": text(d["icd10"]), "name": text(d["name"])} for d in dx],
        }

    def auto_uuc(key: str, kind: str) -> str:
        """UUC this visit gets without a hand choice here: 1, or 2 when HOSxP marks it not claimed; IPD is always 1."""
        if kind == "IPD":
            return "1"
        config, groups = svc.load_setup(q, settings.hcode)
        found = nhso18.opd_contexts(q, [key], groups, config.zone)
        return found[0].uuc if found else ""

    @app.post("/api/nhso18/uuc")
    def set_uuc(request: Request, body: dict = Body(...)):
        key, uuc = text(body.get("key")), text(body.get("uuc"))
        if not key:
            raise HTTPException(400, "ไม่ได้ระบุรายการ")
        if uuc not in {"", "1", "2"}:
            raise HTTPException(400, "UUC ต้องเป็น 1 หรือ 2")
        local.set_nhso18_uuc(key, uuc or None, auth.user_label(request))
        log.info("nhso18 uuc key=%s uuc=%s", key, uuc or "auto")
        return {"uuc_set": uuc}

    @app.post("/api/nhso18/item")
    def item(body: dict = Body(...)):
        key, icode = text(body.get("key")), text(body.get("icode"))
        if not key or not icode:
            raise HTTPException(400, "ข้อมูลไม่ครบ")
        local.set_nhso18_item(key, icode, bool(body.get("excluded")))
        return {"excluded_count": len(local.nhso18_excluded([key]))}

    def build(body: dict) -> tuple[list[str], list[str], dict[str, list[dict]], int] | JSONResponse:
        """Re-check the picked rows with fresh data and build the rows of each file (shared by ZIP, FDH and e-Claim).

        UCS walk-in visits of the same HN and day are taken together (also the ones not picked) and merged into the
        first visit; the last value is how many visits were merged away."""
        picked = [p for p in body.get("rows") or [] if text(p.get("key"))]
        if not picked:
            raise HTTPException(400, "ยังไม่ได้เลือกรายการ")
        vns = list(dict.fromkeys(text(p["key"]) for p in picked if p.get("kind") != "IPD"))
        ans = list(dict.fromkeys(text(p["key"]) for p in picked if p.get("kind") == "IPD"))
        config, groups = svc.load_setup(q, settings.hcode)
        merge = nhso18.walkin_siblings(q, vns, groups.walkin)
        vns += [v for v in dict.fromkeys([*merge.values(), *merge]) if v not in vns]
        rows = svc.search(q, settings.rcm_db, None, None, [], bool(vns), bool(ans), vn_list=vns, an_list=ans)
        reviewed = review(rows)
        blocked = [{"key": r["key"], "reason": r["errors"][0]["message"]} for r in reviewed if r["errors"]]
        if blocked:
            return JSONResponse({"detail": "มีรายการที่ส่งไม่ได้", "blocked": blocked}, status_code=409)
        resend = [r["key"] for r in reviewed if r["status"]]
        if resend and not body.get("confirm_resend"):
            return JSONResponse({"detail": "ต้องยืนยันการส่งซ้ำ", "resend": resend, "needs_confirm": True}, status_code=409)
        odsmis = svc._csv_quoted((q("SELECT sys_value FROM sys_var WHERE sys_name REGEXP 'ODSMIS' LIMIT 1") or [{}])[0].get("sys_value"))
        visits = nhso18.opd_contexts(q, vns, groups, config.zone) + svc.ipd_contexts(q, ans, groups, odsmis)
        if not visits:
            raise HTTPException(400, "ไม่พบรายการที่เลือก")
        nhso18.set_uuc(visits, local.nhso18_uuc(vns + ans))
        excluded = local.nhso18_excluded(vns + ans)
        data = nhso18.Builder(q, config, settings.rcm_db, ECLAIM_DB, excluded).run(visits)
        nhso18.set_permitno(data, {r["key"]: r["claimcode"] for r in reviewed if r["approve_required"]})
        return vns, ans, nhso18.merge_walkin(data, merge), len(merge)

    @app.post("/api/nhso18/export")
    def export(request: Request, body: dict = Body(...)):
        built = build(body)
        if isinstance(built, JSONResponse):
            return built
        vns, ans, data, merged = built
        result = nhso18.build_zip(data, settings.hcode, settings.export_dir)
        export_id = local.record_nhso18_export(result.zip_path, vns + ans, auth.user_label(request))
        log.info("nhso18 export id=%s visits=%s", export_id, len(vns) + len(ans))
        return {"id": export_id, "file": result.zip_path.name, "count": len(vns) + len(ans), "merged": merged,
                "counts": result.counts}

    def save_copy(prefix: str, parts: dict[str, bytes]) -> Path:
        folder = settings.export_dir / f"{prefix}_{datetime.now():%Y%m%d-%H%M%S}"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for name, content in parts.items():
                (folder / name).write_bytes(content)
        except OSError as exc:
            raise HTTPException(500, f"บันทึกสำเนาแฟ้มที่ส่งไม่สำเร็จ: {exc.__class__.__name__}") from None
        return folder

    def line_counts(parts: dict[str, bytes]) -> dict[str, int]:
        return {n.split(".")[0].upper(): max(c.count(b"\r\n") - 1, 0) for n, c in parts.items()}

    # ------------------------------------------------------------ FDH
    def login_of(request: Request) -> str:
        """The login an FDH account belongs to: the session user id, e.g. hosxp:name or moph:id ("" when login is off)."""
        user = getattr(request.state, "user", None)
        return text(user["user_id"]) if user else ""

    def account(request: Request) -> tuple[str, str, str]:
        """(FDH user, saved password hash, source) for this login: its own account, else the one in .env."""
        own = local.fdh_account(login_of(request)) if login_of(request) else None
        if own:
            return own["fdh_user"], own["password_hash"], "own"
        if settings.fdh_user and settings.fdh_password:
            return settings.fdh_user, "", "env"
        return "", "", ""

    def client(request: Request, fdh_user: str | None = None, hashed: str | None = None) -> fdh.Client:
        if fdh_user is None:
            fdh_user, hashed, _ = account(request)
        password = settings.fdh_password if not hashed and fdh_user == settings.fdh_user else ""
        return fdh.Client(fdh_user, password, settings.hcode, settings.fdh_token_url,
                          settings.fdh_hash_key, settings.moph_user_agent, hashed=hashed or "")

    def zone_of(body: dict) -> str:
        zone = text(body.get("zone")) or "uat"
        if zone not in fdh.ZONES:
            raise HTTPException(400, "ปลายทาง FDH ไม่ถูกต้อง")
        return zone

    @app.get("/api/nhso18/fdh/status")
    def fdh_status(request: Request):
        fdh_user, _, source = account(request)
        return {"ready": bool(source), "zones": fdh.ZONES, "fdh_user": fdh_user, "source": source,
                "can_set": bool(login_of(request)), "env_user": settings.fdh_user if settings.fdh_password else ""}

    @app.post("/api/nhso18/fdh/account")
    def fdh_set_account(request: Request, body: dict = Body(...)):
        """Save this login's own FDH account after FDH accepts it (only the password hash is kept)."""
        login = login_of(request)
        if not login:
            raise HTTPException(400, "ต้องเข้าสู่ระบบก่อนจึงจะตั้งบัญชี FDH ของตัวเองได้")
        if body.get("clear"):
            local.set_fdh_account(login, None)
            log.info("fdh account cleared login=%s", login)
            return fdh_status(request)
        fdh_user, password = text(body.get("user")), str(body.get("password") or "")
        if not fdh_user or not password:
            raise HTTPException(400, "ต้องกรอกชื่อผู้ใช้และรหัสผ่าน FDH")
        hashed = fdh.password_hash(password, settings.fdh_hash_key)
        try:
            client(request, fdh_user, hashed).token()
        except fdh.FdhError as exc:
            raise HTTPException(400, str(exc)) from None
        local.set_fdh_account(login, fdh_user, hashed)
        log.info("fdh account set login=%s fdh_user=%s", login, fdh_user)
        return fdh_status(request)

    @app.post("/api/nhso18/fdh/test")
    def fdh_test(request: Request, body: dict = Body(...)):
        zone = zone_of(body)
        try:
            reply = client(request).test(zone)
        except fdh.FdhError as exc:
            raise HTTPException(502, str(exc)) from None
        if not reply.ok:
            raise HTTPException(502, f"FDH ตอบกลับ: {reply.message}")
        return {"message": f"เชื่อมต่อ FDH {zone.upper()} ได้ (Token OK) ด้วยบัญชี {account(request)[0]}"}

    @app.post("/api/nhso18/fdh/send")
    def fdh_send(request: Request, body: dict = Body(...)):
        zone, prescreen = zone_of(body), bool(body.get("prescreen"))
        fdh_user, hashed, source = account(request)
        if not source:
            raise HTTPException(400, "ยังไม่ได้ตั้งบัญชี FDH (ตั้งของตัวเองที่ปุ่ม \"บัญชี FDH\" หรือ FDH_USER / FDH_PASSWORD ใน .env)")
        if prescreen:
            body = {**body, "confirm_resend": True}  # PreScreen only checks; it never needs the resend question.
        built = build(body)
        if isinstance(built, JSONResponse):
            return built
        vns, ans, data, merged = built
        parts = fdh.files(fdh.fix_rows(data, q), with_phdb=bool(body.get("phdb", True)) and not prescreen)
        mode = "prescreen" if prescreen else "send"
        folder = save_copy(f"FDH_{zone}_{mode}", parts)
        user = auth.user_label(request)
        try:
            reply = client(request, fdh_user, hashed).send(zone, parts, prescreen)
        except fdh.FdhError as exc:
            local.record_fdh_send(vns + ans, zone, mode, False, None, str(exc), str(folder), user, fdh_user=fdh_user)
            raise HTTPException(502, str(exc)) from None
        send_id = local.record_fdh_send(vns + ans, zone, mode, reply.ok, reply.http_status, reply.message, str(folder), user,
                                        fdh_user=fdh_user)
        log.info("fdh %s zone=%s id=%s visits=%s ok=%s http=%s fdh_user=%s", mode, zone, send_id, len(vns) + len(ans),
                 reply.ok, reply.http_status, fdh_user)
        return {"id": send_id, "ok": reply.ok, "zone": zone, "mode": mode, "fdh_user": fdh_user, "count": len(vns) + len(ans),
                "merged": merged, "http_status": reply.http_status, "status": reply.status, "message": reply.message, "counts": line_counts(parts)}

    @app.get("/api/nhso18/fdh/history")
    def fdh_history():
        return local.fdh_history()

    # ------------------------------------------------------------ New e-Claim API
    def eclaim() -> eclaim_api.Client:
        return eclaim_api.Client(settings.nhso_user, settings.nhso_password, settings.moph_user_agent)

    @app.get("/api/nhso18/eclaim/status")
    def eclaim_status():
        return {"ready": bool(settings.nhso_user and settings.nhso_password), "maininscl": list(eclaim_api.MAININSCL)}

    @app.post("/api/nhso18/eclaim/test")
    def eclaim_test():
        try:
            eclaim().token()
        except eclaim_api.EclaimError as exc:
            raise HTTPException(502, str(exc)) from None
        return {"message": "เชื่อมต่อ New e-Claim ได้ (Token OK)"}

    @app.post("/api/nhso18/eclaim/send")
    def eclaim_send(request: Request, body: dict = Body(...)):
        if not settings.nhso_user or not settings.nhso_password:
            raise HTTPException(400, "ยังไม่ได้ตั้ง NHSO_USER / NHSO_PASSWORD ใน .env")
        maininscl, import_dup = text(body.get("maininscl")), bool(body.get("import_dup"))
        if maininscl and maininscl not in eclaim_api.MAININSCL:
            raise HTTPException(400, "สิทธิที่เลือกไม่ถูกต้อง")
        built = build(body)
        if isinstance(built, JSONResponse):
            return built
        vns, ans, data, merged = built
        parts = eclaim_api.files(data)
        folder = save_copy("ECLAIM", {f"{n}.txt": c for n, c in parts.items()})
        user, seqs = auth.user_label(request), vns + ans
        try:
            reply = eclaim().send(parts, maininscl, import_dup)
        except eclaim_api.EclaimError as exc:
            local.record_eclaim_send(seqs, maininscl, import_dup, False, None, str(exc), str(folder), user)
            raise HTTPException(502, str(exc)) from None
        send_id = local.record_eclaim_send(seqs, maininscl, import_dup, reply.ok, reply.http_status, reply.message, str(folder), user)
        log.info("eclaim send id=%s visits=%s ok=%s http=%s", send_id, len(seqs), reply.ok, reply.http_status)
        return {"id": send_id, "ok": reply.ok, "count": len(seqs), "merged": merged,
                "http_status": reply.http_status, "status": reply.status, "message": reply.message, "counts": line_counts(parts)}

    @app.get("/api/nhso18/eclaim/history")
    def eclaim_history():
        return local.eclaim_history()

    @app.get("/api/nhso18/exports")
    def exports():
        return [{**row, "file": Path(row.pop("zip_path")).name} for row in local.nhso18_history()]

    @app.get("/api/nhso18/exports/{export_id}/download")
    def download(export_id: int):
        path = local.nhso18_path(export_id)
        if path is None or not path.is_file():
            raise HTTPException(404, "ไม่พบไฟล์")
        return FileResponse(path, media_type="application/zip", filename=path.name)
