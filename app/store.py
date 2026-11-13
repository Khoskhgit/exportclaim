"""The program's own data, kept in SQLite so HOSxP is never written to."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS exports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL, session TEXT NOT NULL, tflag TEXT NOT NULL,
  zip_path TEXT NOT NULL, bill_count INTEGER NOT NULL, claim_total TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS export_visits (
  export_id INTEGER NOT NULL REFERENCES exports(id), vn TEXT NOT NULL,
  tflag TEXT NOT NULL, PRIMARY KEY (export_id, vn)
);
CREATE INDEX IF NOT EXISTS export_visits_vn ON export_visits(vn);
CREATE TABLE IF NOT EXISTS exclusions (
  vn TEXT NOT NULL, icode TEXT NOT NULL, PRIMARY KEY (vn, icode)
);
CREATE TABLE IF NOT EXISTS inclusions (
  vn TEXT NOT NULL, icode TEXT NOT NULL, PRIMARY KEY (vn, icode)
);
CREATE TABLE IF NOT EXISTS aipn_exports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL, session TEXT NOT NULL, appeal TEXT NOT NULL,
  zip_path TEXT NOT NULL, an_count INTEGER NOT NULL, total TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aipn_export_ans (
  export_id INTEGER NOT NULL REFERENCES aipn_exports(id), an TEXT NOT NULL, PRIMARY KEY (export_id, an)
);
CREATE TABLE IF NOT EXISTS aipn_items (
  an TEXT NOT NULL, item TEXT NOT NULL, send INTEGER NOT NULL, PRIMARY KEY (an, item)
);
CREATE TABLE IF NOT EXISTS aipn_prices (
  an TEXT NOT NULL, item TEXT NOT NULL, unitprice TEXT NOT NULL, PRIMARY KEY (an, item)
);
CREATE TABLE IF NOT EXISTS aipn_care_as (
  an TEXT PRIMARY KEY, care_as TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, method TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_states (
  state_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, user_id TEXT NOT NULL,
  method TEXT NOT NULL, ok INTEGER NOT NULL, reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS login_log_user ON login_log(user_id, at);
CREATE TABLE IF NOT EXISTS nhso18_exports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, zip_path TEXT NOT NULL,
  visit_count INTEGER NOT NULL, created_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS nhso18_export_keys (
  export_id INTEGER NOT NULL REFERENCES nhso18_exports(id), seq TEXT NOT NULL, PRIMARY KEY (export_id, seq)
);
CREATE TABLE IF NOT EXISTS nhso18_items (
  seq TEXT NOT NULL, icode TEXT NOT NULL, PRIMARY KEY (seq, icode)
);
CREATE TABLE IF NOT EXISTS nhso18_uuc (
  seq TEXT PRIMARY KEY, uuc TEXT NOT NULL, set_by TEXT NOT NULL DEFAULT '', set_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fdh_sends (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, zone TEXT NOT NULL, mode TEXT NOT NULL,
  visit_count INTEGER NOT NULL, ok INTEGER NOT NULL, http_status INTEGER, message TEXT NOT NULL DEFAULT '',
  folder TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fdh_send_keys (
  send_id INTEGER NOT NULL REFERENCES fdh_sends(id), seq TEXT NOT NULL, PRIMARY KEY (send_id, seq)
);
CREATE TABLE IF NOT EXISTS eclaim_sends (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, maininscl TEXT NOT NULL DEFAULT '',
  import_dup INTEGER NOT NULL DEFAULT 0, visit_count INTEGER NOT NULL, ok INTEGER NOT NULL, http_status INTEGER,
  message TEXT NOT NULL DEFAULT '', folder TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS eclaim_send_keys (
  send_id INTEGER NOT NULL REFERENCES eclaim_sends(id), seq TEXT NOT NULL, PRIMARY KEY (send_id, seq)
);
CREATE TABLE IF NOT EXISTS z_codes (
  code TEXT PRIMARY KEY, description TEXT
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._db() as db:
            db.executescript(SCHEMA)
            # Older databases: record who built each ZIP.
            for table in ("exports", "aipn_exports"):
                columns = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                if "created_by" not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN created_by TEXT NOT NULL DEFAULT ''")

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def sent_vns(self, vns: Iterable[str]) -> set[str]:
        """VNs whose latest local export was not a cancellation."""
        vns = list(vns)
        found: set[str] = set()
        with self._db() as db:
            for start in range(0, len(vns), 500):
                part = vns[start:start + 500]
                marks = ",".join("?" * len(part))
                rows = db.execute(
                    f"""SELECT vn, tflag FROM export_visits WHERE vn IN ({marks})
                    AND export_id=(SELECT MAX(e.export_id) FROM export_visits e WHERE e.vn=export_visits.vn)""",
                    part,
                )
                found |= {r["vn"] for r in rows if r["tflag"] != "D"}
        return found

    def record_export(self, session: str, tflag: str, zip_path: Path, vns: Iterable[str],
                      claim_total, now: datetime | None = None, created_by: str = "") -> int:
        vns = list(vns)
        created = (now or datetime.now()).isoformat(timespec="seconds")
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO exports (created_at, session, tflag, zip_path, bill_count, claim_total, created_by) "
                "VALUES (?,?,?,?,?,?,?)",
                (created, session, tflag, str(zip_path), len(vns), str(claim_total), created_by),
            )
            db.executemany(
                "INSERT INTO export_visits (export_id, vn, tflag) VALUES (?,?,?)",
                [(cur.lastrowid, vn, tflag) for vn in vns],
            )
            return cur.lastrowid

    def history(self, limit: int = 50) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT id, created_at, session, tflag, zip_path, bill_count, claim_total, created_by "
                "FROM exports ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def export_path(self, export_id: int) -> Path | None:
        with self._db() as db:
            row = db.execute("SELECT zip_path FROM exports WHERE id=?", (export_id,)).fetchone()
        return Path(row["zip_path"]) if row else None

    def exclusions(self, vns: Iterable[str] | None = None) -> set[tuple[str, str]]:
        with self._db() as db:
            rows = db.execute("SELECT vn, icode FROM exclusions").fetchall()
        wanted = set(vns) if vns is not None else None
        return {(r["vn"], r["icode"]) for r in rows if wanted is None or r["vn"] in wanted}

    def set_exclusion(self, vn: str, icode: str, excluded: bool) -> None:
        with self._db() as db:
            if excluded:
                db.execute("INSERT OR IGNORE INTO exclusions (vn, icode) VALUES (?,?)", (vn, icode))
            else:
                db.execute("DELETE FROM exclusions WHERE vn=? AND icode=?", (vn, icode))

    def inclusions(self, vns: Iterable[str]) -> set[tuple[str, str]]:
        """Items HOSxP marks as not exported that the user chose to send anyway."""
        wanted = set(vns)
        with self._db() as db:
            rows = db.execute("SELECT vn, icode FROM inclusions").fetchall()
        return {(r["vn"], r["icode"]) for r in rows if r["vn"] in wanted}

    def set_inclusion(self, vn: str, icode: str, included: bool) -> None:
        with self._db() as db:
            if included:
                db.execute("INSERT OR IGNORE INTO inclusions (vn, icode) VALUES (?,?)", (vn, icode))
            else:
                db.execute("DELETE FROM inclusions WHERE vn=? AND icode=?", (vn, icode))

    def z_codes(self) -> set[str]:
        with self._db() as db:
            return {r["code"] for r in db.execute("SELECT code FROM z_codes")}

    def replace_z_codes(self, rows: Iterable[tuple[str, str]]) -> int:
        rows = list(rows)
        with self._db() as db:
            db.execute("DELETE FROM z_codes")
            db.executemany("INSERT OR REPLACE INTO z_codes (code, description) VALUES (?,?)", rows)
        return len(rows)

    # ------------------------------------------------------------------ AIPN
    def aipn_item_choices(self, ans: Iterable[str]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        """Items the user chose not to send, and HOSxP-excluded items the user chose to send, per AN."""
        wanted = set(ans)
        excluded: dict[str, set[str]] = {}
        included: dict[str, set[str]] = {}
        with self._db() as db:
            for r in db.execute("SELECT an, item, send FROM aipn_items"):
                if r["an"] in wanted:
                    (included if r["send"] else excluded).setdefault(r["an"], set()).add(r["item"])
        return excluded, included

    def set_aipn_item(self, an: str, item: str, send: bool | None) -> None:
        """send=None removes the user's choice so HOSxP's own setting applies."""
        with self._db() as db:
            db.execute("DELETE FROM aipn_items WHERE an=? AND item=?", (an, item))
            if send is not None:
                db.execute("INSERT INTO aipn_items (an, item, send) VALUES (?,?,?)", (an, item, int(send)))

    def aipn_prices(self, ans: Iterable[str]) -> dict[str, dict]:
        """Unit prices the user set in this program, per AN and item key."""
        from decimal import Decimal
        wanted = set(ans)
        out: dict[str, dict] = {}
        with self._db() as db:
            for r in db.execute("SELECT an, item, unitprice FROM aipn_prices"):
                if r["an"] in wanted:
                    out.setdefault(r["an"], {})[r["item"]] = Decimal(r["unitprice"])
        return out

    def set_aipn_price(self, an: str, item: str, unitprice) -> None:
        """unitprice=None goes back to the HOSxP price."""
        with self._db() as db:
            db.execute("DELETE FROM aipn_prices WHERE an=? AND item=?", (an, item))
            if unitprice is not None:
                db.execute("INSERT INTO aipn_prices (an, item, unitprice) VALUES (?,?,?)", (an, item, str(unitprice)))

    def aipn_care_as(self, ans: Iterable[str]) -> dict[str, str]:
        wanted = set(ans)
        with self._db() as db:
            return {r["an"]: r["care_as"] for r in db.execute("SELECT an, care_as FROM aipn_care_as") if r["an"] in wanted}

    def set_aipn_care_as(self, an: str, care_as: str | None) -> None:
        with self._db() as db:
            db.execute("DELETE FROM aipn_care_as WHERE an=?", (an,))
            if care_as:
                db.execute("INSERT INTO aipn_care_as (an, care_as) VALUES (?,?)", (an, care_as))

    def aipn_sent(self, ans: Iterable[str]) -> set[str]:
        wanted = set(ans)
        with self._db() as db:
            return {r["an"] for r in db.execute("SELECT DISTINCT an FROM aipn_export_ans") if r["an"] in wanted}

    def record_aipn_export(self, session: str, appeal: str, zip_path: Path, ans: Iterable[str], total,
                           now: datetime | None = None, created_by: str = "") -> int:
        ans = list(ans)
        created = (now or datetime.now()).isoformat(timespec="seconds")
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO aipn_exports (created_at, session, appeal, zip_path, an_count, total, created_by) "
                "VALUES (?,?,?,?,?,?,?)",
                (created, session, appeal, str(zip_path), len(ans), str(total), created_by),
            )
            db.executemany("INSERT INTO aipn_export_ans (export_id, an) VALUES (?,?)", [(cur.lastrowid, a) for a in ans])
            return cur.lastrowid

    def aipn_history(self, limit: int = 50) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT id, created_at, session, appeal, zip_path, an_count, total, created_by FROM aipn_exports ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def aipn_export_path(self, export_id: int) -> Path | None:
        with self._db() as db:
            row = db.execute("SELECT zip_path FROM aipn_exports WHERE id=?", (export_id,)).fetchone()
        return Path(row["zip_path"]) if row else None

    def next_aipn_session(self) -> str:
        with self._db() as db:
            row = db.execute("SELECT MAX(CAST(session AS INTEGER)) m FROM aipn_exports").fetchone()
        return str(row["m"] + 1) if row and row["m"] and row["m"] < 99999 else ""

    # ----------------------------------------------------------------- login
    def create_session(self, token_hash: str, user_id: str, name: str, method: str, hours: int,
                       now: datetime | None = None) -> None:
        from datetime import timedelta
        now = now or datetime.now()
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(timespec="seconds"),))
            db.execute(
                "INSERT INTO sessions (token_hash, user_id, name, method, created_at, expires_at) VALUES (?,?,?,?,?,?)",
                (token_hash, user_id, name, method, now.isoformat(timespec="seconds"),
                 (now + timedelta(hours=hours)).isoformat(timespec="seconds")),
            )

    def session(self, token_hash: str, now: datetime | None = None) -> dict | None:
        now = (now or datetime.now()).isoformat(timespec="seconds")
        with self._db() as db:
            row = db.execute(
                "SELECT user_id, name, method, expires_at FROM sessions WHERE token_hash=? AND expires_at >= ?",
                (token_hash, now),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def save_login_state(self, state_hash: str, now: datetime | None = None) -> None:
        with self._db() as db:
            db.execute("INSERT INTO login_states (state_hash, created_at) VALUES (?,?)",
                       (state_hash, (now or datetime.now()).isoformat(timespec="seconds")))

    def take_login_state(self, state_hash: str, max_minutes: int = 10, now: datetime | None = None) -> bool:
        """A state works once and only for a few minutes."""
        from datetime import timedelta
        now = now or datetime.now()
        oldest = (now - timedelta(minutes=max_minutes)).isoformat(timespec="seconds")
        with self._db() as db:
            row = db.execute("SELECT created_at FROM login_states WHERE state_hash=?", (state_hash,)).fetchone()
            db.execute("DELETE FROM login_states WHERE state_hash=? OR created_at < ?", (state_hash, oldest))
        return bool(row) and row["created_at"] >= oldest

    def log_login(self, user_id: str, method: str, ok: bool, reason: str = "", now: datetime | None = None) -> None:
        with self._db() as db:
            db.execute("INSERT INTO login_log (at, user_id, method, ok, reason) VALUES (?,?,?,?,?)",
                       ((now or datetime.now()).isoformat(timespec="seconds"), user_id, method, int(ok), reason))

    def recent_failures(self, user_id: str, minutes: int = 15, now: datetime | None = None) -> int:
        from datetime import timedelta
        since = ((now or datetime.now()) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        with self._db() as db:
            row = db.execute(
                "SELECT COUNT(*) n FROM login_log WHERE user_id=? AND ok=0 AND at >= ? AND method='hosxp' "
                "AND id > IFNULL((SELECT MAX(id) FROM login_log WHERE user_id=? AND ok=1 AND method='hosxp'), 0)",
                (user_id, since, user_id),
            ).fetchone()
        return int(row["n"])

    # ----------------------------------------------------------- 18 files
    def nhso18_excluded(self, seqs: Iterable[str]) -> set[tuple[str, str]]:
        wanted = set(seqs)
        with self._db() as db:
            return {(r["seq"], r["icode"]) for r in db.execute("SELECT seq, icode FROM nhso18_items") if r["seq"] in wanted}

    def set_nhso18_item(self, seq: str, icode: str, excluded: bool) -> None:
        with self._db() as db:
            db.execute("DELETE FROM nhso18_items WHERE seq=? AND icode=?", (seq, icode))
            if excluded:
                db.execute("INSERT INTO nhso18_items (seq, icode) VALUES (?,?)", (seq, icode))

    def nhso18_uuc(self, seqs: Iterable[str]) -> dict[str, str]:
        """UUC chosen by hand for these visits (the old program's useright toggle), by VN / AN."""
        wanted = set(seqs)
        with self._db() as db:
            return {r["seq"]: r["uuc"] for r in db.execute("SELECT seq, uuc FROM nhso18_uuc") if r["seq"] in wanted}

    def set_nhso18_uuc(self, seq: str, uuc: str | None, set_by: str = "", now: datetime | None = None) -> None:
        """Set UUC 1 or 2 for one visit; None goes back to the program's own rule."""
        with self._db() as db:
            db.execute("DELETE FROM nhso18_uuc WHERE seq=?", (seq,))
            if uuc:
                db.execute("INSERT INTO nhso18_uuc (seq, uuc, set_by, set_at) VALUES (?,?,?,?)",
                           (seq, uuc, set_by, (now or datetime.now()).isoformat(timespec="seconds")))

    def nhso18_sent(self, seqs: Iterable[str]) -> set[str]:
        wanted = set(seqs)
        with self._db() as db:
            return {r["seq"] for r in db.execute("SELECT DISTINCT seq FROM nhso18_export_keys") if r["seq"] in wanted}

    def fdh_sent(self, seqs: Iterable[str]) -> set[str]:
        """Visits sent to production FDH with a success reply (prescreen and UAT do not count)."""
        wanted = set(seqs)
        with self._db() as db:
            return {r["seq"] for r in db.execute(
                "SELECT DISTINCT k.seq FROM fdh_send_keys k JOIN fdh_sends s ON s.id=k.send_id "
                "WHERE s.zone='prod' AND s.mode='send' AND s.ok=1") if r["seq"] in wanted}

    def record_fdh_send(self, seqs: Iterable[str], zone: str, mode: str, ok: bool, http_status: int | None, message: str,
                        folder: str = "", created_by: str = "", now: datetime | None = None) -> int:
        seqs = list(dict.fromkeys(seqs))
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO fdh_sends (created_at, zone, mode, visit_count, ok, http_status, message, folder, created_by) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ((now or datetime.now()).isoformat(timespec="seconds"), zone, mode, len(seqs), int(ok), http_status,
                 message[:1000], folder, created_by))
            db.executemany("INSERT INTO fdh_send_keys (send_id, seq) VALUES (?,?)", [(cur.lastrowid, s) for s in seqs])
            return cur.lastrowid

    def eclaim_sent(self, seqs: Iterable[str]) -> set[str]:
        """Visits sent to New e-Claim with a success reply."""
        wanted = set(seqs)
        with self._db() as db:
            return {r["seq"] for r in db.execute(
                "SELECT DISTINCT k.seq FROM eclaim_send_keys k JOIN eclaim_sends s ON s.id=k.send_id WHERE s.ok=1")
                if r["seq"] in wanted}

    def record_eclaim_send(self, seqs: Iterable[str], maininscl: str, import_dup: bool, ok: bool, http_status: int | None,
                           message: str, folder: str = "", created_by: str = "", now: datetime | None = None) -> int:
        seqs = list(dict.fromkeys(seqs))
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO eclaim_sends (created_at, maininscl, import_dup, visit_count, ok, http_status, message, folder, "
                "created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                ((now or datetime.now()).isoformat(timespec="seconds"), maininscl, int(import_dup), len(seqs), int(ok),
                 http_status, message[:1000], folder, created_by))
            db.executemany("INSERT INTO eclaim_send_keys (send_id, seq) VALUES (?,?)", [(cur.lastrowid, s) for s in seqs])
            return cur.lastrowid

    def eclaim_history(self, limit: int = 50) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, created_at, maininscl, import_dup, visit_count, ok, http_status, message, created_by "
                "FROM eclaim_sends ORDER BY id DESC LIMIT ?", (limit,))]

    def fdh_history(self, limit: int = 50) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, created_at, zone, mode, visit_count, ok, http_status, message, created_by FROM fdh_sends "
                "ORDER BY id DESC LIMIT ?", (limit,))]

    def record_nhso18_export(self, zip_path: Path, seqs: Iterable[str], created_by: str = "", now: datetime | None = None) -> int:
        seqs = list(seqs)
        with self._db() as db:
            cur = db.execute("INSERT INTO nhso18_exports (created_at, zip_path, visit_count, created_by) VALUES (?,?,?,?)",
                             ((now or datetime.now()).isoformat(timespec="seconds"), str(zip_path), len(seqs), created_by))
            db.executemany("INSERT INTO nhso18_export_keys (export_id, seq) VALUES (?,?)", [(cur.lastrowid, s) for s in seqs])
            return cur.lastrowid

    def nhso18_history(self, limit: int = 50) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, created_at, zip_path, visit_count, created_by FROM nhso18_exports ORDER BY id DESC LIMIT ?", (limit,))]

    def nhso18_path(self, export_id: int) -> Path | None:
        with self._db() as db:
            row = db.execute("SELECT zip_path FROM nhso18_exports WHERE id=?", (export_id,)).fetchone()
        return Path(row["zip_path"]) if row else None
