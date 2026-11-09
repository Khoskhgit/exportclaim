"""Read-only access to HOSxP. Every statement goes through ``_select``."""
from __future__ import annotations

import re
import threading
from typing import Any, Iterable, Sequence

import pymysql
from pymysql.cursors import DictCursor

from .config import IDENTIFIER, Settings

REQUIRED_TABLES = (
    "ovst", "vn_stat", "patient", "ptcardno", "pttype", "spclty", "opitemrece",
    "s_drugitems", "income", "income_report2", "ovstist", "ovstost", "drugusage",
    "sp_use", "ovstdiag", "icd101", "icd10_nhso", "doctor", "ptdepart", "oapp",
    "nondrugitems", "ovst_presc_ned",
)
OPTIONAL_TABLES = (
    "icd10_ssop_z", "opitemrece_noexport", "hi_register",
    "ovst_sks_icd10tm", "pttype_upp_type",
)
OPTIONAL_RCM_TABLES = ("chi", "repeclaim", "labitem", "claimcat")
REQUIRED_COLUMNS = (
    ("ovstist", "export_code"), ("ovstost", "export_code"),
    ("income_report2", "cscd_group"), ("spclty", "nhso_code"),
)
WRITE_PRIVILEGES = re.compile(
    r"\b(ALL PRIVILEGES|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE)\b", re.I
)
_READ_ONLY_SQL = re.compile(r"^\s*(SELECT|SHOW)\b", re.I)


class HosxpError(RuntimeError):
    """A database problem that can be shown to the user."""


class Hosxp:
    def __init__(self, settings: Settings):
        self.settings = settings
        # FastAPI runs requests on many threads at once; a pymysql connection must not be shared between them
        # (two threads reading one socket fails with "PyMemoryView_FromBuffer(): info->buf must not be NULL").
        self._local = threading.local()
        self._all: set = set()
        self._all_lock = threading.Lock()
        self._schema: dict[str, Any] | None = None

    @property
    def _conn(self):
        return getattr(self._local, "conn", None)

    @_conn.setter
    def _conn(self, conn) -> None:
        old = getattr(self._local, "conn", None)
        if old is not None and old is not conn:
            with self._all_lock:
                self._all.discard(old)
            try:
                old.close()
            except Exception:
                pass
        self._local.conn = conn
        if conn is not None:
            with self._all_lock:
                self._all.add(conn)

    # ------------------------------------------------------------- connection
    def _connect(self):
        if self._conn is not None and self._conn.open:
            try:
                self._conn.ping(reconnect=False)
                return self._conn
            except pymysql.MySQLError:
                self._conn = None
        s = self.settings
        try:
            conn = pymysql.connect(
                host=s.db_host, port=s.db_port, user=s.db_user, password=s.db_password,
                database=s.db_name, charset="tis620", cursorclass=DictCursor,
                connect_timeout=10, read_timeout=300, autocommit=True,
            )
            with conn.cursor() as cur:
                # A second safety net: the server refuses writes on this session.
                cur.execute("SET SESSION TRANSACTION READ ONLY")
        except pymysql.MySQLError as exc:
            code = exc.args[0] if exc.args else "-"
            reason = "user หรือรหัสผ่านใน .env ไม่ถูกต้อง หรือ user นี้ไม่มีสิทธิ์เข้าจากเครื่องนี้ " if code == 1045 else ""
            raise HosxpError(f"เชื่อมต่อฐาน HOSxP ไม่ได้: {reason}(รหัส {code})") from exc
        self._conn = conn
        return conn

    def _select(self, sql: str, params: Sequence[Any] | None = None) -> list[dict]:
        if not _READ_ONLY_SQL.match(sql):
            raise HosxpError("โปรแกรมอนุญาตเฉพาะคำสั่งอ่านข้อมูล")
        try:
            with self._connect().cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
        except pymysql.MySQLError as exc:
            self._conn = None
            raise HosxpError(f"อ่านข้อมูลไม่สำเร็จ: {exc.args[-1] if exc.args else exc}") from exc
        except (OSError, ValueError, AttributeError) as exc:
            # A broken socket can surface as a plain Python error; drop the connection so the next query reconnects.
            self._conn = None
            raise HosxpError(f"การเชื่อมต่อฐานข้อมูลขาด กรุณาลองใหม่ ({type(exc).__name__})") from exc

    def _rcm(self, table: str) -> str:
        if not IDENTIFIER.match(self.settings.rcm_db):
            raise HosxpError("RCM_DB มีอักขระที่ไม่อนุญาต")
        return f"`{self.settings.rcm_db}`.`{table}`"

    # ----------------------------------------------------------------- schema
    def schema(self, refresh: bool = False) -> dict[str, Any]:
        if self._schema is not None and not refresh:
            return self._schema
        s = self.settings
        tables = {
            r["t"].lower() for r in self._select(
                "SELECT table_name t FROM information_schema.tables WHERE table_schema=%s",
                (s.db_name,),
            )
        }
        rcm_tables = {
            r["t"].lower() for r in self._select(
                "SELECT table_name t FROM information_schema.tables WHERE table_schema=%s",
                (s.rcm_db,),
            )
        }
        columns = {
            (r["t"].lower(), r["c"].lower()) for r in self._select(
                "SELECT table_name t, column_name c FROM information_schema.columns WHERE table_schema=%s",
                (s.db_name,),
            )
        }
        functions = {
            r["f"].lower() for r in self._select(
                "SELECT routine_name f FROM information_schema.routines "
                "WHERE routine_schema=%s AND routine_type='FUNCTION'",
                (s.db_name,),
            )
        }
        self._schema = {
            "tables": tables, "rcm_tables": rcm_tables,
            "columns": columns, "functions": functions,
        }
        return self._schema

    def preflight(self) -> dict[str, Any]:
        """Check the database before use; reports problems, never changes anything."""
        schema = self.schema(refresh=True)
        missing_tables = [t for t in REQUIRED_TABLES if t not in schema["tables"]]
        missing_columns = [
            f"{t}.{c}" for t, c in REQUIRED_COLUMNS
            if t in schema["tables"] and (t, c) not in schema["columns"]
        ]
        optional_missing = [t for t in OPTIONAL_TABLES if t not in schema["tables"]]
        optional_missing += [
            f"{self.settings.rcm_db}.{t}" for t in OPTIONAL_RCM_TABLES
            if t not in schema["rcm_tables"]
        ]
        grants = [next(iter(r.values())) for r in self._select("SHOW GRANTS")]
        return {
            "ok": not missing_tables and not missing_columns,
            "missing_tables": missing_tables,
            "missing_columns": missing_columns,
            "optional_missing": optional_missing,
            "functions": sorted(
                schema["functions"] & {"doctorlicense", "hospmain", "icdname"}
            ),
            "user_can_write": any(WRITE_PRIVILEGES.search(str(g)) for g in grants),
        }

    def _has(self, table: str) -> bool:
        return table in self.schema()["tables"]

    def _has_rcm(self, table: str) -> bool:
        return table in self.schema()["rcm_tables"]

    def _fn(self, name: str) -> bool:
        return name in self.schema()["functions"]

    # ----------------------------------------------------------------- lookup
    def pttypes(self) -> list[dict]:
        return self._select(
            "SELECT pttype, name FROM pttype WHERE isuse='Y' AND "
            "(hipdata_code REGEXP 'SS' OR pcode='A7' OR name REGEXP %s) ORDER BY pttype",
            ("ประกันสังคม",),
        )

    def result_code_names(self) -> dict[str, str]:
        """Meaning of claim result codes, from HOSxP's own table."""
        if not self._has("ovst_sss_err_type"):
            return {}
        return {r["err_code"]: r["err_name"] for r in self._select("SELECT err_code, err_name FROM ovst_sss_err_type")}

    def z_codes(self) -> set[str]:
        if not self._has("icd10_ssop_z"):
            return set()
        return {r["code"].strip().upper() for r in self._select("SELECT code FROM icd10_ssop_z")}

    # ----------------------------------------------------------------- visits
    def search_visits(
        self, start=None, end=None, pttypes: Iterable[str] = (), vn: str = "", hn: str = "",
    ) -> list[dict]:
        where, params = [], []
        if vn:
            where.append("o.vn=%s")
            params.append(vn)
        elif hn:
            where.append("o.hn=%s")
            params.append(hn)
            if start and end:
                where.append("o.vstdate BETWEEN %s AND %s")
                params += [start, end]
        else:
            codes = list(pttypes)
            if not (start and end and codes):
                raise HosxpError("ต้องระบุช่วงวันที่และสิทธิ")
            where.append("o.vstdate BETWEEN %s AND %s AND IFNULL(o.an,'')=''")
            params += [start, end]
            where.append("o.pttype IN (" + ",".join(["%s"] * len(codes)) + ")")
            params += codes
        return self._fetch_visits(" AND ".join(where), params)

    def visits_by_vn(self, vns: Sequence[str]) -> list[dict]:
        if not vns:
            return []
        return self._fetch_visits("o.vn IN (" + ",".join(["%s"] * len(vns)) + ")", list(vns))

    def _fetch_visits(self, where: str, params: list) -> list[dict]:
        doctor = "doctorlicense(o.doctor)" if self._fn("doctorlicense") else "dr.licenseno"
        hospmain = "hospmain(o.vn)" if self._fn("hospmain") else "v.hospmain"
        upp_col = "upp.pttype_upp_type_code" if self._has("pttype_upp_type") else "NULL"
        inscl_col = "SeekHipData(ptt.hipdata_code,0)" if self._fn("seekhipdata") else "ptt.hipdata_code"
        upp_join = (
            "LEFT JOIN pttype_upp_type upp ON upp.pttype_upp_type_id=ptt.pttype_upp_type_id"
            if self._has("pttype_upp_type") else ""
        )
        chi_col, chi_codes, chi_join = "NULL", "NULL", ""
        if self._has_rcm("chi"):
            chi_col, chi_codes = "chi.status", "chi.CheckCode"
            chi_join = f"LEFT JOIN {self._rcm('chi')} chi ON chi.vn=o.vn AND chi.type='SSOP'"
        rep_col = "NULL"
        if self._has_rcm("repeclaim"):
            rep_col = (
                f"(SELECT GROUP_CONCAT(DISTINCT rep) FROM {self._rcm('repeclaim')} r "
                "WHERE r.vn=o.vn AND IFNULL(r.errorcode,'')='')"
            )
        rep_file_col = "NULL"
        if self._has_rcm("repdata"):
            rep_file_col = (f"(SELECT r.Rep FROM {self._rcm('repdata')} r WHERE r.VN=o.vn "
                            "ORDER BY r.SendDate DESC, r.Period DESC LIMIT 1)")
        hi_col = "EXISTS(SELECT 1 FROM hi_register h WHERE h.vn=o.vn)" if self._has("hi_register") else "0"
        sql = f"""
        SELECT o.vn, o.hn, o.an, o.pttype, o.spclty, o.vstdate, o.vsttime, o.cur_dep_time,
          IF(pt.nationality=99, pt.cid,
             (SELECT MAX(pc.cardno) FROM ptcardno pc WHERE pc.hn=pt.hn AND pc.cardtype='02')) AS cid,
          CONCAT(IFNULL(pt.pname,''), IFNULL(pt.fname,''), ' ', IFNULL(pt.lname,'')) AS ptname,
          pt.birthday, cc.export_code AS typein_code, cc.name AS typein_name,
          ost.export_code AS typeout_code, ost.name AS typeout_name,
          IFNULL(sp.nhso_code,'') AS clinic, sp.name AS clinic_name,
          v.pdx, nhso.code AS nhso_code,
          ptt.name AS pttype_name, ptt.hipdata_code, {inscl_col} AS maininscl,
          ptt.pttype_eclaim_id, {upp_col} AS upp_code,
          {hospmain} AS hospmain, v.pttype_begin AS datein, v.pttype_expire AS dateexp,
          {doctor} AS doctor, v.income, v.rcpt_money,
          v.debt_id_list AS debt_id, v.rcpno_list AS rcpno,
          {chi_col} AS chi_status, {chi_codes} AS chi_codes, {rep_col} AS rep, {rep_file_col} AS rcm_rep_file, {hi_col} AS hi_registered,
          (SELECT MAX(a.nextdate) FROM oapp a
            WHERE a.vn=o.vn AND IFNULL(a.oapp_status_id,0) NOT IN (4)) AS appoint_date,
          (SELECT MAX(TIMESTAMP(pd.outdate, pd.outtime)) FROM ptdepart pd
            WHERE pd.vn=o.vn AND pd.outdate<=DATE_ADD(o.vstdate, INTERVAL 1 DAY)) AS depart_out
        FROM ovst o
        LEFT JOIN patient pt ON pt.hn=o.hn
        LEFT JOIN vn_stat v ON v.vn=o.vn
        LEFT JOIN pttype ptt ON ptt.pttype=o.pttype
        {upp_join}
        LEFT JOIN spclty sp ON sp.spclty=o.spclty
        LEFT JOIN icd10_nhso nhso ON nhso.code=v.pdx
        LEFT JOIN ovstist cc ON cc.ovstist=o.ovstist
        LEFT JOIN ovstost ost ON ost.ovstost=o.ovstost
        LEFT JOIN doctor dr ON dr.code=o.doctor
        {chi_join}
        WHERE {where}
        ORDER BY o.vstdate, o.vsttime, o.vn
        LIMIT 20000
        """
        return self._select(sql, params)

    # ------------------------------------------------------------------ items
    def fetch_items(self, vns: Sequence[str]) -> list[dict]:
        if not vns:
            return []
        marks = ",".join(["%s"] * len(vns))
        # EXISTS and scalar subqueries: a join on a non-unique table would duplicate
        # item rows and inflate the claim amount.
        noexport_col = "0"
        if self._has("opitemrece_noexport"):
            noexport_col = (
                "EXISTS(SELECT 1 FROM opitemrece_noexport nn WHERE nn.seq=oo.vn AND nn.icode=oo.icode)"
            )
        tmlt_col, tmlt_chi = "NULL", "NULL"
        if self._has_rcm("labitem"):
            lab = self._rcm("labitem")
            pick = f"FROM {lab} lab WHERE lab.icode=oo.icode ORDER BY lab.chi='1' DESC LIMIT 1"
            tmlt_col, tmlt_chi = f"(SELECT lab.tmlt_code {pick})", f"(SELECT lab.chi {pick})"
        ssocac_col = "0"
        if self._has_rcm("claimcat"):
            ssocac_col = (
                f"EXISTS(SELECT 1 FROM {self._rcm('claimcat')} ccc WHERE ccc.icode=oo.icode "
                "AND ccc.maininscl='SSS' AND oo.rxdate BETWEEN ccc.start_date AND ccc.finish_date "
                "AND ccc.remark REGEXP 'SSOCAC')"
            )
        # Some HOSxP versions do not have this column; the amount is then zero.
        over_col = "IFNULL(oo.over_debt,0)" if ("opitemrece", "over_debt") in self.schema()["columns"] else "0"
        sql = f"""
        SELECT oo.vn, oo.hn, oo.icode, oo.qty, oo.unitprice, oo.sum_price, oo.paidst,
          {over_col} AS over_debt, oo.rxdate, oo.rxtime,
          oo.drugusage AS usage_code, oo.sp_use AS sp_use_code,
          d.name, d.strength, d.nhso_adp_code, d.nhso_adp_type_id, d.sks_drug_code,
          d.tpu_code_list, d.sks_product_category_id, d.drugcategory, d.sks_dfs_code,
          d.units, nd.unit AS nd_unit, d.icd9cm, d.drugaccount,
          g.cscd_group, inc.drg_chrgitem_id,
          CONCAT_WS(' ', u.name1, u.name2, u.name3) AS usage_text,
          CONCAT_WS(' ', sp.name1, sp.name2, sp.name3) AS sp_use_text,
          (SELECT LEFT(GROUP_CONCAT(DISTINCT n.presc_reason),2) FROM ovst_presc_ned n
            WHERE n.vn=oo.vn AND n.icode=oo.icode) AS ned,
          {tmlt_col} AS tmlt_code, {tmlt_chi} AS lab_chi,
          {ssocac_col} AS ssocac_item, {noexport_col} AS hosxp_noexport
        FROM opitemrece oo
        LEFT JOIN s_drugitems d ON d.icode=oo.icode
        LEFT JOIN nondrugitems nd ON nd.icode=oo.icode
        LEFT JOIN income inc ON inc.income=oo.income
        LEFT JOIN income_report2 g ON g.group_id=inc.group2
        LEFT JOIN drugusage u ON u.drugusage=oo.drugusage
        LEFT JOIN sp_use sp ON sp.sp_use=oo.sp_use
        WHERE oo.vn IN ({marks})
        ORDER BY oo.vn, g.cscd_group, oo.icode
        """
        return self._select(sql, list(vns))

    # -------------------------------------------------------------- diagnoses
    def fetch_dx(self, vns: Sequence[str]) -> list[dict]:
        if not vns:
            return []
        marks = ",".join(["%s"] * len(vns))
        name = "icdname(dx.icd10)" if self._fn("icdname") else "aa.name"
        tm_col, tm_join = "NULL", ""
        if self._has("ovst_sks_icd10tm"):
            tm_col, tm_join = "tm.code", "LEFT JOIN ovst_sks_icd10tm tm ON tm.code=dx.icd10"
        sql = f"""
        SELECT dx.vn, dx.diagtype, dx.icd10 AS code, {name} AS name,
          aa.codeset, {tm_col} AS tm_code
        FROM ovstdiag dx
        LEFT JOIN icd101 aa ON aa.code=dx.icd10
        {tm_join}
        WHERE dx.vn IN ({marks}) AND dx.icd10 NOT REGEXP '^[0-9]'
        ORDER BY dx.vn, dx.diagtype, dx.icd10
        """
        return self._select(sql, list(vns))

    # ------------------------------------------------------------------ login
    def login_user(self, loginname: str) -> dict | None:
        """The HOSxP account for a login name. Read only; the password hash never leaves the server."""
        rows = self._select(
            "SELECT loginname, name, passweb, groupname, account_disable, password_expire_date "
            "FROM opduser WHERE loginname=%s LIMIT 2", (loginname,),
        )
        return rows[0] if len(rows) == 1 else None

    def close(self) -> None:
        with self._all_lock:
            conns, self._all = list(self._all), set()
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        self._local = threading.local()
