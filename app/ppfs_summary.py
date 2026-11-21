"""The PPFS dashboard: for each fund (service type), what was sent, what the REP said and what is still unsent.

The REP and STM are per visit (InvNo = VN). The PPFS ZIP carries only the claimed PP items, so a visit's REP line is
that claim. A visit whose fees were sent under more than one PPFS fund shares its payout between the funds by the
amount each one sent. A REP issued before the visit was sent belongs to an earlier claim, so it does not count."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal

from . import eclaim_codes

STATUSES = ("passed", "c", "deny", "waiting")


def _money(value) -> Decimal:
    try:
        return Decimal(str(value or "0").replace(",", "")) if str(value or "").strip() else Decimal(0)
    except ArithmeticError:
        return Decimal(0)


def rep_status(rep: dict | None, sent_at: str) -> str:
    """passed (A), deny (code DENY), c (other C codes), or waiting (no REP yet for this send)."""
    if not rep or not rep.get("rep_stat") or (rep.get("rep_at") or "")[:10] < (sent_at or "")[:10]:
        return "waiting"
    if "DENY" in eclaim_codes.split(rep.get("rep_codes")):
        return "deny"
    return "passed" if rep["rep_stat"] == "A" else "c"


def summarize(rows: list, latest: dict[str, dict], shares: dict[str, Decimal] | None = None) -> dict:
    """rows: the checked ppfs.Row of one fund; latest: RepStmStore.latest() of the sent VNs;
    shares: vn -> this fund's part of the visit's payout (1 when missing)."""
    shares = shares or {}
    out = {k: {"n": 0, "amount": Decimal(0)} for k in ("all", "sent", "ready", "error", *STATUSES)}
    paid = stm_paid = Decimal(0)
    codes: Counter = Counter()
    seen: set[str] = set()
    for r in rows:
        amount = Decimal(r.amount or 0)
        parts = ["all"]
        if r.sent:
            status = rep_status(latest.get(r.vn), r.sent)
            parts += ["sent", status]
            if r.vn not in seen:  # the payout and codes are the visit's: count them once
                seen.add(r.vn)
                rep = latest.get(r.vn) or {}
                share = shares.get(r.vn, Decimal(1))
                if status != "waiting":
                    paid += _money(rep.get("rep_paid")) * share
                    if status in ("c", "deny"):
                        codes.update(eclaim_codes.split(rep.get("rep_codes")))
                stm_paid += _money(rep.get("stm_paid")) * share
        else:
            parts.append("error" if r.errors else "ready")
        for p in parts:
            out[p]["n"] += 1
            out[p]["amount"] += amount
    result = {k: {"n": v["n"], "amount": f"{v['amount']:.2f}"} for k, v in out.items()}
    result["visits"] = len({r.vn for r in rows})
    result["paid"] = f"{paid:.2f}"
    result["stm_paid"] = f"{stm_paid:.2f}"
    result["codes"] = [{"code": c, "n": n, "detail": eclaim_codes.explain(c)["detail"]} for c, n in codes.most_common()]
    return result


def shares(vns: list[str], sent_by_fund: dict[str, dict[str, Decimal]], fund: str) -> dict[str, Decimal]:
    """vn -> this fund's part of what was sent for the visit, by the amounts in the export history."""
    out: dict[str, Decimal] = {}
    for vn in vns:
        funds = sent_by_fund.get(vn) or {}
        total = sum(funds.values(), Decimal(0))
        if len(funds) > 1 and total > 0:
            out[vn] = funds.get(fund, Decimal(0)) / total
    return out
