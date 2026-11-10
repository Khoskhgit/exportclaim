"""Keep the dental reports in the program's SQLite: one row per claim.

A claim is CID + service date + service type + claimed amount + seq (the nth such claim in a report), because one
person can have several claims on one day and the reports carry no claim number.

The submission report fills the claim and its status; the payment report fills what was paid. Either may come first,
and importing an overlapping period again updates the same rows.
"""
from __future__ import annotations

from datetime import datetime

from .dental import PAYMENT, DentalReport
from .store import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS dental_uploads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL, sha256 TEXT NOT NULL,
  period_start TEXT NOT NULL, period_end TEXT NOT NULL, row_count INTEGER NOT NULL, total TEXT NOT NULL,
  saved_as TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS dental_claims (
  cid TEXT NOT NULL, service_date TEXT NOT NULL, service_type TEXT NOT NULL, claim_amount TEXT NOT NULL,
  seq INTEGER NOT NULL DEFAULT 1, claim_date TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
  paid_amount TEXT NOT NULL DEFAULT '', voucher TEXT NOT NULL DEFAULT '', transfer_date TEXT NOT NULL DEFAULT '',
  hn TEXT NOT NULL DEFAULT '', vn TEXT NOT NULL DEFAULT '', matched_at TEXT NOT NULL DEFAULT '',
  submission_upload INTEGER, payment_upload INTEGER, updated_at TEXT NOT NULL,
  PRIMARY KEY (cid, service_date, service_type, claim_amount, seq)
);
CREATE INDEX IF NOT EXISTS dental_claims_date ON dental_claims(service_date);
CREATE INDEX IF NOT EXISTS dental_claims_vn ON dental_claims(vn);
"""


KEY_WHERE = "cid=? AND service_date=? AND service_type=? AND claim_amount=? AND seq=?"


class DentalStore:
    def __init__(self, store: Store):
        self._db = store._db
        with self._db() as db:
            db.executescript(SCHEMA)

    def seen(self, sha256: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT at, name FROM dental_uploads WHERE sha256=? ORDER BY id DESC", (sha256,)).fetchone()
        return dict(row) if row else None

    def save(self, report: DentalReport, name: str, sha256: str, saved_as: str, created_by: str = "",
             now: datetime | None = None) -> dict:
        at = (now or datetime.now()).isoformat(timespec="seconds")
        added = updated = 0
        with self._db() as db:
            upload = db.execute(
                "INSERT INTO dental_uploads (at, name, kind, sha256, period_start, period_end, row_count, total, saved_as,"
                " created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (at, name, report.kind, sha256, report.period_start, report.period_end, len(report.rows), report.total,
                 saved_as, created_by)).lastrowid
            for row in report.rows:
                key = (row.cid, row.service_date, row.service_type, row.claim_amount, row.seq)
                exists = db.execute(f"SELECT 1 FROM dental_claims WHERE {KEY_WHERE}", key).fetchone()
                if not exists:
                    db.execute("INSERT INTO dental_claims (cid, service_date, service_type, claim_amount, seq, updated_at)"
                               " VALUES (?,?,?,?,?,?)", (*key, at))
                    added += 1
                else:
                    updated += 1
                common = {"claim_date": row.claim_date}
                if report.kind == PAYMENT:
                    values = {**common, "paid_amount": row.paid_amount, "voucher": row.voucher,
                              "transfer_date": row.transfer_date, "payment_upload": upload}
                else:
                    values = {**common, "status": row.status, "submission_upload": upload}
                values = {k: v for k, v in values.items() if v not in ("", None)}
                db.execute(f"UPDATE dental_claims SET {', '.join(f'{k}=?' for k in values)}, updated_at=? "
                           f"WHERE {KEY_WHERE}", (*values.values(), at, *key))
        return {"added": added, "updated": updated, "upload_id": upload}

    def claims(self, start: str = "", end: str = "") -> list[dict]:
        sql, params = "SELECT * FROM dental_claims WHERE 1=1", []
        if start:
            sql, params = sql + " AND service_date>=?", [*params, start]
        if end:
            sql, params = sql + " AND service_date<=?", [*params, end]
        with self._db() as db:
            return [dict(r) for r in db.execute(sql + " ORDER BY service_date DESC, cid, seq", params)]

    def set_match(self, matches: dict[tuple, tuple[str, str]], now: datetime | None = None) -> None:
        """matches: (cid, service_date, service_type, claim_amount, seq) -> (hn, vn)."""
        at = (now or datetime.now()).isoformat(timespec="seconds")
        with self._db() as db:
            db.executemany(f"UPDATE dental_claims SET hn=?, vn=?, matched_at=? WHERE {KEY_WHERE}",
                           [(hn, vn, at, *key) for key, (hn, vn) in matches.items()])

    def uploads(self, limit: int = 30) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, at, name, kind, period_start, period_end, row_count, total, created_by FROM dental_uploads "
                "ORDER BY id DESC LIMIT ?", (limit,))]
