"""Keep imported REP and STM rows in the program's SQLite; HOSxP is never written to.

Importing the same REP number or STM document again replaces its rows, so a file can be imported twice safely.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Iterable

from .repstm import Parsed
from .store import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS repstm_uploads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, name TEXT NOT NULL, sha256 TEXT NOT NULL,
  saved_as TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rep_files (
  rep_no TEXT PRIMARY KEY, hcode TEXT NOT NULL, account TEXT NOT NULL, session TEXT NOT NULL, station TEXT NOT NULL,
  issued_at TEXT NOT NULL, sent INTEGER NOT NULL, passed INTEGER NOT NULL, failed INTEGER NOT NULL,
  has_bil INTEGER NOT NULL, has_dis INTEGER NOT NULL, upload_id INTEGER, imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rep_bills (
  rep_no TEXT NOT NULL, line INTEGER NOT NULL, stat TEXT NOT NULL, station TEXT NOT NULL, hcode TEXT NOT NULL,
  hmain TEXT NOT NULL, authcode TEXT NOT NULL, dttran TEXT NOT NULL, invno TEXT NOT NULL, bp TEXT NOT NULL,
  amount TEXT NOT NULL, claimamt TEXT NOT NULL, codes TEXT NOT NULL, PRIMARY KEY (rep_no, line)
);
CREATE INDEX IF NOT EXISTS rep_bills_invno ON rep_bills(invno);
CREATE TABLE IF NOT EXISTS rep_disps (
  rep_no TEXT NOT NULL, line INTEGER NOT NULL, codes TEXT NOT NULL, invno TEXT NOT NULL, dispid TEXT NOT NULL,
  item_count TEXT NOT NULL, charge TEXT NOT NULL, claim TEXT NOT NULL, paid TEXT NOT NULL, other TEXT NOT NULL,
  PRIMARY KEY (rep_no, dispid)
);
CREATE INDEX IF NOT EXISTS rep_disps_invno ON rep_disps(invno);
CREATE TABLE IF NOT EXISTS rep_drugs (
  rep_no TEXT NOT NULL, dispid TEXT NOT NULL, seq INTEGER NOT NULL, codes TEXT NOT NULL, hospdrgid TEXT NOT NULL,
  prdcat TEXT NOT NULL, name TEXT NOT NULL, qty TEXT NOT NULL, unitprice TEXT NOT NULL, chargeamt TEXT NOT NULL,
  reimbamt TEXT NOT NULL, drgid TEXT NOT NULL, claimcont TEXT NOT NULL, PRIMARY KEY (rep_no, dispid, seq)
);
CREATE TABLE IF NOT EXISTS rep_codes (
  code TEXT PRIMARY KEY, name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stm_files (
  stm_no TEXT PRIMARY KEY, hcode TEXT NOT NULL, account TEXT NOT NULL, period TEXT NOT NULL,
  date_start TEXT NOT NULL, date_end TEXT NOT NULL, date_due TEXT NOT NULL, date_issue TEXT NOT NULL,
  count INTEGER NOT NULL, amount TEXT NOT NULL, upload_id INTEGER, imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stm_bills (
  stm_no TEXT NOT NULL, seq INTEGER NOT NULL, station TEXT NOT NULL, hmain TEXT NOT NULL, hproc TEXT NOT NULL,
  hcare TEXT NOT NULL, hn TEXT NOT NULL, invno TEXT NOT NULL, bf TEXT NOT NULL, pcode TEXT NOT NULL, care TEXT NOT NULL,
  payplan TEXT NOT NULL, bp TEXT NOT NULL, dttran TEXT NOT NULL, copay TEXT NOT NULL, cfh TEXT NOT NULL,
  total TEXT NOT NULL, extp TEXT NOT NULL, rid TEXT NOT NULL, cstat TEXT NOT NULL, PRIMARY KEY (stm_no, station, seq)
);
CREATE INDEX IF NOT EXISTS stm_bills_invno ON stm_bills(invno);
"""

REP_FILE_COLS = ("rep_no", "hcode", "account", "session", "station", "issued_at", "sent", "passed", "failed",
                 "has_bil", "has_dis")
STM_FILE_COLS = ("stm_no", "hcode", "account", "period", "date_start", "date_end", "date_due", "date_issue",
                 "count", "amount")


# A C line with only "already have this ID" rejections (Dispense ID / SvID / InvNo sent before) after the VN was
# accepted means the resend was refused as a duplicate: the earlier A still stands.
DUPLICATE_CODES = {"R01", "S01", "T02"}


def is_duplicate_of_accepted(stat: str, codes: str, accepted_before: bool) -> bool:
    found = {c.strip().upper() for c in (codes or "").split(",") if c.strip()}
    return accepted_before and stat != "A" and bool(found & DUPLICATE_CODES)


def _chunks(values: list[str], size: int = 500):
    for start in range(0, len(values), size):
        yield values[start:start + size]


class RepStmStore:
    def __init__(self, store: Store):
        self._db = store._db
        with self._db() as db:
            self._migrate(db)
            db.executescript(SCHEMA)

    @staticmethod
    def _migrate(db) -> None:
        # SEQ in an STM restarts for each station (01, 09, …): the old key (stm_no, seq) refused such files.
        old = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='stm_bills'").fetchone()
        if old and "PRIMARY KEY (stm_no, seq)" in old[0]:
            db.execute("ALTER TABLE stm_bills RENAME TO stm_bills_old")
            db.execute("DROP INDEX IF EXISTS stm_bills_invno")
            db.executescript(SCHEMA)
            db.execute("INSERT INTO stm_bills SELECT * FROM stm_bills_old")
            db.execute("DROP TABLE stm_bills_old")

    def seen(self, sha256: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT id, at, name FROM repstm_uploads WHERE sha256=? ORDER BY id DESC", (sha256,)).fetchone()
        return dict(row) if row else None

    def save(self, parsed: Parsed, name: str, sha256: str, saved_as: str, summary: str, created_by: str = "",
             now: datetime | None = None) -> int:
        at = (now or datetime.now()).isoformat(timespec="seconds")
        with self._db() as db:
            upload_id = db.execute(
                "INSERT INTO repstm_uploads (at, name, sha256, saved_as, summary, created_by) VALUES (?,?,?,?,?,?)",
                (at, name, sha256, saved_as, summary, created_by)).lastrowid
            for rep in parsed.reps.values():
                self._save_rep(db, rep, upload_id, at)
            for stm in parsed.stms.values():
                db.execute("DELETE FROM stm_bills WHERE stm_no=?", (stm.stm_no,))
                db.execute(
                    f"INSERT OR REPLACE INTO stm_files ({','.join(STM_FILE_COLS)}, upload_id, imported_at) "
                    f"VALUES ({','.join('?' * (len(STM_FILE_COLS) + 2))})",
                    (*(getattr(stm, c) for c in STM_FILE_COLS), upload_id, at))
                db.executemany(
                    "INSERT INTO stm_bills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(stm.stm_no, *asdict(b).values()) for b in stm.bills])
        return upload_id

    @staticmethod
    def _save_rep(db, rep, upload_id: int, at: str) -> None:
        old = db.execute("SELECT * FROM rep_files WHERE rep_no=?", (rep.rep_no,)).fetchone()
        # BIL and DIS of one REP number may arrive in separate uploads: keep the part this upload lacks.
        values = {c: getattr(rep, c) for c in REP_FILE_COLS}
        if old is not None:
            for column in ("session", "station", "issued_at", "hcode", "account"):
                values[column] = values[column] or old[column]
            if not rep.has_bil:
                values.update(sent=old["sent"], passed=old["passed"], failed=old["failed"], has_bil=old["has_bil"])
            if not rep.has_dis:
                values["has_dis"] = old["has_dis"]
        values["has_bil"], values["has_dis"] = int(bool(values["has_bil"])), int(bool(values["has_dis"]))
        db.execute(
            f"INSERT OR REPLACE INTO rep_files ({','.join(REP_FILE_COLS)}, upload_id, imported_at) "
            f"VALUES ({','.join('?' * (len(REP_FILE_COLS) + 2))})",
            (*values.values(), upload_id, at))
        if rep.has_bil:
            db.execute("DELETE FROM rep_bills WHERE rep_no=?", (rep.rep_no,))
            db.executemany("INSERT INTO rep_bills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           [(rep.rep_no, *asdict(b).values()) for b in rep.bills])
        if rep.has_dis:
            db.execute("DELETE FROM rep_disps WHERE rep_no=?", (rep.rep_no,))
            db.execute("DELETE FROM rep_drugs WHERE rep_no=?", (rep.rep_no,))
            for disp in rep.disps:
                db.execute("INSERT OR REPLACE INTO rep_disps VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (rep.rep_no, disp.line, disp.codes, disp.invno, disp.dispid, disp.item_count, disp.charge,
                            disp.claim, disp.paid, disp.other))
                db.executemany("INSERT OR REPLACE INTO rep_drugs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               [(rep.rep_no, disp.dispid, i, *asdict(d).values()) for i, d in enumerate(disp.drugs, 1)])
        db.executemany("INSERT OR REPLACE INTO rep_codes (code, name) VALUES (?,?)", list(rep.code_names.items()))

    # ------------------------------------------------------------- reading
    def reps(self, limit: int = 200) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                """SELECT f.*, (SELECT COUNT(*) FROM rep_bills b WHERE b.rep_no=f.rep_no) AS lines,
                (SELECT IFNULL(SUM(CAST(b.claimamt AS REAL)),0) FROM rep_bills b WHERE b.rep_no=f.rep_no AND b.stat='A') AS passed_amount,
                (SELECT IFNULL(SUM(CAST(b.claimamt AS REAL)),0) FROM rep_bills b WHERE b.rep_no=f.rep_no AND b.stat<>'A') AS failed_amount
                FROM rep_files f ORDER BY f.issued_at DESC, f.rep_no DESC LIMIT ?""", (limit,))]

    def stms(self, limit: int = 200) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM stm_files ORDER BY period DESC, stm_no DESC LIMIT ?", (limit,))]

    def uploads(self, limit: int = 30) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, at, name, summary, created_by FROM repstm_uploads ORDER BY id DESC LIMIT ?", (limit,))]

    def rep_detail(self, rep_no: str) -> dict | None:
        with self._db() as db:
            head = db.execute("SELECT * FROM rep_files WHERE rep_no=?", (rep_no,)).fetchone()
            if head is None:
                return None
            bills = [dict(r) for r in db.execute("SELECT * FROM rep_bills WHERE rep_no=? ORDER BY line", (rep_no,))]
            disps = [dict(r) for r in db.execute("SELECT * FROM rep_disps WHERE rep_no=? ORDER BY line", (rep_no,))]
            drugs = [dict(r) for r in db.execute("SELECT * FROM rep_drugs WHERE rep_no=? ORDER BY dispid, seq", (rep_no,))]
        for disp in disps:
            disp["drugs"] = [d for d in drugs if d["dispid"] == disp["dispid"]]
        return {**dict(head), "bills": bills, "disps": disps}

    def stm_detail(self, stm_no: str) -> dict | None:
        with self._db() as db:
            head = db.execute("SELECT * FROM stm_files WHERE stm_no=?", (stm_no,)).fetchone()
            if head is None:
                return None
            bills = [dict(r) for r in db.execute("SELECT * FROM stm_bills WHERE stm_no=? ORDER BY station, seq", (stm_no,))]
        return {**dict(head), "bills": bills}

    def vn_history(self, vn: str) -> dict:
        with self._db() as db:
            reps = [dict(r) for r in db.execute(
                """SELECT b.*, f.issued_at, f.session FROM rep_bills b JOIN rep_files f ON f.rep_no=b.rep_no
                WHERE b.invno=? ORDER BY f.issued_at, b.rep_no""", (vn,))]
            disps = [dict(r) for r in db.execute("SELECT * FROM rep_disps WHERE invno=? ORDER BY rep_no", (vn,))]
            drugs = [dict(r) for r in db.execute(
                "SELECT d.* FROM rep_drugs d JOIN rep_disps p ON p.rep_no=d.rep_no AND p.dispid=d.dispid "
                "WHERE p.invno=? ORDER BY d.rep_no, d.seq", (vn,))]
            stms = [dict(r) for r in db.execute(
                """SELECT b.*, f.period, f.date_issue, f.date_due FROM stm_bills b JOIN stm_files f ON f.stm_no=b.stm_no
                WHERE b.invno=? ORDER BY f.period""", (vn,))]
        for disp in disps:
            disp["drugs"] = [d for d in drugs if d["rep_no"] == disp["rep_no"] and d["dispid"] == disp["dispid"]]
        accepted, status = False, None
        for rep in reps:
            rep["duplicate"] = is_duplicate_of_accepted(rep["stat"], rep["codes"], accepted)
            if not rep["duplicate"]:
                accepted = accepted or rep["stat"] == "A"
                status = rep
        return {"vn": vn, "reps": reps, "disps": disps, "stms": stms,
                "status_rep": status["rep_no"] if status else "", "status_stat": status["stat"] if status else ""}

    def code_names(self) -> dict[str, str]:
        with self._db() as db:
            return {r["code"]: r["name"] for r in db.execute("SELECT code, name FROM rep_codes")}

    def latest(self, vns: Iterable[str]) -> dict[str, dict]:
        """Per VN: the newest REP line (stat, codes, rep_no) and the STM documents that paid it."""
        vns = [v for v in dict.fromkeys(vns) if v]
        found: dict[str, dict] = {}
        with self._db() as db:
            for part in _chunks(vns):
                marks = ",".join("?" * len(part))
                for row in db.execute(
                    f"""SELECT b.invno, b.stat, b.codes, b.rep_no, f.issued_at, f.hcode, f.account FROM rep_bills b
                    JOIN rep_files f ON f.rep_no=b.rep_no WHERE b.invno IN ({marks})
                    ORDER BY f.issued_at, b.rep_no, b.line""", part):
                    mine = found.setdefault(row["invno"], {})
                    if is_duplicate_of_accepted(row["stat"], row["codes"], mine.get("accepted", False)):
                        mine["duplicate_rep"] = row["rep_no"]
                        continue
                    mine["accepted"] = mine.get("accepted", False) or row["stat"] == "A"
                    mine.update(
                        rep_stat=row["stat"], rep_codes=row["codes"], rep_no=row["rep_no"], rep_at=row["issued_at"],
                        rep_file=f"{row['hcode']}_{row['account']}BIL_{row['rep_no']}")
                for row in db.execute(
                    f"""SELECT b.invno, GROUP_CONCAT(b.stm_no) AS stm_no, SUM(CAST(b.total AS REAL)) AS paid
                    FROM stm_bills b WHERE b.invno IN ({marks}) GROUP BY b.invno""", part):
                    found.setdefault(row["invno"], {}).update(stm_no=row["stm_no"], stm_paid=f"{row['paid']:.2f}")
        return found
