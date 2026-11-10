"""Keep the Krungthai Approve Codes in the program's SQLite: one row per transaction (Approve Code + Trans Ref ID).

Importing a file again, or a later file that repeats a transaction, updates the same row. The visit is not stored:
it is worked out from HOSxP each time, so a corrected right or a new visit is picked up.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .approve import ApproveFile
from .store import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS approve_uploads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, name TEXT NOT NULL, sha256 TEXT NOT NULL,
  file_date TEXT NOT NULL DEFAULT '', row_count INTEGER NOT NULL, total TEXT NOT NULL,
  saved_as TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS approve_codes (
  appr_code TEXT NOT NULL, trans_ref TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL, cid TEXT NOT NULL,
  txn_date TEXT NOT NULL, txn_time TEXT NOT NULL DEFAULT '', post_date TEXT NOT NULL DEFAULT '',
  post_time TEXT NOT NULL DEFAULT '', amount TEXT NOT NULL, channel TEXT NOT NULL DEFAULT '',
  terminal TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', appr_old TEXT NOT NULL DEFAULT '',
  upload_id INTEGER, updated_at TEXT NOT NULL,
  PRIMARY KEY (appr_code, trans_ref)
);
CREATE INDEX IF NOT EXISTS approve_codes_cid ON approve_codes(cid, txn_date);
CREATE INDEX IF NOT EXISTS approve_codes_date ON approve_codes(txn_date);
"""
COLUMNS = ("kind", "cid", "txn_date", "txn_time", "post_date", "post_time", "amount", "channel", "terminal", "reason",
           "appr_old")


class ApproveStore:
    def __init__(self, store: Store):
        self._db = store._db
        with self._db() as db:
            db.executescript(SCHEMA)

    def seen(self, sha256: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT at, name FROM approve_uploads WHERE sha256=? ORDER BY id DESC", (sha256,)).fetchone()
        return dict(row) if row else None

    def save(self, parsed: ApproveFile, name: str, sha256: str, saved_as: str, created_by: str = "",
             now: datetime | None = None) -> dict:
        at = (now or datetime.now()).isoformat(timespec="seconds")
        added = updated = 0
        with self._db() as db:
            upload = db.execute(
                "INSERT INTO approve_uploads (at, name, sha256, file_date, row_count, total, saved_as, created_by)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (at, name, sha256, parsed.file_date, len(parsed.rows), parsed.total, saved_as, created_by)).lastrowid
            for row in parsed.rows:
                values = [getattr(row, c) for c in COLUMNS]
                changed = db.execute(
                    f"UPDATE approve_codes SET {', '.join(f'{c}=?' for c in COLUMNS)}, upload_id=?, updated_at=? "
                    "WHERE appr_code=? AND trans_ref=?", (*values, upload, at, row.appr_code, row.trans_ref)).rowcount
                if changed:
                    updated += 1
                    continue
                db.execute(
                    f"INSERT INTO approve_codes (appr_code, trans_ref, {', '.join(COLUMNS)}, upload_id, updated_at) "
                    f"VALUES ({','.join('?' * (len(COLUMNS) + 4))})", (row.appr_code, row.trans_ref, *values, upload, at))
                added += 1
        return {"added": added, "updated": updated, "upload_id": upload}

    def rows(self, start: str = "", end: str = "") -> list[dict]:
        sql, params = "SELECT * FROM approve_codes WHERE 1=1", []
        if start:
            sql, params = sql + " AND txn_date>=?", [*params, start]
        if end:
            sql, params = sql + " AND txn_date<=?", [*params, end]
        with self._db() as db:
            return [dict(r) for r in db.execute(sql + " ORDER BY txn_date DESC, txn_time DESC", params)]

    def of_cids(self, cids: Iterable[str]) -> list[dict]:
        """Every transaction of these patients, refunds included (a refund may come in a later file)."""
        cids = sorted({c for c in cids if c})
        out: list[dict] = []
        with self._db() as db:
            for at in range(0, len(cids), 500):
                part = cids[at:at + 500]
                out += [dict(r) for r in db.execute(
                    f"SELECT * FROM approve_codes WHERE cid IN ({','.join('?' * len(part))})", part)]
        return out

    def file_dates(self) -> set[str]:
        """The days whose bank file has been imported (the file of a day holds the transactions posted that day)."""
        with self._db() as db:
            return {r[0] for r in db.execute("SELECT DISTINCT file_date FROM approve_uploads WHERE file_date<>''")}

    def uploads(self, limit: int = 30) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, at, name, file_date, row_count, total, created_by FROM approve_uploads ORDER BY id DESC LIMIT ?",
                (limit,))]
