"""NHSO 16/18-file export for New e-Claim upload, ported from ส่งออก18แฟ้ม.txt (GetQuery/ReadQuery/ExportDBF).

The old program built temporary tables and also changed HOSxP (ovstdiag.diagtype, column types).
A read-only MariaDB session refuses even temporary tables, so every statement here is a single SELECT;
grouping, updates and de-duplication are done in Python, and HOSxP is never changed.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Iterable, Sequence

from pymysql.converters import escape_item

from .validate import text

ENCODING = "cp874"
CRLF = "\r\n"
MY_OFC = r"OFC|LGO|BKK|BMT|SRT|PVT"
CHRONIC = r"DM:|HT:|CKD:|Asthma:|COPD:|IHD:|Cancer:|TB:|Chronic:"
X_CODES = {"XXXXXX", "51999", "52999", "55999", "72999"}

# Export4<file> tables from CreateTempFile: (column, kind, size). Kind: C char, D date, I int, N<places> decimal.
SCHEMA: dict[str, list[tuple[str, str, int]]] = {
    "INS": [("HN", "C", 15), ("INSCL", "C", 3), ("SUBTYPE", "C", 2), ("CID", "C", 16), ("HCODE", "C", 5), ("DATEEXP", "D", 8),
            ("HOSPMAIN", "C", 5), ("HOSPSUB", "C", 5), ("GOVCODE", "C", 6), ("GOVNAME", "C", 250), ("PERMITNO", "C", 30),
            ("DOCNO", "C", 30), ("OWNRPID", "C", 13), ("OWNNAME", "C", 250), ("AN", "C", 15), ("SEQ", "C", 25),
            ("SUBINSCL", "C", 2), ("RELINSCL", "C", 2), ("HTYPE", "C", 1)],
    "PAT": [("HCODE", "C", 5), ("HN", "C", 15), ("CHANGWAT", "C", 2), ("AMPHUR", "C", 2), ("DOB", "D", 8), ("SEX", "C", 1),
            ("MARRIAGE", "C", 1), ("OCCUPA", "C", 3), ("NATION", "C", 3), ("PERSON_ID", "C", 13), ("NAMEPAT", "C", 36),
            ("TITLE", "C", 30), ("FNAME", "C", 40), ("LNAME", "C", 40), ("IDTYPE", "I", 1)],
    "OPD": [("HN", "C", 15), ("CLINIC", "C", 5), ("DATEOPD", "D", 8), ("TIMEOPD", "C", 4), ("SEQ", "C", 25), ("UUC", "C", 1),
            ("DETAIL", "C", 200), ("BTEMP", "N1", 3), ("SBP", "N0", 3), ("DBP", "N0", 3), ("PR", "N0", 3), ("RR", "N0", 3),
            ("OPTYPE", "C", 2), ("TYPEIN", "C", 1), ("TYPEOUT", "C", 1)],
    "ORF": [("HN", "C", 15), ("DATEOPD", "D", 8), ("CLINIC", "C", 5), ("REFER", "C", 5), ("REFERTYPE", "C", 1), ("SEQ", "C", 25)],
    "ODX": [("HN", "C", 15), ("DATEDX", "D", 8), ("CLINIC", "C", 5), ("DIAG", "C", 9), ("DXTYPE", "C", 1), ("DRDX", "C", 20),
            ("PERSON_ID", "C", 13), ("SEQ", "C", 25)],
    "OOP": [("HN", "C", 15), ("DATEOPD", "D", 8), ("CLINIC", "C", 5), ("OPER", "C", 10), ("DROPID", "C", 20),
            ("PERSON_ID", "C", 13), ("SEQ", "C", 25)],
    "IPD": [("HN", "C", 15), ("AN", "C", 15), ("DATEADM", "D", 8), ("TIMEADM", "C", 4), ("DATEDSC", "D", 8), ("TIMEDSC", "C", 4),
            ("DISCHS", "C", 1), ("DISCHT", "C", 1), ("WARDDSC", "C", 4), ("DEPT", "C", 2), ("ADM_W", "N3", 7), ("UUC", "C", 1),
            ("SVCTYPE", "C", 1)],
    "IRF": [("AN", "C", 15), ("REFER", "C", 5), ("REFERTYPE", "C", 1)],
    "IDX": [("AN", "C", 15), ("DIAG", "C", 9), ("DXTYPE", "C", 1), ("DRDX", "C", 20)],
    "IOP": [("AN", "C", 15), ("OPER", "C", 10), ("OPTYPE", "C", 1), ("DROPID", "C", 20), ("DATEIN", "D", 8), ("TIMEIN", "C", 4),
            ("DATEOUT", "D", 8), ("TIMEOUT", "C", 4)],
    "CHT": [("HN", "C", 15), ("AN", "C", 15), ("DATE", "D", 8), ("TOTAL", "N2", 10), ("PAID", "N2", 10), ("PTTYPE", "C", 2),
            ("PERSON_ID", "C", 13), ("SEQ", "C", 25)],
    "CHA": [("HN", "C", 15), ("AN", "C", 15), ("DATE", "D", 8), ("CHRGITEM", "C", 2), ("AMOUNT", "N2", 10), ("PERSON_ID", "C", 13),
            ("SEQ", "C", 25)],
    "AER": [("HN", "C", 15), ("AN", "C", 15), ("DATEOPD", "D", 8), ("AUTHAE", "C", 12), ("AEDATE", "D", 8), ("AETIME", "C", 4),
            ("AETYPE", "C", 1), ("REFER_NO", "C", 20), ("REFMAINI", "C", 5), ("IREFTYPE", "C", 4), ("REFMAINO", "C", 5),
            ("OREFTYPE", "C", 4), ("UCAE", "C", 1), ("EMTYPE", "C", 1), ("SEQ", "C", 25), ("AESTATUS", "C", 1), ("DALERT", "D", 8),
            ("TALERT", "C", 4)],
    "ADP": [("HN", "C", 15), ("AN", "C", 15), ("DATEOPD", "D", 8), ("BILLMAUD", "C", 2), ("TYPE", "C", 2), ("CODE", "C", 20),
            ("QTY", "I", 4), ("RATE", "N2", 10), ("SEQ", "C", 25), ("CAGCODE", "C", 10), ("DOSE", "C", 10), ("CA_TYPE", "C", 1),
            ("SERIALNO", "C", 24), ("TOTCOPAY", "N2", 10), ("USE_STATUS", "C", 1), ("TOTAL", "N2", 10), ("QTYDAY", "I", 3),
            ("TMLTCODE", "C", 20), ("STATUS1", "C", 20), ("BI", "C", 3), ("GRAVIDA", "C", 2), ("GA_WEEK", "C", 2), ("DCIP", "C", 2),
            ("LMP", "D", 8), ("SP_ITEM", "C", 2)],
    "LVD": [("SEQLVD", "C", 3), ("AN", "C", 15), ("DATEOUT", "D", 8), ("TIMEOUT", "C", 4), ("DATEIN", "D", 8), ("TIMEIN", "C", 4),
            ("QTYDAY", "C", 3)],
    "DRU": [("HCODE", "C", 5), ("HN", "C", 15), ("AN", "C", 15), ("CLINIC", "C", 5), ("PERSON_ID", "C", 13), ("DATE_SERV", "D", 8),
            ("DID", "C", 30), ("DIDNAME", "C", 250), ("AMOUNT", "C", 12), ("DRUGPRIC", "C", 14), ("DRUGCOST", "C", 14),
            ("DIDSTD", "C", 24), ("UNIT", "C", 20), ("UNIT_PACK", "C", 20), ("SEQ", "C", 25), ("DRUGTYPE", "C", 2),
            ("DRUGREMARK", "C", 2), ("PA_NO", "C", 25), ("TOTCOPAY", "N2", 10), ("USE_STATUS", "C", 1), ("TOTAL", "N2", 10)],
    "LAB": [("HCODE", "C", 5), ("HN", "C", 15), ("PERSON_ID", "C", 13), ("DATESERV", "D", 8), ("SEQ", "C", 25), ("LABTEST", "C", 7),
            ("LABRESULT", "N2", 7)],
    "PHDB": [("HN", "C", 15), ("CODE", "C", 20), ("NAME", "C", 100), ("QTY", "I", 5), ("PRICE", "N2", 10), ("AN", "C", 15),
             ("SEQ", "C", 25), ("INSCL", "C", 3)],
}
KEYS = {
    "PHDB": ("SEQ", "CODE"), "ADP": ("SEQ", "TYPE", "CODE", "RATE", "SP_ITEM"), "AER": ("SEQ",), "CHA": ("SEQ", "CHRGITEM"),
    "CHT": ("SEQ",), "DRU": ("SEQ", "DID", "DATE_SERV", "USE_STATUS"), "IDX": ("AN", "DIAG"), "INS": ("SEQ",),
    "IOP": ("AN", "OPER"), "IPD": ("AN",), "IRF": ("AN", "REFER", "REFERTYPE"), "LAB": ("SEQ", "LABTEST"), "LVD": ("SEQLVD",),
    "ODX": ("SEQ", "DIAG"), "OOP": ("SEQ", "OPER"), "OPD": ("SEQ",), "ORF": ("SEQ", "REFER", "REFERTYPE"), "PAT": ("HN",),
}
HEADER_NAMES = {("ADP", "DCIP"): "DCIP/E_Screen"}
FILES = ["INS", "PAT", "OPD", "ORF", "ODX", "OOP", "IPD", "IRF", "IDX", "IOP", "CHT", "CHA", "AER", "ADP", "LVD", "DRU", "LAB", "PHDB"]


class Nhso18Error(RuntimeError):
    """The export cannot be completed."""


@dataclass
class Visit:
    """One row of the old tmpExport: an OPD VN or an IPD AN (``vn`` holds the AN for IPD)."""
    vn: str
    comedate: date | None = None
    imc: str = ""
    hw: str = ""
    uuc: str = "1"
    maininscl: str = ""
    hospmain: str = ""
    ods: str = ""
    demand: str = ""
    projectcode: str = ""
    ancid: int | None = None
    pregno: int | None = None
    lmp: date | None = None
    ga: int | None = None


@dataclass
class Config:
    hcode: str
    hosp_main: str = ""
    zone: str = ""
    nicd10: int = 3
    use_tm: bool = True
    income_homemed: tuple[str, ...] = ()
    income_drug_ned: tuple[str, ...] = ()
    homemed: str = ""
    us_items: tuple[str, ...] = ()
    free_right: tuple[str, ...] = ()
    ontop4: bool = False


def lit(value) -> str:
    # Queries go through pymysql with an empty parameter list, so a literal % must be doubled.
    return escape_item(value, "tis620").replace("%", "%%")


def lit_list(values: Iterable) -> str:
    values = [v for v in values if v not in (None, "")]
    return ",".join(lit(v) for v in values) if values else "'\x01'"


def num(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def round2(value) -> Decimal:
    return num(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value.strip())
        if m:
            year = int(m.group(1))
            year = year - 543 if year > 2400 else year
            try:
                return date(year, int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
    return None


def patient_title(row: dict) -> str:
    """Title rules from ReadQuery."""
    title = text(row.get("pname"))
    for old, new in (("นางสาว", "น.ส."), ("นส.", "น.ส."), ("เด็กชาย", "ด.ช."), ("ดช.", "ด.ช."), ("เด็กหญิง", "ด.ญ."), ("ดญ.", "ด.ญ.")):
        title = title.replace(old, new)
    if title in {"นาย", "นาง", "น.ส.", "ด.ช.", "ด.ญ."}:
        return title
    born = as_date(row.get("birthday"))
    adult = False
    if born:
        today = date.today()
        adult = (today.year - born.year - ((today.month, today.day) < (born.month, born.day))) >= 15
    sex = text(row.get("sex"))
    if sex == "1":
        return "นาย" if adult else "ด.ช."
    if sex == "2":
        return ("น.ส." if text(row.get("marrystatus")) == "1" else "นาง") if adult else "ด.ญ."
    return "นาย"


# ---------------------------------------------------------------- builder
class Builder:
    """Build the rows of each file. ``select(sql)`` runs one read-only query and returns dict rows."""

    def __init__(self, select: Callable[[str], list[dict]], config: Config, rcm: str, eclaim: str,
                 excluded: Iterable[tuple[str, str]] = ()):
        self.q = select
        self.c = config
        # Items the user left out in this program (VN or AN, icode); HOSxP's opitemrece_noexport still applies.
        self.excluded = sorted({f"{seq}|{icode}" for seq, icode in excluded})
        self.rcm = f"`{rcm}`"
        self.eclaim = f"`{eclaim}`"

    def run(self, visits: Sequence[Visit]) -> dict[str, list[dict]]:
        self.visits = list(visits)
        self.by_vn = {v.vn: v for v in self.visits}
        self.order = {v.vn: i for i, v in enumerate(self.visits)}
        rows = {}
        for name in FILES:
            raw = getattr(self, f"f_{name.lower()}")()
            rows[name] = finish(name, [self.common(name, r) for r in raw])
        return rows

    @property
    def ex(self) -> str:
        """Derived table standing in for tmpExport."""
        parts = []
        for v in self.visits:
            parts.append(
                "SELECT " + ",".join([
                    f"{lit(v.vn)} vn", f"{lit(v.comedate)} comedate", f"{lit(v.imc)} imc", f"{lit(v.hw)} hw", f"{lit(v.uuc)} uuc",
                    f"{lit(v.maininscl)} maininscl", f"{lit(v.hospmain)} hospmain", f"{lit(v.ods)} ods",
                    f"{lit(v.demand or None)} demand", f"{lit(v.projectcode or None)} projectcode", f"{lit(v.ancid)} ancid",
                    f"{lit(v.pregno)} pregno", f"{lit(v.lmp)} lmp", f"{lit(v.ga)} ga",
                ]))
        return "(" + " UNION ALL ".join(parts) + ")" if parts else "(SELECT NULL vn LIMIT 0)"

    def homemed(self) -> str:
        return lit_list(self.c.income_homemed) if self.c.income_homemed else "'XXX'"

    def noexport(self, alias: str, seq: str, with_homemed: bool) -> str:
        kind = (f"IF({alias}.item_type='H' OR {alias}.income IN ({self.homemed()}),'H','')" if with_homemed
                else f"IF({alias}.item_type='H','H','')")
        local = (f" AND CONCAT({alias}.{seq},'|',{alias}.icode) NOT IN ({lit_list(self.excluded)})" if self.excluded else "")
        return (f"NOT EXISTS(SELECT 1 FROM opitemrece_noexport no WHERE no.seq={alias}.{seq} AND no.icode={alias}.icode "
                f"AND no.type={kind}){local}")

    def common(self, name: str, r: dict) -> dict:
        """Per-row rules from ReadQuery."""
        r = {k.lower(): v for k, v in r.items()}
        r["hcode"] = self.c.hcode
        r["person_id"] = text(r.get("person_id")) or text(r.get("cid"))
        if "maininscl" in r and name != "PHDB":
            r["inscl"] = r.get("maininscl")
        r["subinscl"] = ""
        inscl = text(r.get("maininscl"))
        if inscl and inscl not in {"UCS", "SSS", "SSI", "STP", "FWF"}:
            r["hospmain"] = r["hospsub"] = ""
            r["datein"] = r["dateexp"] = None
        return {col: r.get(col.lower()) for col, _, _ in SCHEMA[name]}

    # ------------------------------------------------------------ INS
    def f_ins(self) -> list[dict]:
        free = lit_list(self.c.free_right)
        htype = "'1'" if self.c.hosp_main and re.search(self.c.hosp_main, self.c.hcode) else "'2'"
        return self.q(f"""
        SELECT o.vn seq, IF(ex.maininscl REGEXP 'NRD|NRH|FWF' AND IFNULL(bb.bio_id,'')<>'', bb.bio_id, pt.cid) cid,
          o.hn, '' an, seekhipdata(ptt.hipdata_code,1) maininscl, IF(ptt.pcode LIKE 'U%%','UC',ptt.pcode) subtype,
          seekpttypes('opd',o.vn,'dateexp') dateexp,
          IF(o.pttype IN ({free}),'NHSO',seekpttypes('opd',o.vn,'hospmain')) hospmain,
          IF(o.pttype IN ({free}),'NHSO',seekpttypes('opd',o.vn,'hospsub')) hospsub,
          (SELECT vp.claim_code FROM visit_pttype vp WHERE vp.vn=o.vn AND vp.pttype=o.pttype LIMIT 1) permitno,
          IF(ex.maininscl='SSS',{htype},'') htype
        FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn
        LEFT JOIN patient pt ON pt.hn=o.hn LEFT JOIN patient_bio_id bb ON bb.hn=pt.hn
        LEFT JOIN pttype ptt ON ptt.pttype=o.pttype
        UNION ALL
        SELECT i.an seq, IF(ex.maininscl REGEXP 'NRD|NRH|FWF' AND IFNULL(bb.bio_id,'')<>'', bb.bio_id, pt.cid) cid,
          i.hn, i.an, seekhipdata(ptt.hipdata_code,1) maininscl, IF(ptt.pcode LIKE 'U%%','UC',ptt.pcode) subtype,
          seekpttypes('ipd',i.an,'dateexp') dateexp,
          IF(i.pttype IN ({free}),'NHSO',seekpttypes('ipd',i.an,'hospmain')) hospmain,
          IF(i.pttype IN ({free}),'NHSO',seekpttypes('ipd',i.an,'hospsub')) hospsub,
          (SELECT ip.claim_code FROM ipt_pttype ip WHERE ip.an=i.an AND ip.pttype=i.pttype LIMIT 1) permitno,
          IF(ex.maininscl='SSS',{htype},'') htype
        FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn
        LEFT JOIN patient pt ON pt.hn=i.hn LEFT JOIN patient_bio_id bb ON bb.hn=pt.hn
        LEFT JOIN pttype ptt ON ptt.pttype=i.pttype""")

    # ------------------------------------------------------------ PAT
    def f_pat(self) -> list[dict]:
        rows = self.q(f"""
        SELECT pt.occupation occupa, nn.nhso_code nation, pt.hn, pt.cid, pt.pname, pt.fname, pt.lname,
          pt.birthday, pt.sex, pt.marrystatus, pt.chwpart, pt.amppart, pt.nationality
        FROM patient pt
        LEFT JOIN occupation occ ON occ.occupation=pt.occupation
        LEFT JOIN nationality nn ON nn.nationality=pt.nationality
        WHERE pt.hn IN (SELECT o.hn FROM ovst o JOIN {self.ex} ex ON ex.vn=o.vn)
           OR pt.hn IN (SELECT i.hn FROM ipt i JOIN {self.ex} ex ON ex.vn=i.an)""")
        # ReadQuery replaced OCCUPA with patient.occupation; the old file was in HN order.
        rows.sort(key=lambda r: text(r["hn"]))
        out = []
        for r in rows:
            title = patient_title(r)
            fname, lname = text(r.get("fname")), text(r.get("lname"))
            out.append({**r, "title": title, "fname": fname, "lname": lname, "namepat": f"{fname} {lname},{title}",
                        "changwat": text(r.get("chwpart")).zfill(2), "amphur": text(r.get("amppart")).zfill(2),
                        "dob": r.get("birthday"), "marriage": r.get("marrystatus"),
                        "nation": text(r.get("nation")) or text(r.get("nationality")).zfill(3), "idtype": 1})
        return out

    # ------------------------------------------------------------ OPD
    def f_opd(self) -> list[dict]:
        return self.q(f"""
        SELECT o.vn seq, o.hn, sp.provis_code clinic, o.vstdate dateopd, DATE_FORMAT(o.vsttime,'%%H%%i') timeopd,
          IF(ex.uuc='2','2','1') uuc, asstring(s.cc) detail,
          NULLIF(s.temperature,0) btemp, NULLIF(s.bps,0) sbp, NULLIF(s.bpd,0) dbp, NULLIF(s.pulse,0) pr, NULLIF(s.rr,0) rr,
          ' ' optype, seekexport(o.ovstist,'ovstist',NULL) typein, seekexport(o.ovstost,'ovstost',NULL) typeout
        FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn
        LEFT JOIN opdscreen s ON s.vn=o.vn LEFT JOIN spclty sp ON sp.spclty=o.spclty""")

    def f_orf(self) -> list[dict]:
        return self.q(f"""
        SELECT o.vn seq, o.hn, IFNULL(r.refer_date,o.vstdate) dateopd, sp.provis_code clinic,
          IFNULL(r.refer_hospcode,o.rfrilct) refer, '1' refertype
        FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn
        LEFT JOIN ovstist ist ON ist.ovstist=o.ovstist LEFT JOIN spclty sp ON sp.spclty=o.spclty
        LEFT JOIN referin r ON r.vn=o.vn
        WHERE (ist.export_code=3 OR r.vn IS NOT NULL)
        UNION ALL
        SELECT o.vn seq, o.hn, IFNULL(r.refer_date,o.vstdate) dateopd, sp.provis_code clinic,
          IFNULL(r.refer_hospcode,o.rfrolct) refer, '2' refertype
        FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn
        LEFT JOIN ovstost ost ON ost.ovstost=o.ovstost LEFT JOIN spclty sp ON sp.spclty=o.spclty
        LEFT JOIN referout r ON r.vn=o.vn
        WHERE (ost.export_code=3 OR r.vn IS NOT NULL)""")

    def diag_sql(self, column: str) -> str:
        if self.c.nicd10 == 3:
            return f"moreicd({column},'NHSO')"
        if self.c.nicd10 == 4:
            return f"moreicd({column},'SSS')"
        return column

    def f_odx(self) -> list[dict]:
        # The old program first ran UPDATE ovstdiag SET diagtype='5' for V-Y codes; here the type is set in the output.
        return self.q(f"""
        SELECT o.vn seq, o.hn, seekcid(o.hn,NULL) cid, o.vstdate datedx, sp.provis_code clinic,
          {self.diag_sql('dx.icd10')} diag, IF(dx.icd10 REGEXP '^[V-Y]','5',dx.diagtype) dxtype, doctorlicense(dx.doctor) drdx
        FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn
        LEFT JOIN spclty sp ON sp.spclty=o.spclty
        JOIN ovstdiag dx ON dx.vn=o.vn
        WHERE dx.icd10 NOT REGEXP '^[0-9]'
        ORDER BY o.vn, IF(dx.icd10 REGEXP '^[V-Y]','5',dx.diagtype), dx.ovst_diag_id""")

    def f_oop(self) -> list[dict]:
        base = f"SELECT o.vn seq, o.hn, seekcid(o.hn,NULL) cid, o.vstdate dateopd, sp.provis_code clinic"
        frm = f"FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn LEFT JOIN spclty sp ON sp.spclty=o.spclty"
        parts = [
            f"{base}, op.icd10 oper, doctorlicense(op.doctor) dropid, 1 part, op.ovst_diag_id ord {frm} JOIN ovstdiag op ON op.vn=o.vn WHERE op.icd10 REGEXP '^[0-9]'",
            f"{base}, op.icd9cm oper, doctorlicense(dr.doctor) dropid, 2, 0 {frm} JOIN er_regist_oper dr ON dr.vn=o.vn JOIN er_oper_code op ON op.er_oper_code=dr.er_oper_code WHERE IFNULL(op.icd9cm,'')<>''",
            f"{base}, op.icd9cm oper, doctorlicense(dr.doctor) dropid, 3, 0 {frm} JOIN doctor_operation dr ON dr.vn=o.vn JOIN er_oper_code op ON op.er_oper_code=dr.er_oper_code WHERE IFNULL(op.icd9cm,'')<>''",
            f"{base}, tm.icd9cm oper, doctorlicense(dt.doctor) dropid, 4, 0 {frm} JOIN dtmain dt ON dt.vn=o.vn JOIN dttm tm ON tm.code=dt.tmcode WHERE IFNULL(tm.icd9cm,'')<>''",
            f"{base}, op.icd10tm oper, doctorlicense(dr.doctor) dropid, 5, 0 {frm} JOIN er_regist_oper dr ON dr.vn=o.vn JOIN er_oper_code op ON op.er_oper_code=dr.er_oper_code WHERE IFNULL(op.icd10tm,'')<>''",
            f"{base}, op.icd10tm oper, doctorlicense(dr.doctor) dropid, 6, 0 {frm} JOIN doctor_operation dr ON dr.vn=o.vn JOIN er_oper_code op ON op.er_oper_code=dr.er_oper_code WHERE IFNULL(op.icd10tm,'')<>''",
            f"{base}, tm.icd10tm_operation_code oper, doctorlicense(dt.doctor) dropid, 7, 0 {frm} JOIN dtmain dt ON dt.vn=o.vn JOIN dttm tm ON tm.code=dt.tmcode WHERE IFNULL(tm.icd10tm_operation_code,'')<>''",
            f"{base}, tt.icd10tm oper, doctorlicense(provider(s.health_med_doctor_id,'health')) dropid, 8, 0 {frm} JOIN health_med_service s ON s.vn=o.vn "
            "LEFT JOIN health_med_service_operation ops ON ops.health_med_service_id=s.health_med_service_id "
            "LEFT JOIN health_med_operation_item tt ON tt.health_med_operation_item_id=ops.health_med_operation_item_id WHERE tt.icd10tm REGEXP '^[0-9]'",
        ]
        rows = []
        for number, part in enumerate(parts, 1):
            try:
                rows += [{**r, "part": number} for r in self.q(part)]
            except Exception as exc:  # a table may not exist in every HOSxP version
                if "doesn't exist" not in str(exc):
                    raise
        if not self.c.use_tm:
            rows = [r for r in rows if len(text(r.get("oper"))) <= 5]

        def oper_num(r):
            m = re.match(r"\s*([+-]?\d+)", text(r.get("oper")))
            return int(m.group(1)) if m else 0
        # REPLACE keeps the last row per (seq, oper). The old order among equal codes was arbitrary;
        # letting the ER/operation record win matched the old file most often.
        return sorted(rows, key=lambda r: (text(r["seq"]), oper_num(r)))

    # ------------------------------------------------------------ IPD
    def f_ipd(self) -> list[dict]:
        return self.q(f"""
        SELECT i.an, i.hn, i.regdate dateadm, formattime(i.regtime) timeadm, i.dchdate datedsc, formattime(i.dchtime) timedsc,
          s.nhso_dchstts dischs, t.nhso_dchtype discht, i.ward warddsc, IFNULL(st.nhso_code,'01') dept, i.bw/1000 adm_w,
          IF(ex.uuc='2','2','1') uuc,
          CASE WHEN ex.ods='Y' THEN 'A' WHEN ex.demand='Ambulatory Care' THEN 'A' ELSE 'I' END svctype
        FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn
        LEFT JOIN dchstts s ON s.dchstts=i.dchstts LEFT JOIN dchtype t ON t.dchtype=i.dchtype
        LEFT JOIN spclty st ON st.spclty=i.spclty""")

    def f_irf(self) -> list[dict]:
        rows = self.q(f"""
        SELECT i.an, IFNULL(r.refer_hospcode,i.rfrolct) refer, '2' refertype
        FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn
        LEFT JOIN dchtype t ON t.dchtype=i.dchtype LEFT JOIN referout r ON r.vn=i.an
        WHERE t.nhso_dchtype=4 OR r.vn IS NOT NULL
        UNION ALL
        SELECT i.an, IFNULL(r.refer_hospcode,i.rfrilct) refer, '1' refertype
        FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn
        LEFT JOIN ovstist ist ON ist.ovstist=i.ivstist LEFT JOIN referin r ON r.vn=i.vn
        WHERE ist.export_code=3 OR r.vn IS NOT NULL""")
        return rows

    def f_idx(self) -> list[dict]:
        return self.q(f"""
        SELECT i.an, {self.diag_sql('dx.icd10')} diag, dx.diagtype dxtype, doctorlicense(dx.doctor) drdx
        FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn JOIN iptdiag dx ON dx.an=i.an
        ORDER BY i.an, dx.diagtype, dx.ipt_diag_id""")

    def f_iop(self) -> list[dict]:
        rows = self.q(f"""
        SELECT i.an, CONCAT_WS('+',op.icd9,NULLIF(op.ext_code,'')) oper, IFNULL(op.oper_type,1) optype,
          IFNULL(op.opdate,i.regdate) datein, formattime(IFNULL(op.optime,i.regtime)) timein,
          CASE WHEN ex.hw='Y' THEN IFNULL(op.opdate,i.regdate) ELSE IF(op.enddate<op.opdate,op.opdate,op.enddate) END dateout,
          CASE WHEN ex.hw='Y' THEN formattime(IFNULL(op.optime,i.regtime)) ELSE formattime(op.endtime) END timeout,
          doctorlicense(op.doctor) dropid
        FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn JOIN iptoprt op ON op.an=i.an
        ORDER BY i.an, op.oper_type, op.iptoprt_id""")
        # CREATE ... IGNORE: the first row per (an, oper) wins.
        seen, out = set(), []
        for r in rows:
            key = (r["an"], text(r["oper"]))
            if key not in seen:
                seen.add(key)
                out.append(r)
        extra = self.q(f"""
        SELECT i.an, 'HOMEWARD' oper, '2' optype, i.regdate datein, formattime(i.regtime) timein, i.regdate dateout,
          formattime(i.regtime) timeout, doctorlicense(i.admdoctor) dropid
        FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn
        WHERE ex.hw='Y' AND NOT EXISTS(SELECT 1 FROM iptoprt op WHERE op.an=i.an AND op.icd9='HOMEWARD')
        UNION ALL
        SELECT i.an, 'IMC' oper, '2' optype, i.regdate datein, formattime(i.regtime) timein, i.dchdate dateout,
          formattime(i.dchtime) timeout, doctorlicense(i.admdoctor) dropid
        FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn
        WHERE ex.imc='Y' AND NOT EXISTS(SELECT 1 FROM iptoprt op WHERE op.an=i.an AND op.icd9='IMC')""")
        return replace_rows(out + extra, ("an", "oper"))

    # ------------------------------------------------------------ money
    def f_cht(self) -> list[dict]:
        rows = self.q(f"""
        SELECT o.vn seq, pt.cid, o.hn, NULL an, o.vstdate date, o.pttype,
          SUM(oo.sum_price) total, SUM(IF(oo.paidst IN ('01','03'),oo.sum_price,0)) paid
        FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn
        LEFT JOIN patient pt ON pt.hn=o.hn
        LEFT JOIN opitemrece oo ON oo.vn=o.vn AND {self.noexport('oo', 'vn', True)}
        GROUP BY o.vn
        UNION ALL
        SELECT i.an seq, pt.cid, i.hn, i.an, i.dchdate date, i.pttype,
          SUM(oo.sum_price) total, SUM(IF(oo.paidst IN ('01','03'),oo.sum_price,0)) paid
        FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn
        LEFT JOIN patient pt ON pt.hn=i.hn
        LEFT JOIN opitemrece oo ON oo.an=i.an AND {self.noexport('oo', 'an', True)}
        GROUP BY i.an""")
        return rows

    def f_cha(self) -> list[dict]:
        home = f"IF(oo.item_type='H',{lit(self.c.homemed) if self.c.homemed else 'oo.income'},oo.income)"
        rows = self.q(f"""
        SELECT seq, cid, hn, an, date, chrgitem, SUM(sum_price) amount FROM (
          SELECT o.vn seq, pt.cid, o.hn, NULL an, o.vstdate date,
            IF(oo.paidst IN ('03'),chrgitem_code2,chrgitem_code1) chrgitem, oo.sum_price
          FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn LEFT JOIN patient pt ON pt.hn=o.hn
          JOIN opitemrece oo ON oo.vn=o.vn
          LEFT JOIN income inc ON inc.income={home}
          LEFT JOIN drg_chrgitem drg ON drg.drg_chrgitem_id=inc.drg_chrgitem_id
          WHERE {self.noexport('oo', 'vn', True)}
          UNION ALL
          SELECT i.an seq, pt.cid, i.hn, i.an, i.dchdate date,
            IF(oo.paidst IN ('03'),chrgitem_code2,chrgitem_code1) chrgitem, oo.sum_price
          FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn LEFT JOIN patient pt ON pt.hn=i.hn
          JOIN opitemrece oo ON oo.an=i.an
          LEFT JOIN income inc ON inc.income={home}
          LEFT JOIN drg_chrgitem drg ON drg.drg_chrgitem_id=inc.drg_chrgitem_id
          WHERE {self.noexport('oo', 'an', True)}
        ) t GROUP BY seq, chrgitem HAVING amount<>0 ORDER BY seq, chrgitem""")
        return rows

    # ------------------------------------------------------------ AER
    def f_aer(self) -> list[dict]:
        zone = self.c.zone
        out_refer = "R" if zone == "13" or zone >= "01" else "O"
        in_refer = "P" if zone == "13" or zone >= "01" else "I"
        ucae = (f"CASE WHEN ex.demand='Normal' THEN 'N' WHEN ex.demand='Accident' THEN 'A' WHEN ex.demand='Emergency' THEN 'E' "
                f"WHEN ex.demand='OP Refer ข้ามจังหวัด' THEN '{out_refer}' WHEN ex.demand='OP Refer ในจังหวัด' THEN '{in_refer}' "
                "WHEN ex.demand='ย้ายหน่วยเกิดสิทธิทันที' THEN 'C' WHEN ex.demand='บริการเชิงรุก' THEN 'Z' ELSE '' END")
        return self.q(f"""
        SELECT o.vn seq, NULL an, o.hn, o.vstdate dateopd, NULL authae, NULL aedate, NULL aetime, NULL aetype, {ucae} ucae, '' emtype,
          IF(ri.vn IS NOT NULL,ri.refer_hospcode,'') refmaini, IF(ri.vn IS NOT NULL,'1100','') ireftype,
          IF(ro.vn IS NOT NULL,ro.refer_hospcode,'') refmaino, IF(ro.vn IS NOT NULL,'1100','') oreftype,
          CONCAT_WS(',',CONCAT('In:',ri.docno),CONCAT('Out:',ro.refer_number)) refer_no
        FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn
        LEFT JOIN referin ri ON ri.vn=o.vn LEFT JOIN referout ro ON ro.vn=o.vn
        WHERE IFNULL(ex.demand,'')<>'' OR ri.vn IS NOT NULL OR ro.vn IS NOT NULL
        UNION ALL
        SELECT i.an seq, i.an, i.hn, i.regdate dateopd, ac.claim_code authae, ac.ac_date aedate, formattime(ac.ac_time) aetime,
          ac.ac_type aetype, {ucae} ucae, ac.ac_emtype emtype,
          CASE WHEN ri.vn IS NOT NULL THEN ri.refer_hospcode WHEN ist.export_code=3 THEN i.rfrilct END refmaini,
          CASE WHEN ri.vn IS NOT NULL THEN '1100' WHEN ist.export_code=3 THEN i.iref_type END ireftype,
          CASE WHEN ro.vn IS NOT NULL THEN ro.refer_hospcode WHEN dt.nhso_dchtype=4 THEN i.rfrolct END refmaino,
          IF(ro.vn IS NOT NULL OR dt.nhso_dchtype=4,'1100','') oreftype,
          CONCAT_WS(',',CONCAT('In:',ri.docno),CONCAT('Out:',ro.refer_number)) refer_no
        FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn
        LEFT JOIN ipt_accident ac ON ac.an=i.an LEFT JOIN ovstist ist ON ist.ovstist=i.ivstist
        LEFT JOIN dchtype dt ON dt.dchtype=i.dchtype LEFT JOIN referin ri ON ri.vn=i.vn LEFT JOIN referout ro ON ro.vn=i.an
        WHERE IFNULL(ex.demand,'')<>'' OR ist.export_code=3 OR dt.nhso_dchtype=4 OR ri.vn IS NOT NULL OR ro.vn IS NOT NULL""")

    # ------------------------------------------------------------ ADP
    def code_lists(self) -> tuple[set[str], set[str]]:
        nurse = {r["code"] for r in self.q(f"SELECT DISTINCT code FROM {self.eclaim}.l_sev12 WHERE maininscl<>'UCS'")}
        supply = {r["code"] for r in self.q(f"SELECT DISTINCT code FROM {self.eclaim}.l_medical_supplies WHERE maininscl<>'UCS'")}
        return nurse, supply

    def f_adp(self) -> list[dict]:
        nurse, supply = self.code_lists()
        item_cols = """
          oo.rxdate dateopd, ex.maininscl, ex.projectcode, ex.demand, ex.pregno gravida, ex.ga ga_week, ex.lmp lmp, oo.paidst,
          inc.drg_chrgitem_id billmaud, IF(oo.paidst IN ('03'),chrgitem_code2,chrgitem_code1) chrgitem,
          oo.icode, CONCAT_WS(' ',d.name,d.strength) drugname, d.nhso_adp_type_id type, d.nhso_adp_code adp_code,
          fwf.fwf_item_code, adp.nhso_adp_code adp_master, adp.ucep, d.max_qty, d.stdprice,
          oo.qty, oo.unitprice, oo.sum_price, ll.tmlt_code tmltcode"""
        joins = f"""
          JOIN s_drugitems d ON d.icode=oo.icode
          LEFT JOIN fwf_item fwf ON fwf.fwf_item_id=d.fwf_item_id
          LEFT JOIN income inc ON inc.income=oo.income
          LEFT JOIN drg_chrgitem drg ON drg.drg_chrgitem_id=inc.drg_chrgitem_id
          LEFT JOIN nhso_adp_code adp ON adp.nhso_adp_code=d.nhso_adp_code AND zero(adp.drg_chrgitem_id)=zero(d.drg_chrgitem_id)
          LEFT JOIN {self.rcm}.labitem ll ON ll.icode=oo.icode AND (chi=1 OR eclaim=1)"""
        opd = self.q(f"""
        SELECT oo.vn seq, oo.an, oo.hn, {item_cols},
          IF(ex.projectcode='UCEP24' AND adp.ucep>0 AND caldatetime(oo.rxdate,oo.rxtime)<=DATE_ADD(caldatetime(o.vstdate,o.vsttime),INTERVAL 24 HOUR),'01',NULL) sp_item,
          NULLIF((SELECT ovst_functional_score FROM ovst_functional WHERE vn=o.vn ORDER BY ovst_functional_id DESC LIMIT 1),0) bi
        FROM {self.ex} ex LEFT JOIN ovst o ON o.vn=ex.vn JOIN opitemrece oo ON oo.vn=o.vn {joins}
        WHERE (oo.sum_price<>0 OR d.nhso_adp_code IS NOT NULL) AND {self.noexport('oo', 'vn', False)}""")
        ipd = self.q(f"""
        SELECT oo.an seq, oo.an, oo.hn, {item_cols},
          IF(ex.projectcode='UCEP24' AND adp.ucep>0 AND caldatetime(oo.rxdate,oo.rxtime)<=DATE_ADD(caldatetime(i.regdate,i.regtime),INTERVAL 24 HOUR),'01',NULL) sp_item,
          NULLIF(IFNULL((SELECT score FROM ipd_nurse_ass_adls_scale WHERE an=i.an ORDER BY ipd_nurse_ass_adls_scale_id DESC LIMIT 1),
            (SELECT ovst_functional_score FROM ovst_functional WHERE vn=i.vn ORDER BY ovst_functional_id DESC LIMIT 1)),0) bi
        FROM {self.ex} ex LEFT JOIN ipt i ON i.an=ex.vn JOIN opitemrece oo ON oo.an=i.an {joins}
        WHERE (oo.sum_price<>0 OR d.nhso_adp_code IS NOT NULL) AND {self.noexport('oo', 'an', False)}""")

        income = []
        for r in opd + ipd:
            income.append(adp_item(r, nurse, supply))
        type_by_billmaud = {1: "10", 5: "11", 13: "12", 6: "14", 7: "15", 8: "16", 12: "17", 10: "18", 11: "19", 14: "20"}
        for r in income:
            bm = r["billmaud"]
            if bm is not None and int(bm) in type_by_billmaud:
                r["type"] = type_by_billmaud[int(bm)]
            r["type"] = None if r["type"] is None else str(r["type"])[:2]

        # GROUP BY seq,type,code,rates,sp_item (non-grouped columns come from the first row).
        groups: dict[tuple, dict] = {}
        for r in income:
            x_code = r["code"] in X_CODES and text(r["maininscl"]) in {"OFC", "LGO", "STP"}
            rates = Decimal("0") if x_code else round2(r["rate"])
            key = (r["seq"], r["type"], r["code"], rates, r["sp_item"])
            g = groups.get(key)
            if g is None:
                g = groups[key] = {**r, "rate": rates, "qty": 0, "totcopay": Decimal("0"), "total": Decimal("0"), "x_code": x_code}
            g["qty"] += 0 if r["paidst"] in {"03"} else num(r["qty"])
            g["totcopay"] += num(r["sum_price"]) if r["paidst"] in {"03"} else 0
            g["total"] += num(r["sum_price"])
        data = {}
        for key, g in groups.items():
            if g["x_code"]:
                g["qty"] = 1
            g["qty"] = int(num(g["qty"]).to_integral_value(rounding=ROUND_HALF_UP))
            g["totcopay"], g["total"] = round2(g["totcopay"]), round2(g["total"])
            if g["code"] in X_CODES and g["rate"] == 0:
                g["rate"] = g["total"]
            data[(g["seq"], g["type"], g["code"], g["rate"], g["sp_item"])] = g

        def put(row: dict) -> None:
            row = {"an": None, "billmaud": None, "chrgitem": None, "drugname": None, "totcopay": None, "total": None,
                   "tmltcode": None, "use_status": None, "status1": None, "gravida": None, "ga_week": None, "lmp": None,
                   "dcip": None, "sp_item": None, "bi": None, "demand": None, **row}
            row["rate"] = round2(row.get("rate")) if row.get("rate") is not None else None
            data[(row["seq"], row["type"], row["code"], row["rate"], row["sp_item"])] = row

        for r in self.q(f"""
            SELECT i.an seq, i.an, i.hn, i.dchdate dateopd, IF(pt.sex='1','SSSTRM','SSSTRW') code, IF(pt.sex='1',500,1000) rate, ex.maininscl
            FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn LEFT JOIN patient pt ON pt.hn=i.hn
            WHERE ex.maininscl REGEXP '^SS'
              AND EXISTS(SELECT 1 FROM iptoprt op WHERE op.an=i.an AND op.icd9 REGEXP '^637|^66[2|3]')
              AND NOT EXISTS(SELECT 1 FROM opitemrece oo JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.an=i.an AND d.nhso_adp_code REGEXP '^SSSTR')"""):
            put({**r, "billmaud": "19", "type": "3", "qty": 1, "total": r["rate"]})
        for r in self.q(f"""
            SELECT i.an seq, i.an, i.hn, i.regdate dateopd, ex.maininscl,
              (SELECT score FROM ipd_nurse_ass_adls_scale WHERE an=i.an ORDER BY ipd_nurse_ass_adls_scale_id DESC LIMIT 1) bi
            FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn
            WHERE ex.imc='Y' AND NOT EXISTS(SELECT 1 FROM opitemrece oo JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.an=i.an AND d.nhso_adp_code='IMC')"""):
            put({**r, "billmaud": "15", "type": "13", "code": "IMC", "drugname": "IMC", "qty": 1, "rate": 0,
                 "bi": int(num(r.get("bi")))})
        for r in self.q(f"""
            SELECT o.vn seq, o.hn, o.vstdate dateopd, ex.projectcode code, ex.demand, ex.maininscl
            FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn
            WHERE IFNULL(ex.projectcode,'')<>''
              AND NOT EXISTS(SELECT 1 FROM opitemrece oo JOIN s_drugitems d ON d.icode=oo.icode
                             WHERE oo.vn=o.vn AND d.nhso_adp_type_id IN (5) AND d.nhso_adp_code=ex.projectcode)"""):
            er = r["code"] == "ER-EXT" and date.today() >= date(2024, 7, 1)
            put({**r, "type": "3" if er else "5", "qty": 1, "rate": 150 if er else 0})
        for r in self.q(f"""
            SELECT i.an seq, i.an, i.hn, i.dchdate dateopd, ex.projectcode code, ex.demand, ex.maininscl
            FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn
            WHERE IFNULL(ex.projectcode,'')<>''
              AND NOT EXISTS(SELECT 1 FROM opitemrece oo JOIN s_drugitems d ON d.icode=oo.icode
                             WHERE oo.an=i.an AND d.nhso_adp_type_id IN (5) AND d.nhso_adp_code=ex.projectcode)"""):
            put({**r, "type": "5", "qty": 1})

        rows = list(data.values())
        rows = [r for r in rows if not (r["code"] == "WALKIN" and text(r.get("an")))]
        rows = [r for r in rows if not (r["code"] == "WALKIN" and r.get("demand") == "ย้ายหน่วยเกิดสิทธิทันที")]
        rows = [r for r in rows if not (r["code"] == "ER-EXT" and not self.c.ontop4)]
        for r in rows:
            if r["type"] == "5":
                r["billmaud"] = r["chrgitem"] = None
                r["qty"] = 1
            if r["code"] == "ER-EXT" and (date.today() >= date(2024, 7, 1) or r["type"] == "3"):
                r.update(billmaud="19", qty=1, rate=round2(150), total=round2(150), type="3")
        data = {(r["seq"], r["type"], r["code"], r["rate"], r["sp_item"]): r for r in rows}

        dental = {"2377020": "50001", "238703A": "50002", "238703B": "50003", "238703C": "50004", "238703D": "50005",
                  "238703E": "50006", "238703F": "50007", "238703G": "50008", "238703H": "50009"}
        for r in self.q(f"""
            SELECT o.vn seq, o.hn, o.vstdate dateopd, tm.icd10tm_operation_code tm, ex.maininscl
            FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn JOIN dtmain dt ON dt.vn=o.vn JOIN dttm tm ON tm.code=dt.tmcode
            WHERE tm.icd10tm_operation_code REGEXP '^2377020|^238703[A-H]'"""):
            put({**r, "billmaud": "18", "type": "4", "code": dental.get(text(r["tm"]), ""), "qty": 1,
                 "rate": 100 if r["tm"] == "2377020" else 250})
        for r in self.q(f"""
            SELECT o.vn seq, o.hn, o.vstdate dateopd, ex.pregno gravida, ex.ga ga_week, ex.lmp, ex.maininscl
            FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn JOIN person_anc_service s ON s.vn=o.vn
            WHERE s.anc_service_type_id=1
              AND NOT EXISTS(SELECT 1 FROM opitemrece oo JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn=o.vn AND d.nhso_adp_code='30011')"""):
            put({**r, "billmaud": "18", "type": "4", "code": "30011", "drugname": "ANC", "qty": 1, "rate": 360})
        if self.c.us_items:
            for r in self.q(f"""
                SELECT DISTINCT o.vn seq, o.hn, o.vstdate dateopd, ex.pregno gravida, ex.ga ga_week, ex.lmp, ex.maininscl
                FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn JOIN person_anc a ON a.person_anc_id=ex.ancid
                JOIN opitemrece oo ON oo.vn=o.vn
                WHERE oo.icode IN ({lit_list(self.c.us_items)}) AND {self.noexport('oo', 'vn', False)}"""):
                put({**r, "billmaud": "18", "type": "4", "code": "30010", "drugname": "U/S", "qty": 1, "rate": 400})
        for r in self.q(f"""
            SELECT DISTINCT o.vn seq, o.hn, o.vstdate dateopd, ex.maininscl
            FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn JOIN ovstdiag dx ON dx.vn=o.vn
            WHERE dx.icd10='Z515'
              AND NOT EXISTS(SELECT 1 FROM opitemrece oo JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn=o.vn AND d.nhso_adp_code='30001')"""):
            put({**r, "billmaud": "18", "type": "4", "code": "30001", "qty": 1, "rate": 0})
        pp_rows = self.q(f"""
            SELECT o.vn seq, o.hn, o.vstdate dateopd, t.pp_special_code pp, ex.maininscl
            FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn
            LEFT JOIN pp_special pp ON o.vn=pp.vn LEFT JOIN pp_special_type t ON t.pp_special_type_id=pp.pp_special_type_id
            WHERE t.pp_special_code IS NOT NULL""")
        for r in pp_rows:
            pp = text(r["pp"])
            group = {"1B0044": "1B004N", "1B30": "1B004N", "1B004N": "1B004N", "1B40": "1B004P", "1B004P": "1B004P",
                     "1B0040": "1B004_0N", "1B0043": "1B004_0N", "1B004_0N": "1B004_0N", "1B0041": "1B004_0P",
                     "1B0042": "1B004_0P", "1B0045": "1B004_0P", "1B004_0P": "1B004_0P"}
            if pp in {"1B0044", "1B30", "1B40", "1B004N", "1B004P", "1B0040", "1B0041", "1B0042", "1B0043", "1B0045", "1B004_0N", "1B004_0P"}:
                put({**r, "billmaud": "19", "type": "3", "code": group.get(pp), "qty": 1, "rate": 250})
        for r in pp_rows:
            pp = text(r["pp"])
            if re.match(r"^1B0046|^1B0048|^1B0049|^0320277_0|^0320277_1", pp):
                code = "1B0046" if pp in {"1B0046", "1B0046_0"} else "1B005" if pp in {"1B0048", "1B0049"} else pp
                put({**r, "billmaud": "18", "type": "4", "code": code, "qty": 1, "rate": 420 if code.startswith("1B0046") else 250})
        for r in pp_rows:
            if text(r["pp"]) in {"1B006", "1B0060", "1B0061"}:
                put({**r, "billmaud": "18", "type": "4", "code": "90005", "qty": 1, "rate": 60})
        rtx = {"49001": "RTX101", "49003": "RTX102", "49002": "RTX103", "49005": "RTX104", "49004": "RTX105", "49601": "RTX107",
               "49602": "RTX108", "49101": "RTX201", "49202": "RTX202", "49201": "RTX203", "49203": "RTX204", "49306": "RTX205",
               "49402": "RTX207", "49208": "RTX209", "49302": "RTX210", "49303": "RTX211"}
        for r in self.q(f"""
            SELECT o.vn seq, o.hn, o.vstdate dateopd, d.nhso_adp_code adp, oo.qty, oo.unitprice rate, ex.maininscl
            FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn JOIN opitemrece oo ON oo.vn=o.vn JOIN s_drugitems d ON d.icode=oo.icode
            WHERE d.nhso_adp_code REGEXP '^49' AND d.nhso_adp_type_id=16 AND {self.noexport('oo', 'vn', False)}"""):
            put({**r, "billmaud": "8", "type": "7", "code": rtx.get(text(r["adp"])), "qty": int(num(r["qty"]))})

        rows = list(data.values())
        covid = {(r["vn"], r["cscode"]): r["result"] for r in self.q(f"""
            SELECT lh.vn, d.nhso_adp_code cscode,
              CASE WHEN lab_order_result REGEXP 'ไม่พบ|Neg|Not' THEN '2' WHEN lab_order_result REGEXP 'พบ|Pos|Detect' THEN '1' ELSE '2' END result
            FROM {self.ex} ex JOIN lab_head lh ON lh.vn=ex.vn JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number
            JOIN lab_items l ON l.lab_items_code=lo.lab_items_code JOIN nondrugitems d ON d.icode=l.icode
            WHERE d.nhso_adp_code IN (SELECT code FROM covid_items_detail WHERE id IN (4,5))""")}
        stool = {r["vn"]: r["result"] for r in self.q(f"""
            SELECT ex.vn, CASE WHEN t.pp_special_code='1B0060' THEN '29' WHEN t.pp_special_code='1B0061' THEN '28'
              WHEN lo.lab_order_result REGEXP 'neg' THEN '29' WHEN lo.lab_order_result REGEXP 'pos' THEN '28' ELSE '30' END result
            FROM {self.ex} ex LEFT JOIN pp_special pp ON pp.vn=ex.vn
            LEFT JOIN pp_special_type t ON t.pp_special_type_id=pp.pp_special_type_id
            LEFT JOIN lab_head lh ON lh.vn=ex.vn
            LEFT JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number AND lo.lab_items_name_ref REGEXP 'stool'
            WHERE t.pp_special_code IN ('1B0060','1B0061') OR lh.vn IS NOT NULL""")}
        ages = {r["vn"]: r["age_y"] for r in self.q(
            f"SELECT v.vn, v.age_y FROM vn_stat v JOIN {self.ex} ex ON ex.vn=v.vn")}
        age_rules = {"12001": (15, 34), "12002": (35, 59), "12003": (35, 59), "12004": (45, 70), "13001": (13, 24),
                     "14001": (13, 45), "15001": (25, 59)}
        kept = []
        for r in rows:
            if (r["seq"], r["code"]) in covid:
                r["status1"] = covid[(r["seq"], r["code"])]
            if r["code"] == "90005":
                r["dcip"] = stool.get(r["seq"], "30")
            if r.get("billmaud") is None:
                r["billmaud"] = ""
            billmaud = text(r["billmaud"])
            bm_num = int(billmaud) if billmaud.lstrip("-").isdigit() else 0
            if bm_num != 15:
                r["bi"] = None
            code = r.get("code")
            type_num = int(r["type"]) if text(r.get("type")).isdigit() else 0
            keep = ((bm_num in (3, 4) and code is not None and (re.search(r"HERB|FP|^14001", code) or type_num in (1, 3, 4)))
                    or (bm_num not in (3, 4) and code is not None))
            if not keep:
                continue
            if text(r.get("an")) and re.match(r"^M", code):
                continue
            if code in age_rules and r["seq"] in ages and ages[r["seq"]] is not None:
                low, high = age_rules[code]
                if not low <= int(ages[r["seq"]]) <= high:
                    continue
            if billmaud == "17":
                continue
            kept.append(r)

        def order(r):
            bm = text(r["billmaud"])
            return (text(r["seq"]), text(r.get("an")), int(bm) if bm.lstrip("-").isdigit() else 0, text(r["code"]),
                    _desc(text(r.get("sp_item"))), num(r.get("rate")))
        kept.sort(key=order)
        for r in kept:
            r["status1"] = (text(r.get("status1")) or "")[:1]
            r["gravida"] = None if r.get("gravida") is None else str(r["gravida"])[:2]
            r["ga_week"] = None if r.get("ga_week") is None else str(r["ga_week"])[:2]
            r["code"] = text(r["code"])[:15]
        return kept

    # ------------------------------------------------------------ DRU
    def f_dru(self) -> list[dict]:
        has_pa = bool(self.q("SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() "
                             "AND table_name='opitemrece' AND column_name='pa_no'"))
        drug_ned = lit_list(self.c.income_drug_ned) if self.c.income_drug_ned else "'XXX'"
        cols = f"""
          oo.rxdate, oo.icode did, CONCAT_WS(' ',d.name,d.strength) didname, d.units unit, oo.qty, oo.sum_price,
          oo.unitprice drugpric, oo.paidst,
          CASE WHEN ex.maininscl REGEXP 'NRD|NRH|FWF' AND fwf.fwf_item_code IS NOT NULL THEN fwf.fwf_item_code ELSE d.did END didstd,
          CASE WHEN oh.opitemrece_home_type_id IS NOT NULL THEN oh.opitemrece_home_type_id
               WHEN oo.item_type='H' OR oo.income IN ({self.homemed()}) THEN
                 CASE WHEN d.drugcategory REGEXP '{CHRONIC}' THEN '4' ELSE '2' END
               ELSE '1' END use_status,
          {'oo.pa_no' if has_pa else 'NULL'} pa_no"""
        opd = self.q(f"""
        SELECT o.vn seq, NULL an, o.hn, pt.cid, sp.provis_code clinic, {cols},
          CASE WHEN IFNULL(ned.presc_reason,'')<>'' THEN ned.presc_reason
               WHEN oo.income IN ({drug_ned}) OR d.drugaccount='-' OR IFNULL(d.drugaccount,'')='' THEN 'EC' END drugremark
        FROM {self.ex} ex JOIN ovst o ON o.vn=ex.vn LEFT JOIN patient pt ON pt.hn=o.hn LEFT JOIN spclty sp ON sp.spclty=o.spclty
        JOIN opitemrece oo ON oo.vn=o.vn JOIN drugitems d ON d.icode=oo.icode
        LEFT JOIN fwf_item fwf ON fwf.fwf_item_id=d.fwf_item_id
        LEFT JOIN ovst_presc_ned ned ON ned.vn=o.vn AND ned.icode=oo.icode
        LEFT JOIN opitemrece_home oh ON oh.opi_guid=oo.hos_guid
        WHERE {self.noexport('oo', 'vn', True)}""")
        ipd = self.q(f"""
        SELECT i.an seq, i.an, i.hn, pt.cid, sp.provis_code clinic, {cols},
          CASE WHEN IFNULL(ned.presc_reason_1,'')<>'' THEN ned.presc_reason_1
               WHEN oo.income IN ({drug_ned}) OR d.drugaccount='-' OR IFNULL(d.drugaccount,'')='' THEN 'EC' END drugremark
        FROM {self.ex} ex JOIN ipt i ON i.an=ex.vn LEFT JOIN patient pt ON pt.hn=i.hn LEFT JOIN spclty sp ON sp.spclty=i.spclty
        JOIN opitemrece oo ON oo.an=i.an JOIN drugitems d ON d.icode=oo.icode
        LEFT JOIN fwf_item fwf ON fwf.fwf_item_id=d.fwf_item_id
        LEFT JOIN ipt_presc_ned ned ON ned.an=i.an AND ned.icode=oo.icode
        LEFT JOIN opitemrece_home oh ON oh.opi_guid=oo.hos_guid
        WHERE {self.noexport('oo', 'an', True)}""")
        out = []
        for part in (opd, ipd):
            groups: dict[tuple, dict] = {}
            for r in part:
                r["did"] = text(r["did"])[:13]
                r["use_status"] = text(r["use_status"])[:2]
                key = (r["seq"], r["did"], r["use_status"])
                g = groups.get(key)
                if g is None:
                    g = groups[key] = {**r, "date_serv": r["rxdate"], "amount": Decimal("0"), "totcopay": Decimal("0"),
                                       "total": Decimal("0")}
                g["date_serv"] = max(g["date_serv"], r["rxdate"]) if r["rxdate"] else g["date_serv"]
                g["amount"] += num(r["qty"])
                g["totcopay"] += num(r["sum_price"]) if r["paidst"] in {"03"} else 0
                g["total"] += num(r["sum_price"])
            for key in sorted(groups, key=lambda k: (text(k[0]), k[1], k[2])):
                g = groups[key]
                if g["total"] == 0:
                    continue
                g["amount"] = int(g["amount"].to_integral_value(rounding=ROUND_HALF_UP))
                g["drugpric"] = round2(g["drugpric"])
                g["didname"] = text(g["didname"])[:100]
                g["unit"] = text(g["unit"])[:20]
                g["drugremark"] = text(g["drugremark"])[:5]
                out.append(g)
        return out

    # ------------------------------------------------------------ LAB
    def f_lab(self) -> list[dict]:
        rows = self.q(f"""
        SELECT lh.hn, pt.cid person_id, lh.order_date dateserv, lh.vn seq, l.labtest, lo.lab_order_result labresult
        FROM {self.ex} ex JOIN lab_head lh ON lh.vn=ex.vn JOIN clinic_visit cv ON cv.vn=ex.vn
        LEFT JOIN patient pt ON pt.hn=lh.hn
        JOIN lab_order lo ON lo.lab_order_number=lh.lab_order_number JOIN lab_items l ON l.lab_items_code=lo.lab_items_code
        WHERE IFNULL(l.labtest,'')<>''""")
        for r in rows:
            m = re.match(r"\s*([+-]?\d+(?:\.\d+)?)", text(r.get("labresult")))
            r["labresult"] = round2(m.group(1)) if m else Decimal("0")
        rows = replace_rows(rows, ("seq", "labtest"))
        return [r for r in rows if r["labresult"] > 0]

    # ------------------------------------------------------------ PHDB
    def f_phdb(self) -> list[dict]:
        code = f"""
          CASE WHEN IFNULL(d.income_phdb_code,'')<>'' THEN d.income_phdb_code
               WHEN d.nhso_adp_code REGEXP '^XXXX' THEN LPAD(inc.drg_chrgitem_id,5,'X')
               WHEN d.nhso_adp_code IN ('21301') THEN IF(ex.maininscl REGEXP 'SSS',LPAD(inc.drg_chrgitem_id,5,'X'),d.nhso_adp_code)
               WHEN inc.drg_chrgitem_id IN (3,4) THEN IF(IFNULL(d.sks_drug_code,'')='',LPAD(inc.drg_chrgitem_id,5,'X'),d.sks_drug_code)
               WHEN inc.drg_chrgitem_id IN (6,7) THEN IF(IFNULL(d.tmlt_code,'')='',LPAD(inc.drg_chrgitem_id,5,'X'),d.tmlt_code)
               WHEN inc.drg_chrgitem_id=14 AND ex.maininscl REGEXP '{MY_OFC}' AND oo.rxdate>='2024-05-31' THEN 'XXX14'
               WHEN IFNULL(d.nhso_adp_code,'')<>'' THEN d.nhso_adp_code
               ELSE LPAD(inc.drg_chrgitem_id,5,'X') END"""
        part = f"""
          SELECT oo.hn, {code} code, CONCAT_WS(' ',d.name,d.strength) name, oo.qty, oo.sum_price price, {{an}} an,
            {{seq}} seq, ex.maininscl inscl, oo.hos_guid
          FROM {self.ex} ex JOIN opitemrece oo ON oo.{{key}}=ex.vn JOIN s_drugitems d ON d.icode=oo.icode
          LEFT JOIN income inc ON inc.income=oo.income
          WHERE oo.sum_price<>0 AND {self.noexport('oo', 'vn', False)}"""
        raw = self.q(part.format(an="NULL", seq="oo.vn", key="vn") + " ORDER BY oo.vn, oo.hos_guid")
        raw += self.q(part.format(an="oo.an", seq="oo.an", key="an") + " ORDER BY oo.an, oo.hos_guid")
        groups: dict[tuple, dict] = {}
        for r in raw:
            key = (text(r["seq"]), text(r["code"]))
            g = groups.get(key)
            if g is None:
                g = groups[key] = {**r, "qty": 0, "price": Decimal("0")}
                g["name"] = r["name"]
            g["qty"] += int(num(r["qty"]).to_integral_value(rounding=ROUND_HALF_UP))
            g["price"] += round2(r["price"])
        rows = [groups[k] for k in sorted(groups)]
        for r in rows:
            # Upper case like the old file; only spaces are trimmed (names can end in a stray CR, kept as before).
            r["name"] = (r["name"] or "").upper().strip(" ")
        return rows

    def f_lvd(self) -> list[dict]:
        # The old GetQuery had no LVD query, so the file was always empty.
        return []


def _desc(value: str) -> str:
    """Sort key for 'sp_item DESC' inside an ascending tuple sort."""
    return "".join(chr(0x10FFFF - ord(ch)) for ch in value) + "\U0010ffff"


def adp_item(r: dict, nurse: set[str], supply: set[str]) -> dict:
    """One tmpIncome row (aipn-style CASE from the ADP query)."""
    inscl = text(r.get("maininscl"))
    adp = r.get("adp_code")
    bm = r.get("billmaud")
    bm = int(bm) if bm is not None else None
    ofc = bool(re.search(MY_OFC, inscl))
    project = r.get("projectcode")
    if re.search("NRD|NRH|FWF", inscl) and r.get("fwf_item_code") is not None:
        code = r["fwf_item_code"]
    elif adp == "21301":
        code = "XXXXXX" if re.search("SSS", inscl) else adp
    elif bm == 5:
        code = adp if re.search("UCS", inscl) else (adp or "XXXXXX") if not supply else (adp if adp in supply else "XXXXXX")
    elif bm == 9:
        code = adp if ofc else (adp or "51999")
    elif bm == 10:
        code = adp if ofc else (adp or "52999")
    elif bm == 11 and ofc and r.get("adp_master") is not None and not text(r["adp_master"]).startswith("7"):
        code = "72999"
    elif bm == 12:
        code = adp if (ofc or project is not None) else (adp or "55999") if not nurse else (adp if adp in nurse else "55999")
    elif bm == 14 and ofc and r.get("dateopd") and r["dateopd"] >= date(2024, 5, 31):
        code = "XXX14"
    elif bm == 19:
        code = adp if ofc else (adp or "XXX19")
    elif text(adp):
        code = adp
    else:
        code = "XXXXXX"
    herb = bool(re.search("HERB", text(adp)))
    qty = num(r.get("qty"))
    if r.get("paidst") in {"03"}:
        qty = Decimal("0")
    elif herb and r.get("max_qty") is not None and qty > num(r["max_qty"]):
        qty = num(r["max_qty"])
    ofc14 = bm == 14 and ofc and r.get("dateopd") and r["dateopd"] >= date(2024, 5, 31)
    if herb:
        rate = num(r.get("stdprice"))
    elif project == "UCEP24" and num(r.get("ucep")) > 0:
        rate = num(r["ucep"])
    elif ofc14:
        rate = Decimal("0")
    else:
        rate = num(r.get("unitprice"))
    price = num(r.get("sum_price")) if ofc14 else num(r.get("qty")) * rate
    return {
        "seq": r["seq"], "an": r.get("an"), "hn": r.get("hn"), "dateopd": r.get("dateopd"), "maininscl": inscl,
        "paidst": r.get("paidst"), "billmaud": bm, "chrgitem": r.get("chrgitem"), "drugname": r.get("drugname"),
        "type": r.get("type"), "code": code, "qty": num(r.get("qty")) if r.get("paidst") not in {"03"} and not herb else qty,
        "rate": rate, "sum_price": price, "tmltcode": r.get("tmltcode"),
        "use_status": "2" if text(r.get("type")) == "11" else None, "status1": None,
        "gravida": r.get("gravida"), "ga_week": r.get("ga_week"), "lmp": r.get("lmp"), "dcip": None,
        "sp_item": r.get("sp_item"), "bi": r.get("bi"), "demand": r.get("demand"),
    }


def replace_rows(rows: list[dict], key: Sequence[str]) -> list[dict]:
    """REPLACE INTO a keyed table: a later row replaces an earlier one in the earlier row's place."""
    index: dict[tuple, int] = {}
    out: list[dict] = []
    for r in rows:
        k = tuple(text(r.get(c)) for c in key)
        if k in index:
            out[index[k]] = r
        else:
            index[k] = len(out)
            out.append(r)
    return out


def finish(name: str, rows: list[dict]) -> list[dict]:
    """Load into the keyed Export4 table, then blank SEQ for IPD rows (ExportDBF)."""
    rows = replace_rows(rows, KEYS[name])
    columns = {c for c, _, _ in SCHEMA[name]}
    if {"AN", "SEQ"} <= columns:
        for r in rows:
            if text(r.get("AN")):
                r["SEQ"] = ""
    return rows


# ---------------------------------------------------------------- output
def fmt(value, kind: str, size: int) -> str:
    if kind == "D":
        d = as_date(value)
        return f"{d:%Y%m%d}" if d else ""
    if kind == "I":
        return str(int(num(value).to_integral_value(rounding=ROUND_HALF_UP)))
    if kind.startswith("N"):
        places = int(kind[1:])
        q = Decimal(1).scaleb(-places) if places else Decimal(1)
        return f"{num(value).quantize(q, rounding=ROUND_HALF_UP):.{places}f}"
    if value is None:
        return ""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int):
        s = str(value)
    elif isinstance(value, (float, Decimal)):
        s = f"{num(value).quantize(Decimal('0.00001'), rounding=ROUND_HALF_UP):.5f}"
    elif isinstance(value, (date, datetime)):
        s = f"{value:%Y%m%d}"
    else:
        s = str(value)
    s = s.replace("|", " ")
    return s[:size].strip(" ")


def to_txt(name: str, rows: list[dict]) -> bytes:
    cols = SCHEMA[name]
    lines = ["|".join(HEADER_NAMES.get((name, c), c) for c, _, _ in cols)]
    for r in rows:
        lines.append("|".join(fmt(r.get(c), k, s) for c, k, s in cols))
    try:
        return (CRLF.join(lines) + CRLF).encode(ENCODING, errors="strict")
    except UnicodeEncodeError as exc:
        raise Nhso18Error(f"แฟ้ม {name} มีอักขระที่ใช้กับ windows-874 ไม่ได้: {exc.object[exc.start]!r}") from exc


@dataclass
class Result:
    zip_path: Path
    counts: dict[str, int]


def build_zip(rows: dict[str, list[dict]], hcode: str, output_root: Path, now: datetime | None = None) -> Result:
    now = (now or datetime.now()).replace(microsecond=0)
    folder = output_root / f"NHSO18_{now:%Y%m%d-%H%M%S}"
    zip_path = folder / f"{hcode}_18FILES_{now:%Y%m%d%H%M%S}.zip"
    try:
        folder.mkdir(parents=True, exist_ok=False)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in FILES:
                archive.writestr(f"{name}.TXT", to_txt(name, rows.get(name, [])))
        with zipfile.ZipFile(zip_path) as archive:
            if archive.testzip() is not None:
                raise Nhso18Error("สร้างไฟล์ ZIP ไม่สำเร็จ")
    except OSError as exc:
        raise Nhso18Error(f"บันทึกไฟล์ส่งออกไม่สำเร็จ: {exc.__class__.__name__}") from exc
    return Result(zip_path, {name: len(rows.get(name, [])) for name in FILES})


# ---------------------------------------------------------------- context
ADP_UUC = (r"30001|42330|42333|42338|42339|Cons01|Eva001|DRUGP|TELMED|STEMI1|STROK1|SPECC|^37550|30009|^2006|^2007|^13001|"
           r"^15001|1B004|1B005|^AB00|^3001[0-7]|^1200[1-4]|^9000[1-5]|^DEN|^FP|^HERB|^HC0|^HD000|80008")


@dataclass
class PttypeGroups:
    """Right groups the old ResetPttype read from sys_var."""
    referin: tuple[str, ...] = ()
    oprefer: tuple[str, ...] = ()
    cuprefer: tuple[str, ...] = ()
    moveright: tuple[str, ...] = ()
    walkin: tuple[str, ...] = ()
    emergency: tuple[str, ...] = ()
    ontop4: bool = False
    ontop6: bool = False


def opd_contexts(select: Callable[[str], list[dict]], vns: Sequence[str], groups: PttypeGroups, zone: str) -> list[Visit]:
    """tmpExport rows for OPD visits (AddOPD + Data2Report + CheckUUC)."""
    if not vns:
        return []
    vn_list = lit_list(vns)
    rows = select(f"""
    SELECT o.vn, o.hn, o.vstdate, o.an, seekhipdatas(o.pttype,0) maininscl, pttypeeclaim(o.pttype) pttype_eclaim,
      hospmain(o.vn) hospmain, v.pdx, timestampdiff(year,pt.birthday,o.vstdate) age,
      seekexport(o.ovstost,'ovstost',NULL) discharge,
      CASE
        WHEN o.pttype IN ({lit_list(groups.oprefer)}) OR o.pttype IN ({lit_list(groups.referin)}) THEN 'OP Refer ข้ามจังหวัด'
        WHEN o.pttype IN ({lit_list(groups.cuprefer)}) THEN 'OP Refer ในจังหวัด'
        WHEN o.pttype IN ({lit_list(groups.moveright)}) THEN 'ย้ายหน่วยเกิดสิทธิทันที'
        WHEN tt.ucae='A' THEN 'Accident'
        WHEN tt.ucae='E' THEN 'Emergency'
        WHEN o.pttype IN ({lit_list(groups.emergency)}) THEN IF(v.pdx REGEXP 'S|T','Accident','Emergency')
      END demand,
      (SELECT t.nhso_ucae_type_name FROM visit_pttype vp JOIN nhso_ucae_type t ON t.nhso_ucae_type_code=vp.nhso_ucae_type_code
        WHERE vp.vn=o.vn AND vp.pttype=o.pttype LIMIT 1) demand_set,
      CASE
        WHEN IFNULL((SELECT vp.project_code FROM visit_pttype vp WHERE vp.vn=o.vn AND IFNULL(vp.project_code,'')<>''
                     AND vp.project_code NOT REGEXP '^PG' LIMIT 1),'')<>''
          THEN (SELECT vp.project_code FROM visit_pttype vp WHERE vp.vn=o.vn AND IFNULL(vp.project_code,'')<>''
                AND vp.project_code NOT REGEXP '^PG' LIMIT 1)
        WHEN o.pttype IN ({lit_list(groups.oprefer)}) THEN IF({lit(zone)}='09' AND hospitalzone(v.hospmain)='09','R9OPFS','')
        WHEN o.pttype IN ({lit_list(groups.cuprefer)}) THEN IF({lit(zone)}='09' AND hospitalzone(v.hospmain)='09','R9OPCH','')
        WHEN o.pttype IN ({lit_list(groups.walkin)}) THEN 'WALKIN'
        WHEN EXISTS(SELECT 1 FROM opitemrece oo JOIN nondrugitems d ON d.icode=oo.icode WHERE oo.vn=o.vn
                    AND d.nhso_adp_code IN (SELECT code FROM covid_items_detail)) THEN 'SCRCOV'
        WHEN diagnosis('opd',o.vn,0) REGEXP 'Z115' THEN 'SCRCOV'
        WHEN v.pdx BETWEEN 'C00' AND 'D4899' OR diagnosis('opd',o.vn,0) REGEXP 'Z511|Z08' THEN 'CANCER'
      END projectcode,
      (SELECT d.nhso_adp_code FROM opitemrece oo LEFT JOIN s_drugitems d ON d.icode=oo.icode
        WHERE oo.vn=o.vn AND d.nhso_adp_type_id=5 LIMIT 1) project_item,
      (SELECT IF(request_funds='N','2','') FROM visit_pttype WHERE vn=o.vn LIMIT 1) uuc_flag,
      EXISTS(SELECT 1 FROM opitemrece oo JOIN s_drugitems d ON d.icode=oo.icode WHERE oo.vn=o.vn AND (
        d.nhso_adp_code REGEXP '{ADP_UUC}' OR d.name REGEXP 'Clopido|streptoki|Tamoxifen|methadone'
        OR d.nhso_adp_type_id IN (2,5)
        OR (d.nhso_adp_code REGEXP '^14001' AND timestampdiff(year,pt.birthday,o.vstdate) BETWEEN 13 AND 45))) uuc_item
    FROM ovst o
    LEFT JOIN patient pt ON pt.hn=o.hn
    LEFT JOIN vn_stat v ON v.vn=o.vn
    LEFT JOIN er_regist er ON er.vn=o.vn
    LEFT JOIN er_pt_type tt ON tt.er_pt_type=er.er_pt_type
    WHERE o.vn IN ({vn_list})""")
    # The old program used the first pregnancy record within 50 weeks of the visit (MIN(person_anc_id)).
    first_anc = {}
    for r in select(f"""
    SELECT o.vn, MIN(a.person_anc_id) ancid FROM ovst o JOIN person p ON p.patient_hn=o.hn
    JOIN person_anc a ON a.person_id=p.person_id AND timestampdiff(week,a.lmp,o.vstdate) BETWEEN 0 AND 50
    WHERE o.vn IN ({vn_list}) GROUP BY o.vn"""):
        first_anc[r["vn"]] = r["ancid"]
    anc_rows = {r["person_anc_id"]: r for r in select(
        f"SELECT person_anc_id, preg_no, lmp FROM person_anc WHERE person_anc_id IN ({lit_list(first_anc.values())})")} if first_anc else {}
    out = []
    for r in rows:
        vn = r["vn"]
        demand = text(r.get("demand_set")) or text(r.get("demand"))
        project = text(r.get("project_item")) or text(r.get("projectcode"))
        if project == "ADSCOV":
            project = ""
        if project in {"ADSCOV", "SCRCOV"} and r["vstdate"] >= date(2023, 10, 1):
            project = ""
        if project == "CANCER" and not groups.ontop6:
            project = ""
        if project == "ER-EXT" and not groups.ontop4:
            project = ""
        if project in {"R9OPCH", "R9OPFS"}:
            demand = ""
        elif project == "ER-EXT":
            demand = demand or "Normal"
        uuc = "1"
        if text(r.get("uuc_flag")):
            uuc = text(r["uuc_flag"])
        if text(r.get("an")):
            uuc = "1"
        elif text(r.get("pttype_eclaim")) in {"01", "02", "27"}:
            uuc = "2"
        if r.get("uuc_item"):
            uuc = "1"
        elif project or demand:
            uuc = "1"
        elif text(r.get("discharge")).startswith("2"):
            uuc = "2"
        ancid = first_anc.get(vn)
        anc_row = anc_rows.get(ancid, {})
        lmp = anc_row.get("lmp")
        ga = None
        if lmp and r["vstdate"]:
            ga = (r["vstdate"] - lmp).days // 7
        out.append(Visit(
            vn=vn, comedate=r["vstdate"], uuc=uuc, maininscl=text(r.get("maininscl")), hospmain=text(r.get("hospmain")),
            ods="Y" if demand == "Ambulatory Care" else "", demand=demand, projectcode=project,
            ancid=ancid, pregno=anc_row.get("preg_no"), lmp=lmp, ga=ga,
        ))
    order = {vn: i for i, vn in enumerate(vns)}
    return sorted(out, key=lambda v: order.get(v.vn, 0))
