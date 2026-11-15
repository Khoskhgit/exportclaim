from datetime import date
from decimal import Decimal

from app.ppfs_ironchild import check, rewrite_adp


def item(vn, day, hn="1", months=8, nationality="99", an=None, qty=1, amount=50, paidst="02"):
    return {"vn": vn, "hn": hn, "vstdate": date.fromisoformat(day), "vsttime": "09:00:00", "an": an, "pttype": "89",
            "pttype_name": "บัตรทอง", "ptname": "เด็ก ทดสอบ", "cid": "1" * 13, "nationality": nationality, "sex": "1",
            "age_y": months // 12, "age_m": months % 12, "age_d": 0, "icode": "3004084", "paidst": paidst,
            "item_name": "18จ่ายยาเสริมธาตุเหล็ก", "qty": qty, "amount": amount}


def data(items, iron=None):
    history = [{"vn": i["vn"], "hn": i["hn"], "vstdate": i["vstdate"]} for i in items if i["qty"]]
    given = {i["vn"]: {"name": "Ferrous fumarate × 1", "qty": Decimal("1")} for i in items} if iron is None else iron
    return {"items": list(items), "history": history, "iron": given}


def codes(row):
    return [e["code"] for e in row.errors], [w["code"] for w in row.warnings]


def test_the_code_must_be_set_first():
    (row,) = check(data([item("v1", "2026-03-01")]), adp="")
    assert codes(row) == (["NO_ADP"], [])
    (row,) = check(data([item("v1", "2026-03-01")]), adp="IRON01")
    assert codes(row) == ([], []) and row.extra["adp"] == "IRON01" and row.extra["months"] == 8


def test_age_in_months_and_once_a_fiscal_year():
    rows = {r.vn: r for r in check(data([
        item("v1", "2025-10-05", months=7), item("v2", "2026-01-05", months=10), item("v3", "2026-10-05", months=19, hn="1"),
        item("v4", "2026-03-01", hn="4", months=5), item("v5", "2026-03-01", hn="5", months=12), item("v6", "2026-03-01", hn="6", months=13)]),
        adp="IRON01")}
    assert codes(rows["v1"])[0] == [] and codes(rows["v2"])[0] == ["OVER_YEAR"]
    assert codes(rows["v3"])[0] == ["AGE"]  # a new fiscal year, but too old
    assert codes(rows["v4"])[0] == ["AGE"] and codes(rows["v5"])[0] == [] and codes(rows["v6"])[0] == ["AGE"]


def test_others():
    rows = {r.vn: r for r in check(data([
        item("v1", "2026-03-01", hn="1", nationality="57"), item("v2", "2026-03-01", hn="2", an="69001"),
        item("v3", "2026-03-01", hn="3", paidst="03"), item("v4", "2026-03-01", hn="4", qty=2, amount=100)], iron={}), adp="IRON01")}
    assert codes(rows["v1"])[0] == ["NOT_THAI"] and codes(rows["v2"])[0] == ["IPD"] and codes(rows["v3"])[0] == ["SELF_PAID"]
    assert codes(rows["v4"]) == ([], ["NO_IRON", "PRICE"])


def test_the_zip_sends_the_real_code():
    built = {"ADP": [{"SEQ": "v1", "CODE": "10000"}, {"SEQ": "v2", "CODE": "10000"}, {"SEQ": "v1", "CODE": "30011"}]}
    rewrite_adp(built, {"v1"}, "IRON01")
    assert [r["CODE"] for r in built["ADP"]] == ["IRON01", "10000", "30011"]
