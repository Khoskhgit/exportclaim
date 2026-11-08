"""Gather AIPN data for a set of admissions and turn it into reviews and claims."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from . import aipn
from .aipn_db import AipnDb
from .config import Settings
from .validate import text


def _group(rows: list[dict], key: str = "an") -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[text(r.get(key))].append(r)
    return out


@dataclass
class Bundle:
    """Everything known about the requested admissions, fetched in a few queries."""
    admissions: list[dict]
    items: dict[str, list[dict]]
    ops: dict[str, list[dict]]
    catalog: list[dict]
    missing_debt: set[str] = field(default_factory=set)


def fetch(db: AipnDb, admissions: list[dict], prices: dict[str, dict] | None = None) -> Bundle:
    """prices: unit prices the user set in this program, per AN and item key."""
    ans = [text(a["an"]) for a in admissions]
    items = db.items(ans)
    grouped = {an: aipn.apply_prices(rows, (prices or {}).get(an)) for an, rows in _group(items).items()}
    return Bundle(
        admissions=admissions,
        items=grouped,
        ops=_group(db.ops(ans)),
        catalog=db.claimcat(text(i.get("icode")) for i in items),
        missing_debt=db.missing_debt(ans),
    )


def rows_for(bundle: Bundle, adm: dict, excluded: set[str], included: set[str]) -> list[aipn.BillRow]:
    an = text(adm["an"])
    return aipn.bill_rows(
        bundle.items.get(an, []), bundle.catalog, diag=text(adm.get("pdx")).upper()[:4],
        excluded=excluded, included=included,
    )


def claims(db: AipnDb, bundle: Bundle, settings: Settings, excluded: dict[str, set[str]],
           included: dict[str, set[str]], care_as: dict[str, str]) -> list[aipn.Claim]:
    ans = [text(a["an"]) for a in bundle.admissions]
    adt = {text(r["an"]): r for r in db.adt(ans)}
    dx = _group(db.dx(ans))
    copay = _group(db.copay(ans))
    result = []
    for adm in bundle.admissions:
        an = text(adm["an"])
        chosen = care_as.get(an) or aipn.default_care_as(aipn.hmain(adm), settings.hcode, settings.hosp_main)
        result.append(aipn.Claim(
            adm=adm, adt=adt.get(an, {}), dx=dx.get(an, []), ops=bundle.ops.get(an, []),
            rows=rows_for(bundle, adm, excluded.get(an, set()), included.get(an, set())),
            copay=copay.get(an, []), care_as=chosen,
        ))
    return result
