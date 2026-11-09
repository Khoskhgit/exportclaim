"""Search, review and export for the NHSO 18-file page (AddOPD/AddIPD/Data2Report of ส่งออก18แฟ้ม.txt)."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Callable, Iterable, Sequence

from . import nhso18
from .nhso18 import PttypeGroups, Visit, lit, lit_list, num
from .validate import text

Select = Callable[[str], list[dict]]


def _csv_quoted(value) -> tuple[str, ...]:
    return tuple(p.strip().strip('"').strip("'") for p in text(value).split(",") if p.strip().strip('"').strip("'"))


def load_setup(q: Select, hcode: str) -> tuple[nhso18.Config, PttypeGroups]:
    """Read the settings the old program took from sys_var / opdconfig."""
    sv = {r["sys_name"]: r["sys_value"] for r in q(
        "SELECT sys_name, sys_value FROM sys_var WHERE sys_name IN ('Referin','OpRefer','CupRefer','MoveRight','Walkin',"
        "'FreeRight','ODSMIS','home_income','non_drug_account_income','CHI_HospMain','UC-ONTOP-4','UC-ONTOP-6') "
        "OR sys_name REGEXP 'UCAE'")}
    lower = {k.lower(): v for k, v in sv.items()}
    zone = text((q(f"SELECT hospitalzone({lit(hcode)}) z") or [{}])[0].get("z"))
    has_us = bool(q("SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='us_items'"))
    us = tuple(r["icode"] for r in q("SELECT icode FROM us_items")) if has_us else ()
    homemed = _csv_quoted(lower.get("home_income"))
    ucae = tuple(v for k, val in sv.items() if re.search("UCAE", k) for v in _csv_quoted(val))
    yes = lambda key: text(lower.get(key)).upper() in {"Y", "1", "T", "TRUE"}
    config = nhso18.Config(
        hcode=hcode, hosp_main=text(lower.get("chi_hospmain")), zone=zone, income_homemed=homemed,
        income_drug_ned=_csv_quoted(lower.get("non_drug_account_income")), homemed=homemed[0] if homemed else "",
        us_items=us, free_right=_csv_quoted(lower.get("freeright")), ontop4=yes("uc-ontop-4"),
    )
    groups = PttypeGroups(
        referin=_csv_quoted(lower.get("referin")), oprefer=_csv_quoted(lower.get("oprefer")),
        cuprefer=_csv_quoted(lower.get("cuprefer")), moveright=_csv_quoted(lower.get("moveright")),
        walkin=_csv_quoted(lower.get("walkin")), emergency=ucae, ontop4=yes("uc-ontop-4"), ontop6=yes("uc-ontop-6"),
    )
    return config, groups


def walkin_pttypes(q: Select) -> tuple[str, ...]:
    """The walk-in rights (sys_var Walkin), as load_setup reads them."""
    row = (q("SELECT sys_value FROM sys_var WHERE sys_name='Walkin' LIMIT 1") or [{}])[0]
    return _csv_quoted(row.get("sys_value"))


def pttypes(q: Select) -> list[dict]:
    return q("SELECT pttype, name, hipdata_code FROM pttype WHERE isuse='Y' ORDER BY pttype")


# ------------------------------------------------------------------ search
def search(q: Select, rcm: str, start: date | None, end: date | None, rights: Sequence[str], opd: bool, ipd: bool,
           vn: str = "", an: str = "", hn: str = "", vn_list: Sequence[str] = (), an_list: Sequence[str] = ()) -> list[dict]:
    rcm = f"`{rcm}`"
    rows: list[dict] = []
    has = lambda t: bool(q(f"SELECT 1 FROM information_schema.tables WHERE table_schema='{rcm.strip('`')}' AND table_name='{t}'"))
    rep_ok = has("repdata")
    eclaim_ok = has("repeclaim")
    rep = lambda key, col: (f"(SELECT GROUP_CONCAT(DISTINCT rep) FROM {rcm}.repdata WHERE {key}={col} AND IFNULL(errorcode,'')='')"
                            if rep_ok else "NULL")
    paid = lambda key, col: (f"(SELECT GROUP_CONCAT(DISTINCT rep) FROM {rcm}.repeclaim WHERE {key}={col} AND IFNULL(errorcode,'')='')"
                             if eclaim_ok else "NULL")
    if opd and not an:
        if vn_list:
            where = f"o.vn IN ({lit_list(vn_list)})"
        elif vn:
            where = f"o.vn={lit(vn)}"
        elif hn:
            where = f"o.hn={lit(hn)}" + (f" AND o.vstdate BETWEEN {lit(start)} AND {lit(end)}" if start and end else "")
        else:
            where = f"o.vstdate BETWEEN {lit(start)} AND {lit(end)} AND IFNULL(o.an,'')='' AND o.pttype IN ({lit_list(rights)})"
        rows += [{**r, "kind": "OPD"} for r in q(f"""
        SELECT o.vn, NULL an, o.hn, pt.cid, CONCAT(IFNULL(pt.pname,''),IFNULL(pt.fname,''),' ',IFNULL(pt.lname,'')) ptname,
          o.vstdate date, o.vsttime time, o.pttype, ptt.name pttype_name, seekhipdatas(o.pttype,0) maininscl,
          pttypeeclaim(o.pttype) pttype_eclaim, diagnosis('opd',o.vn,1) pdx, v.income money_hosxp,
          (SELECT SUM(qty*unitprice) FROM opitemrece WHERE vn=o.vn) money_total,
          (SELECT vp.claim_code FROM visit_pttype vp WHERE vp.vn=o.vn AND vp.pttype=o.pttype LIMIT 1) claimcode,
          {rep('vn', 'o.vn')} rep_rep, {paid('vn', 'o.vn')} rep_invoice,
          re.fdh_claim_status_message fdh_status, re.fdh_act_amt fdh_amount,
          seekexport(o.ovstost,'ovstost',NULL) discharge, o.an has_an
        FROM ovst o
        LEFT JOIN patient pt ON pt.hn=o.hn LEFT JOIN vn_stat v ON v.vn=o.vn LEFT JOIN pttype ptt ON ptt.pttype=o.pttype
        LEFT JOIN fdh_claim_status re ON re.vn=o.vn
        WHERE {where}
        ORDER BY o.vstdate, o.vsttime, o.vn LIMIT 20000""")]
    if ipd and not vn:
        if an_list:
            where = f"i.an IN ({lit_list(an_list)})"
        elif an:
            where = f"i.an={lit(an)}"
        elif hn:
            where = f"i.hn={lit(hn)}" + (f" AND i.dchdate BETWEEN {lit(start)} AND {lit(end)}" if start and end else "")
        else:
            where = f"i.dchdate BETWEEN {lit(start)} AND {lit(end)} AND i.pttype IN ({lit_list(rights)})"
        rows += [{**r, "kind": "IPD"} for r in q(f"""
        SELECT NULL vn, i.an, i.hn, pt.cid, CONCAT(IFNULL(pt.pname,''),IFNULL(pt.fname,''),' ',IFNULL(pt.lname,'')) ptname,
          i.dchdate date, i.dchtime time, i.pttype, ptt.name pttype_name, seekhipdatas(i.pttype,0) maininscl,
          pttypeeclaim(i.pttype) pttype_eclaim, diagnosis('ipd',i.an,1) pdx, a.income money_hosxp,
          (SELECT SUM(qty*unitprice) FROM opitemrece WHERE an=i.an) money_total,
          (SELECT ip.claim_code FROM ipt_pttype ip WHERE ip.an=i.an AND ip.pttype=i.pttype LIMIT 1) claimcode,
          {rep('an', 'i.an')} rep_rep, {paid('an', 'i.an')} rep_invoice,
          re.fdh_claim_status_message fdh_status, re.fdh_act_amt fdh_amount, NULL discharge, NULL has_an
        FROM ipt i
        LEFT JOIN patient pt ON pt.hn=i.hn LEFT JOIN an_stat a ON a.an=i.an LEFT JOIN pttype ptt ON ptt.pttype=i.pttype
        LEFT JOIN fdh_claim_status re ON re.vn=i.an
        WHERE {where}
        ORDER BY i.dchdate, i.an LIMIT 20000""")]
    return rows


def key_of(row: dict) -> str:
    return text(row.get("an")) or text(row.get("vn"))


def checks(q: Select, rcm: str, rows: list[dict]) -> dict[str, dict]:
    """Errors and warnings of Data2Report, computed in a few batch queries."""
    rcm = f"`{rcm}`"
    vns = [r["vn"] for r in rows if r["kind"] == "OPD"]
    ans = [r["an"] for r in rows if r["kind"] == "IPD"]
    found: dict[str, dict] = {key_of(r): {"errors": [], "warns": []} for r in rows}

    def flag(sql_vn: str, sql_an: str) -> dict[str, list]:
        out: dict[str, list] = {}
        for key, ids, sql in (("vn", vns, sql_vn), ("an", ans, sql_an)):
            if not ids or not sql:
                continue
            for r in q(sql.replace("{ids}", lit_list(ids))):
                out.setdefault(r["k"], []).append(r.get("item"))
        return out

    has_catalog = bool(q(f"SELECT 1 FROM information_schema.tables WHERE table_schema='{rcm.strip('`')}' AND table_name='eclaim_drugcatalog'"))
    items_sql = lambda key, where: (f"SELECT oo.{key} k, CONCAT_WS(' ',oo.icode,d.name) item FROM opitemrece oo "
                                    f"JOIN drugitems d ON d.icode=oo.icode LEFT JOIN {rcm}.eclaim_drugcatalog dd ON dd.hospdrugcode=oo.icode "
                                    f"WHERE oo.{key} IN ({{ids}}) AND oo.sum_price<>0 AND {where}")
    drug_missing = flag(items_sql("vn", "IFNULL(dd.hospdrugcode,'')=''"), items_sql("an", "IFNULL(dd.hospdrugcode,'')=''")) if has_catalog else {}
    tmt_wrong = flag(items_sql("vn", "IFNULL(d.tpu_code_list,'')<>IFNULL(dd.tmtid,'')"),
                     items_sql("an", "IFNULL(d.tpu_code_list,'')<>IFNULL(dd.tmtid,'')")) if has_catalog else {}
    adp_sql = lambda key: (f"SELECT oo.{key} k, CONCAT_WS(' ',oo.icode,d.name) item FROM opitemrece oo "
                           f"LEFT JOIN income inc ON inc.income=oo.income LEFT JOIN s_drugitems d ON d.icode=oo.icode "
                           f"WHERE oo.{key} IN ({{ids}}) AND inc.drg_chrgitem_id IN (1,2,5,6,7,8,9,10,11,12,13,14,15,19) "
                           f"AND IFNULL(d.nhso_adp_code,'')=''")
    adp_missing = flag(adp_sql("vn"), adp_sql("an"))
    inc_sql = lambda key: (f"SELECT oo.{key} k, CONCAT_WS(' ',oo.icode,d.name) item FROM opitemrece oo "
                           f"LEFT JOIN income inc ON inc.income=oo.income LEFT JOIN s_drugitems d ON d.icode=oo.icode "
                           f"WHERE oo.{key} IN ({{ids}}) AND inc.income IS NULL")
    inc_missing = flag(inc_sql("vn"), inc_sql("an"))
    hn_sql = lambda key, table, col: (f"SELECT oo.{key} k, oo.hn item FROM opitemrece oo JOIN {table} x ON x.{col}=oo.{key} "
                                      f"WHERE oo.{key} IN ({{ids}}) AND oo.hn<>x.hn")
    other_hn = flag(hn_sql("vn", "ovst", "vn"), hn_sql("an", "ipt", "an"))
    screen = {r["k"]: r["item"] for r in q(
        "SELECT vn k, CASE WHEN bw>999 THEN 'บันทึกน้ำหนักผิดพลาด' WHEN temperature>999 THEN 'บันทึกอุณหภูมิผิดพลาด' "
        "WHEN bps>999 OR bpd>999 THEN 'บันทึก BP ผิดพลาด' WHEN pulse>999 THEN 'บันทึกชีพจรผิดพลาด' "
        f"WHEN rr>999 THEN 'บันทึกอัตราการหายใจผิดพลาด' ELSE '' END item FROM opdscreen WHERE vn IN ({lit_list(vns)})")} if vns else {}
    refer = {r["k"]: r for r in q(f"""
        SELECT o.vn k, (SELECT GROUP_CONCAT(DISTINCT d.nhso_adp_code) FROM opitemrece oo JOIN nondrugitems d ON d.icode=oo.icode
                        WHERE oo.vn=o.vn AND d.nhso_adp_code REGEXP '^S180') codes
        FROM ovst o WHERE o.vn IN ({lit_list(vns)})
          AND (EXISTS(SELECT 1 FROM referout r WHERE r.vn=o.vn) OR o.ovstost IN (SELECT ovstost FROM ovstost WHERE export_code=3))""")} if vns else {}

    for r in rows:
        k = key_of(r)
        f = found[k]
        e, w = f["errors"], f["warns"]
        add = lambda bucket, code, message, items=(): bucket.append({"code": code, "message": message, "items": [text(i) for i in items if i]})
        if text(r.get("maininscl")) == "OFC" and not text(r.get("claimcode")):
            add(e, "NO_CLAIMCODE", "ไม่พบ ClaimCode")
        if not text(r.get("pdx")):
            add(e, "PDX_EMPTY", "ไม่พบ PDx")
        if not r.get("date"):
            add(e, "DATE_EMPTY", "ไม่พบวันที่ D/C" if r["kind"] == "IPD" else "ไม่พบวันที่ตรวจ")
        if k in refer:
            codes = text(refer[k].get("codes"))
            if not codes:
                add(e, "REFER_NO_CHARGE", "ไม่มีค่าใช้จ่ายในการ Refer (S180x)")
            elif "S1802" in codes and "S1801" not in codes:
                add(e, "REFER_WRONG", "บันทึกการ Refer ผิดพลาด")
        if r["kind"] == "OPD":
            disch = text(r.get("discharge"))
            if text(r.get("has_an")) and not disch.startswith("2"):
                add(e, "ADMIT_STATUS", "พบ AN แต่ไม่ลงสถานะ Admit")
            elif disch.startswith("2") and not text(r.get("has_an")):
                add(e, "ADMIT_NO_AN", "ลงสถานะ Admit แต่ไม่พบ AN")
        if k in tmt_wrong:
            add(e, "TMT_WRONG", "TMT ไม่ตรงกับ Drug Catalog e-Claim", tmt_wrong[k])
        if k in adp_missing:
            add(e, "ADP_MISSING", "ยัง Map ADP ไม่ครบถ้วน", adp_missing[k])
        if k in inc_missing:
            add(e, "BILLMAUD_WRONG", "Billmaud ผิดพลาด (ไม่พบหมวดค่าใช้จ่าย)", inc_missing[k])
        if text(screen.get(k)):
            add(e, "SCREEN_WRONG", screen[k])
        if k in other_hn:
            add(e, "OTHER_HN", "พบ HN อื่นในรายการ")
        if round(num(r.get("money_hosxp")), 2) != round(num(r.get("money_total")), 2):
            add(w, "MONEY_MISMATCH", "ยอดค่าใช้จ่ายไม่ตรงกับรายการ")
        if k in drug_missing:
            add(w, "DRUG_NOT_IN_CATALOG", "ไม่พบรหัสยาใน Drug Catalog e-Claim", drug_missing[k])
    return found


def status(r: dict, local_sent: bool, sent_label: str = "") -> str:
    if text(r.get("rep_invoice")):
        return "ชดเชยแล้ว"
    if text(r.get("rep_rep")):
        return "ส่งแล้ว"
    if text(r.get("fdh_status")):
        return text(r["fdh_status"])[:40]
    return sent_label or ("ส่งออกแล้ว" if local_sent else "")


# ------------------------------------------------------------------ IPD context
def ipd_contexts(q: Select, ans: Sequence[str], groups: PttypeGroups, odsmis: Iterable[str] = ()) -> list[Visit]:
    if not ans:
        return []
    rows = q(f"""
    SELECT i.an, i.dchdate, seekhipdatas(i.pttype,0) maininscl, hospmain(i.an) hospmain, a.pdx, operation('ipd',i.an) oper,
      CASE WHEN ac.ac_ae='A' THEN 'Accident' WHEN ac.ac_ae='E' THEN 'Emergency'
           WHEN i.pttype IN ({lit_list(groups.emergency)}) THEN IF(a.pdx REGEXP 'S|T','Accident','Emergency')
           WHEN tt.is_ambulatory='Y' THEN 'Ambulatory Care'
           WHEN i.pttype IN ({lit_list(odsmis)}) THEN 'Ambulatory Care' END demand,
      CASE WHEN IFNULL((SELECT ip.project_code FROM ipt_pttype ip WHERE ip.an=i.an AND IFNULL(ip.project_code,'')<>''
                        AND ip.project_code NOT REGEXP '^PG' LIMIT 1),'')<>''
             THEN (SELECT ip.project_code FROM ipt_pttype ip WHERE ip.an=i.an AND IFNULL(ip.project_code,'')<>''
                   AND ip.project_code NOT REGEXP '^PG' LIMIT 1)
           WHEN i.pttype IN ({lit_list(groups.walkin)}) THEN 'WALKIN'
           WHEN a.pdx BETWEEN 'C00' AND 'D4899' OR diagnosis('ipd',i.an,0) REGEXP 'Z511|Z08' THEN 'CANCER' END projectcode,
      (SELECT d.nhso_adp_code FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode
        WHERE oo.an=i.an AND d.nhso_adp_type_id=5 LIMIT 1) project_item,
      EXISTS(SELECT 1 FROM iptdiag dx WHERE dx.an=i.an AND dx.icd10 REGEXP 'I6[0-9]|S06[1-9]|S140|S141|S24.0|S241|S340|S341|S343') imc_dx,
      EXISTS(SELECT 1 FROM opitemrece oo JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.an=i.an AND d.nhso_adp_code='IMC') imc_item,
      EXISTS(SELECT 1 FROM iptdiag dx WHERE dx.an=i.an AND dx.icd10 REGEXP 'E1[0-4]|I1[0-5]|L89|N39|J1[0-9]|K3[5-8]|U07') hw_dx,
      EXISTS(SELECT 1 FROM ward w WHERE w.ward=i.ward AND w.name REGEXP 'HW|HomeWard|Home Ward') hw_ward
    FROM ipt i
    LEFT JOIN ipt_type tt ON tt.ipt_type=i.ipt_type LEFT JOIN an_stat a ON a.an=i.an LEFT JOIN ipt_accident ac ON ac.an=i.an
    WHERE i.an IN ({lit_list(ans)})""")
    out = []
    for r in rows:
        project = text(r.get("project_item")) or text(r.get("projectcode"))
        if project == "CANCER" and not groups.ontop6:
            project = ""
        if project in {"ER-EXT"} and not groups.ontop4:
            project = ""
        if project == "WALKIN":
            project = ""
        demand = text(r.get("demand"))
        oper = text(r.get("oper"))
        imc = "Y" if "IMC" in oper or (r["imc_dx"] and r["imc_item"]) else ""
        hw = "Y" if "HOMEWARD" in oper or (r["hw_dx"] and r["hw_ward"]) else ""
        out.append(Visit(vn=r["an"], comedate=r["dchdate"], imc=imc, hw=hw, uuc="1", maininscl=text(r.get("maininscl")),
                         hospmain=text(r.get("hospmain")), ods="Y" if demand == "Ambulatory Care" else "", demand=demand,
                         projectcode=project))
    return out


def money(value) -> str:
    return f"{num(value).quantize(Decimal('0.01')):.2f}"
