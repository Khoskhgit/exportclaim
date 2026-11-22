"""Keep the SSO disability reports in the program's SQLite: one row per claim.

A claim is CID + admit date + OPD/IPD + claimed amount + seq (the nth such claim in a report), as the reports carry
no claim number. The submission report fills the claim and its status, a payment report what was paid; either may
come first, and importing an overlapping period again updates the same rows.
"""
from __future__ import annotations

from datetime import datetime

from .disabled import PAYMENT, DisabledReport
from .store import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS disabled_uploads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL, sha256 TEXT NOT NULL,
  period_start TEXT NOT NULL, period_end TEXT NOT NULL, row_count INTEGER NOT NULL, total TEXT NOT NULL,
  saved_as TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS disabled_claims (
  cid TEXT NOT NULL, admit_date TEXT NOT NULL, kind TEXT NOT NULL, claim_amount TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 1,
  discharge_date TEXT NOT NULL DEFAULT '', claim_date TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
  paid_amount TEXT NOT NULL DEFAULT '', voucher TEXT NOT NULL DEFAULT '', transfer_date TEXT NOT NULL DEFAULT '',
  hn TEXT NOT NULL DEFAULT '', vn TEXT NOT NULL DEFAULT '', an TEXT NOT NULL DEFAULT '', matched_at TEXT NOT NULL DEFAULT '',
  submission_upload INTEGER, payment_upload INTEGER, updated_at TEXT NOT NULL,
  PRIMARY KEY (cid, admit_date, kind, claim_amount, seq)
);
CREATE INDEX IF NOT EXISTS disabled_claims_date ON disabled_claims(admit_date);
"""

KEY_WHERE = "cid=? AND admit_date=? AND kind=? AND claim_amount=? AND seq=?"


class DisabledStore:
    def __init__(self, store: Store):
        self._db = store._db
        with self._db() as db:
            db.executescript(SCHEMA)

    def seen(self, sha256: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT at, name FROM disabled_uploads WHERE sha256=? ORDER BY id DESC", (sha256,)).fetchone()
        return dict(row) if row else None

    def save(self, report: DisabledReport, name: str, sha256: str, saved_as: str, created_by: str = "",
             now: datetime | None = None) -> dict:
        at = (now or datetime.now()).isoformat(timespec="seconds")
        added = updated = 0
        with self._db() as db:
            upload = db.execute(
                "INSERT INTO disabled_uploads (at, name, kind, sha256, period_start, period_end, row_count, total, saved_as,"
                " created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (at, name, report.kind, sha256, report.period_start, report.period_end, len(report.rows), report.total,
                 saved_as, created_by)).lastrowid
            for row in report.rows:
                key = (row.cid, row.admit_date, row.kind, row.claim_amount, row.seq)
                if db.execute(f"SELECT 1 FROM disabled_claims WHERE {KEY_WHERE}", key).fetchone():
                    updated += 1
                else:
                    db.execute("INSERT INTO disabled_claims (cid, admit_date, kind, claim_amount, seq, updated_at)"
                               " VALUES (?,?,?,?,?,?)", (*key, at))
                    added += 1
                common = {"discharge_date": row.discharge_date, "claim_date": row.claim_date}
                if report.kind == PAYMENT:
                    values = {**common, "paid_amount": row.paid_amount, "voucher": row.voucher,
                              "transfer_date": row.transfer_date, "payment_upload": upload}
                else:
                    values = {**common, "status": row.status, "submission_upload": upload}
                values = {k: v for k, v in values.items() if v not in ("", None)}
                db.execute(f"UPDATE disabled_claims SET {', '.join(f'{k}=?' for k in values)}, updated_at=? "
                           f"WHERE {KEY_WHERE}", (*values.values(), at, *key))
        return {"added": added, "updated": updated, "upload_id": upload}

    def claims(self, start: str = "", end: str = "") -> list[dict]:
        sql, params = "SELECT * FROM disabled_claims WHERE 1=1", []
        if start:
            sql, params = sql + " AND admit_date>=?", [*params, start]
        if end:
            sql, params = sql + " AND admit_date<=?", [*params, end]
        with self._db() as db:
            return [dict(r) for r in db.execute(sql + " ORDER BY admit_date DESC, cid, seq", params)]

    def set_match(self, matches: dict[tuple, tuple[str, str, str]], now: datetime | None = None) -> None:
        """matches: (cid, admit_date, kind, claim_amount, seq) -> (hn, vn, an)."""
        at = (now or datetime.now()).isoformat(timespec="seconds")
        with self._db() as db:
            db.executemany(f"UPDATE disabled_claims SET hn=?, vn=?, an=?, matched_at=? WHERE {KEY_WHERE}",
                           [(hn, vn, an, at, *key) for key, (hn, vn, an) in matches.items()])

    def uploads(self, limit: int = 30) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, at, name, kind, period_start, period_end, row_count, total, created_by FROM disabled_uploads "
                "ORDER BY id DESC LIMIT ?", (limit,))]
