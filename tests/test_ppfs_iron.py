from datetime import date
from decimal import Decimal

from app.ppfs_iron import check


def item(vn, day, hn="1", sex="2", nationality="99", age=30, an=None, qty=1, amount=80, paidst="02"):
    return {"vn": vn, "hn": hn, "vstdate": date.fromisoformat(day), "vsttime": "09:00:00", "an": an, "pttype": "20",
            "pttype_name": "ประกันสังคม", "ptname": "ผู้ป่วย ทดสอบ", "cid": "1" * 13, "nationality": nationality, "sex": sex,
            "age_y": age, "icode": "3003901", "paidst": paidst, "item_name": "ยาเม็ดเสริมธาตุเหล็ก", "qty": qty, "amount": amount}


def data(items, iron=None):
    history = [{"vn": i["vn"], "hn": i["hn"], "vstdate": i["vstdate"]} for i in items if i["qty"]]
    given = {i["vn"]: {"name": "FERROFOLIC", "qty": Decimal("52")} for i in items} if iron is None else iron
    return {"items": list(items), "history": history, "iron": given}


def codes(row):
    return [e["code"] for e in row.errors], [w["code"] for w in row.warnings]


def test_a_clean_supply():
    (row,) = check(data([item("v1", "2026-03-01")]))
    assert (row.key, row.count, str(row.amount), row.extra["drug_qty"]) == ("v1|14001", 1, "80.00", "52")
    assert codes(row) == ([], [])


def test_once_a_fiscal_year():
    rows = {r.vn: r for r in check(data([item("v1", "2025-10-05"), item("v2", "2026-09-30"), item("v3", "2026-10-01")]))}
    assert codes(rows["v1"])[0] == [] and codes(rows["v2"])[0] == ["OVER_YEAR"] and codes(rows["v3"])[0] == []
    assert rows["v2"].extra["first"] == "2025-10-05"


def test_who_may_receive_it():
    rows = {r.vn: r for r in check(data([
        item("v1", "2026-03-01", hn="1", sex="1"), item("v2", "2026-03-01", hn="2", nationality="57"),
        item("v3", "2026-03-01", hn="3", age=46), item("v4", "2026-03-01", hn="4", age=12), item("v5", "2026-03-01", hn="5", an="69001"),
        item("v6", "2026-03-01", hn="6", paidst="03")]))}
    assert [codes(rows[f"v{i}"])[0] for i in range(1, 7)] == [["NOT_FEMALE"], ["NOT_THAI"], ["AGE"], ["AGE"], ["IPD"], ["SELF_PAID"]]


def test_tablets_and_price():
    rows = {r.vn: r for r in check(data([item("v1", "2026-03-01"), item("v2", "2026-03-01", hn="2", qty=2, amount=160)],
                                        iron={"v2": {"name": "FERROUS", "qty": Decimal("90")}}))}
    assert codes(rows["v1"]) == ([], ["NO_IRON"])
    assert codes(rows["v2"]) == ([], ["TABLETS", "PRICE"])
