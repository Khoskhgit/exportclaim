"""Read-only HOSxP queries for AIPN (in-patient claims). Queries follow aipn.txt AddData/ReadData.

The old program built temporary tables and used session variables; here every
statement is a single SELECT and the per-row calculation is done in ``aipn.py``.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from .hosxp import Hosxp

AIPN_ECLAIM_IDS = ("10", "11", "12", "13", "14", "15")


def _marks(values: Sequence) -> str:
    return ",".join(["%s"] * len(values))


class AipnDb:
    def __init__(self, hosxp: Hosxp):
        self.h = hosxp

    @property
    def settings(self):
        return self.h.settings

    def pttypes(self) -> list[dict]:
        """Rights offered by the old AIPN screen (aipn.txt:144)."""
        return self.h._select(
            "SELECT pttype, name FROM pttype WHERE isuse='Y' AND (hipdata_code REGEXP 'SS' OR pcode='A7' "
            "OR name REGEXP %s OR pttype_eclaim_id BETWEEN 10 AND 15) ORDER BY pttype",
            ("ประกันสังคม",),
        )

    # ------------------------------------------------------------- admissions
    def search(self, start=None, end=None, pttypes: Iterable[str] = (), an: str = "") -> list[dict]:
        if an:
            return self._admissions("i.an=%s", [an])
        codes = list(pttypes)
        if not (start and end and codes):
            from .hosxp import HosxpError
            raise HosxpError("ต้องระบุช่วงวันที่จำหน่ายและสิทธิ")
        return self._admissions(
            f"i.dchdate BETWEEN %s AND %s AND i.pttype IN ({_marks(codes)})", [start, end, *codes],
        )

    def by_an(self, ans: Sequence[str]) -> list[dict]:
        if not ans:
            return []
        return self._admissions(f"i.an IN ({_marks(ans)})", list(ans))

    def _admissions(self, where: str, params: list) -> list[dict]:
        h = self.h
        rcm_status = "NULL"
        parts = []
        if h._has_rcm("repeclaim"):
            parts.append(
                f"WHEN EXISTS(SELECT 1 FROM {h._rcm('repeclaim')} r WHERE r.an=i.an AND r.department='IP' "
                "AND IFNULL(r.errorcode,'')='') THEN 'ชดเชยแล้ว'"
            )
        if h._has_rcm("repdata"):
            parts.append(
                f"WHEN EXISTS(SELECT 1 FROM {h._rcm('repdata')} r WHERE r.an=i.an AND r.department='IP' "
                "AND IFNULL(r.errorcode,'')='') THEN 'ส่งแล้ว'"
            )
        if h._has_rcm("chi_send"):
            parts.append(
                f"WHEN EXISTS(SELECT 1 FROM {h._rcm('chi_send')} c WHERE c.seq=i.an AND IFNULL(c.flag,'')<>'') THEN 'ติด C'"
            )
        if parts:
            rcm_status = "CASE " + " ".join(parts) + " ELSE NULL END"
        upp_col, upp_join = "NULL", ""
        if h._has("pttype_upp_type"):
            upp_col = "upp.pttype_upp_type_code"
            upp_join = "LEFT JOIN pttype_upp_type upp ON upp.pttype_upp_type_id=ptt.pttype_upp_type_id"
        cancer_col = "NULL"
        if h._has("ssocac_registry"):
            cancer_col = (
                "(SELECT MAX(ca.caseno) FROM ssocac_registry ca WHERE ca.hn=i.hn AND IFNULL(ca.status,'')<>'9')"
            )
        eclaim = _marks(AIPN_ECLAIM_IDS)
        sql = f"""
        SELECT i.an, i.hn, i.vn, i.pttype, i.regdate, i.regtime, i.dchdate, i.dchtime, i.ods,
          IF(pt.nationality=99, pt.cid,
             (SELECT MAX(pc.cardno) FROM ptcardno pc WHERE pc.hn=pt.hn AND pc.cardtype='02')) AS cid,
          CONCAT(IFNULL(pt.pname,''), IFNULL(pt.fname,''), ' ', IFNULL(pt.lname,'')) AS ptname,
          pt.birthday, pt.nationality,
          sp.nhso_code AS clinic, sp.name AS clinic_name,
          ds.name AS dchstts_name, dt.name AS dchtype_name,
          ptt.name AS pttype_name, ptt.hipdata_code, SeekHipData(ptt.hipdata_code,0) AS maininscl,
          ptt.pttype_eclaim_id, ec.name AS eclaim_name, {upp_col} AS upp_code,
          (SELECT GROUP_CONCAT(DISTINCT ip.pttype) FROM ipt_pttype ip JOIN pttype p2 ON p2.pttype=ip.pttype
            WHERE ip.an=i.an AND ip.pttype<>i.pttype AND p2.pttype_eclaim_id IN ({eclaim})) AS copttype,
          hospmain(i.an) AS hospmain,
          a.income, a.rcpt_money, a.discount_money,
          (SELECT dx.icd10 FROM iptdiag dx WHERE dx.an=i.an AND dx.diagtype=1 AND dx.icd10 NOT REGEXP '^[0-9]'
            ORDER BY dx.ipt_diag_id LIMIT 1) AS pdx,
          doctorlicense(i.admdoctor) AS doctor,
          ipp.claim_code AS auth_code, ipp.auth_datetime,
          {rcm_status} AS rcm_status, {cancer_col} AS cancer_caseno
        FROM ipt i
        LEFT JOIN patient pt ON pt.hn=i.hn
        LEFT JOIN an_stat a ON a.an=i.an
        LEFT JOIN pttype ptt ON ptt.pttype=i.pttype
        LEFT JOIN pttype_eclaim ec ON ec.code=ptt.pttype_eclaim_id
        {upp_join}
        LEFT JOIN spclty sp ON sp.spclty=i.spclty
        LEFT JOIN dchstts ds ON ds.dchstts=i.dchstts
        LEFT JOIN dchtype dt ON dt.dchtype=i.dchtype
        LEFT JOIN ipt_pttype ipp ON ipp.an=i.an AND ipp.pttype=i.pttype
        WHERE {where}
        GROUP BY i.an
        ORDER BY i.hn, i.an
        LIMIT 5000
        """
        return h._select(sql, [*AIPN_ECLAIM_IDS, *params])

    # ------------------------------------------------------------------ items
    def items(self, ans: Sequence[str]) -> list[dict]:
        """One row per opitemrece row; grouping and claim rules are applied in Python."""
        if not ans:
            return []
        h = self.h
        catalog = h._rcm("chi_drugcatalog")
        lab = h._rcm("labitem")
        labcat = h._rcm("labcat") if h._has_rcm("labcat") else None
        lab_pick = f"FROM {lab} l WHERE l.icode=oo.icode AND l.chi='1' AND l.benifitplan REGEXP 'SS' LIMIT 1"
        labcat_col = (
            f"(SELECT MAX(ll.unitprice) FROM {labcat} ll WHERE ll.lccode=oo.icode)" if labcat else "NULL"
        )
        noexport_col = "0"
        if h._has("opitemrece_noexport"):
            noexport_col = (
                "EXISTS(SELECT 1 FROM opitemrece_noexport nn WHERE nn.seq=oo.an AND nn.icode=oo.icode "
                "AND nn.type=IF(inc.group1 IN (3,4) AND oo.item_type='H','H',''))"
            )
        ssocac_col = "0"
        if h._has("equipdev_aipn"):
            ssocac_col = "EXISTS(SELECT 1 FROM equipdev_aipn eq WHERE eq.code=d.sks_drug_code AND eq.remark='SSOCAC')"
        sql = f"""
        SELECT oo.an, oo.hn, oo.icode, oo.income, inc.group1, oo.item_type, oo.paidst,
          oo.qty, oo.unitprice, oo.sum_price, oo.rxdate, oo.rxtime, oo.pttype,
          d.name, d.strength, d.nhso_adp_code, d.nhso_adp_type_id, d.sks_drug_code,
          (SELECT dg.tpu_code_list FROM drugitems dg WHERE dg.icode=oo.icode) AS tpu_code_list,
          EXISTS(SELECT 1 FROM drugitems dg WHERE dg.icode=oo.icode) AS is_drug,
          (SELECT dd.tmtid FROM {catalog} dd WHERE dd.hospdrugcode=oo.icode) AS tmtid,
          (SELECT dd.unitprice FROM {catalog} dd WHERE dd.hospdrugcode=oo.icode) AS catalog_price,
          EXISTS(SELECT 1 FROM {catalog} dd WHERE dd.hospdrugcode=oo.icode) AS in_catalog,
          (SELECT l.tmlt_code {lab_pick}) AS tmlt_code,
          EXISTS(SELECT 1 {lab_pick}) AS in_labitem,
          {labcat_col} AS lab_price,
          SeekHipData(ptt.hipdata_code,0) AS maininscl, ptt.pttype_eclaim_id,
          {ssocac_col} AS ssocac_item, {noexport_col} AS hosxp_noexport
        FROM opitemrece oo
        LEFT JOIN income inc ON inc.income=oo.income
        LEFT JOIN s_drugitems d ON d.icode=oo.icode
        LEFT JOIN pttype ptt ON ptt.pttype=oo.pttype
        WHERE oo.an IN ({_marks(ans)})
        ORDER BY oo.an, oo.icode, oo.rxdate, oo.rxtime
        """
        return h._select(sql, list(ans))

    def claimcat(self, icodes: Iterable[str]) -> list[dict]:
        icodes = sorted({c for c in icodes if c})
        if not icodes or not self.h._has_rcm("claimcat"):
            return []
        return self.h._select(
            f"SELECT icode, income, start_date, finish_date, bill_code, claim_cat, claim_amount, rev_date, remark "
            f"FROM {self.h._rcm('claimcat')} WHERE maininscl='SSS' AND icode IN ({_marks(icodes)})",
            icodes,
        )

    # ------------------------------------------------------- file sections
    def adt(self, ans: Sequence[str]) -> list[dict]:
        """IPADT fields in file order (aipn.txt:1404)."""
        if not ans:
            return []
        return self.h._select(f"""
        SELECT i.an, i.hn, 0 AS idtype, IF(pt.nationality=99, pt.cid, pc.cardno) AS pidpat, pt.pname AS title,
          CONCAT(pt.fname,' ',pt.lname) AS namepat, DATE_FORMAT(pt.birthday,'%%Y-%%m-%%d') AS dob, pt.sex,
          IF(pt.marrystatus IN (1,2,3), pt.marrystatus, 4) AS marry,
          zero(pt.chwpart) AS changwat, zero(pt.amppart) AS ampur,
          IF(pt.nationality IN (44,45,46,48,56,57,99), pt.nationality, '97') AS nation,
          'O' AS admtype, 'O' AS admsource,
          DATE_FORMAT(CONCAT(i.regdate,' ',i.regtime),'%%Y-%%m-%%dT%%T') AS dtadm,
          DATE_FORMAT(CONCAT(i.dchdate,' ',i.dchtime),'%%Y-%%m-%%dT%%T') AS dtdisch,
          i.leave_home_day AS leaveday,
          seekexport(i.dchstts,'dchstts',NULL) AS dischs, seekexport(i.dchtype,'dchtype',NULL) AS discht,
          CAST(CAST(i.bw/1000 AS DECIMAL(10,3)) AS CHAR(7)) AS bw, i.ward,
          IF(sp.nhso_code BETWEEN '01' AND '12', sp.nhso_code, '01') AS clinic
        FROM ipt i
        LEFT JOIN patient pt ON pt.hn=i.hn
        LEFT JOIN ptcardno pc ON pc.hn=pt.hn AND pc.cardtype='02'
        LEFT JOIN spclty sp ON sp.spclty=i.spclty
        WHERE i.an IN ({_marks(ans)})
        GROUP BY i.an
        """, list(ans))

    def dx(self, ans: Sequence[str]) -> list[dict]:
        if not ans:
            return []
        tm = "EXISTS(SELECT 1 FROM ovst_sks_icd10tm b WHERE b.code=dx.icd10)" if self.h._has("ovst_sks_icd10tm") else "0"
        return self.h._select(f"""
        SELECT dx.an, dx.diagtype AS subclass, dx.icd10 AS code, icdname(dx.icd10) AS name,
          doctorlicense(i.admdoctor) AS doctor, aa.codeset, {tm} AS is_tm
        FROM iptdiag dx
        JOIN ipt i ON i.an=dx.an
        LEFT JOIN icd101 aa ON aa.code=dx.icd10
        WHERE dx.an IN ({_marks(ans)})
        ORDER BY dx.an, dx.diagtype, dx.ipt_diag_id
        """, list(ans))

    def ops(self, ans: Sequence[str]) -> list[dict]:
        if not ans:
            return []
        return self.h._select(f"""
        SELECT cc.an, cc.icd9, cc.ext_code, icdname(cc.icd9) AS name, doctorlicense(cc.doctor) AS doctor,
          cc.opdate, cc.optime, cc.enddate, cc.endtime, i.regdate, i.regtime, i.dchdate, i.dchtime
        FROM iptoprt cc
        JOIN ipt i ON i.an=cc.an
        WHERE cc.an IN ({_marks(ans)})
        ORDER BY cc.an, cc.iptoprt_id
        """, list(ans))

    def copay(self, ans: Sequence[str]) -> list[dict]:
        """Other rights on the admission, for the Coinsurance section (aipn.txt:1640)."""
        if not ans:
            return []
        return self.h._select(f"""
        SELECT ip.an, ip.pttype, ip.max_debt_amount AS total, ptt.hipdata_code AS code, ptt.pttype_eclaim_id AS id
        FROM ipt_pttype ip
        LEFT JOIN pttype ptt ON ptt.pttype=ip.pttype
        WHERE ip.an IN ({_marks(ans)}) AND IFNULL(ptt.pttype_eclaim_id,'') NOT IN ('10','11','12','13')
        ORDER BY ip.an, ip.pttype
        """, list(ans))

    def missing_debt(self, ans: Sequence[str]) -> set[str]:
        """Admissions with an accident/other right that has no credit limit (warning in the old program)."""
        if not ans:
            return set()
        rows = self.h._select(f"""
        SELECT DISTINCT ip.an FROM ipt_pttype ip JOIN pttype ptt ON ptt.pttype=ip.pttype
        WHERE ip.an IN ({_marks(ans)}) AND ptt.pttype_eclaim_id IN ('14','15','28') AND ip.max_debt_amount IS NULL
        """, list(ans))
        return {r["an"] for r in rows}
