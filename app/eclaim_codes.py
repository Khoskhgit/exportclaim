"""The NHSO e-Claim C error codes: what each code means and how to fix it.

eclaim_c_codes.json was read from the สปสช. document "C ERROR E-CLAIM NHSO" (C-Error-E-Claim-NHSO_06-01-2565.pdf,
47 pages): code, detail (รายละเอียด), fix (วิธีปฏิบัติ/แนวทางการแก้ไข) and the page it is on. The codes are the
ones in the e-Claim REP (บัตรทอง UCS, ข้าราชการ OFC, อปท. LGO). SSOP (ปกส. ผู้ป่วยนอก) and AIPN (ปกส. ผู้ป่วยใน)
have their own code sets with the same digits, so the lookup is used only for e-Claim REP."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Iterable

PATH = Path(__file__).with_name("eclaim_c_codes.json")
SOURCE = "C ERROR E-CLAIM NHSO (สปสช. 06-01-2565)"
ACCOUNTS = frozenset({"UCS", "OFC", "LGO"})


@lru_cache(maxsize=1)
def table() -> dict[str, dict]:
    return {row["code"]: row for row in json.loads(PATH.read_text(encoding="utf-8"))}


def is_eclaim(account: str | None) -> bool:
    return (account or "").upper() in ACCOUNTS


def split(codes: str | None) -> list[str]:
    return [c for c in (p.strip().upper() for p in (codes or "").split(",")) if c]


def explain(code: str) -> dict:
    """The detail and fix of one code; codes not in the document say so."""
    found = table().get((code or "").strip().upper())
    if not found:
        return {"code": code, "detail": "", "fix": "", "found": False}
    return {"code": found["code"], "detail": found["detail"], "fix": found["fix"], "page": found["page"], "found": True}


def describe(codes: Iterable[str]) -> dict[str, dict]:
    """{code: explain(code)} for each distinct code."""
    return {c: explain(c) for c in dict.fromkeys(c for c in codes if c)}


def search(query: str = "", limit: int = 100) -> list[dict]:
    """Codes whose number, detail or fix contain the query (all of them when it is empty)."""
    q = (query or "").strip().lower()
    rows = [r for r in table().values() if not q or q in r["code"].lower() or q in r["detail"].lower() or q in r["fix"].lower()]
    exact = [r for r in rows if r["code"].lower() == q]
    rest = [r for r in rows if r["code"].lower() != q]
    return [{k: r[k] for k in ("code", "detail", "fix", "page")} for r in exact + rest][:limit]
