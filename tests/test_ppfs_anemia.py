from datetime import date

from app.ppfs_anemia import check


def item(vn, day, hn="1", sex="2", nationality="99", age=18, an=None, qty=1, amount=65, paidst="02"):
    return {"vn": vn, "hn": hn, "vstdate": date.fromisoformat(day), "vsttime": "09:00:00", "an": an, "pttype": "20",
            "pttype_name": "ประกันสังคม", "ptname": "ผู้ป่วย ทดสอบ", "cid": "1" * 13, "nationality": nationality, "sex": sex,
            "age_y": age, "icode": "3003900", "paidst": paidst, "item_name": "คัดกรองโลหิตจาง", "qty": qty, "amount": amount}


def data(items, history=(), labs=None):
    claimed = [{"vn": i["vn"], "hn": i["hn"], "vstdate": i["vstdate"]} for i in items if i["qty"]]
    return {"items": list(items), "history": claimed + list(history),
            "labs": {i["vn"]: {"Hb": "12.5", "Hct": "38"} for i in items} if labs is None else labs}


def codes(row):
    return [e["code"] for e in row.errors], [w["code"] for w in row.warnings]


def test_a_clean_screening():
    (row,) = check(data([item("v1", "2026-03-01")]))
    assert (row.key, row.count, str(row.amount), row.extra["labs"]) == ("v1|13001", 1, "65.00", {"Hb": "12.5", "Hct": "38"})
    assert codes(row) == ([], [])


def test_once_in_a_lifetime():
    rows = {r.vn: r for r in check(data([item("v2", "2026-03-01")], history=[{"vn": "v0", "hn": "1", "vstdate": date(2020, 5, 1)}]))}
    assert codes(rows["v2"])[0] == ["DONE_BEFORE"] and rows["v2"].extra["first"] == "2020-05-01" and rows["v2"].count == 2


def test_who_may_be_screened():
    rows = {r.vn: r for r in check(data([
        item("v1", "2026-03-01", hn="1", sex="1"), item("v2", "2026-03-01", hn="2", nationality="57"),
        item("v3", "2026-03-01", hn="3", age=25), item("v4", "2026-03-01", hn="4", age=12), item("v5", "2026-03-01", hn="5", an="69001"),
        item("v6", "2026-03-01", hn="6", paidst="03")]))}
    assert [codes(rows[f"v{i}"])[0] for i in range(1, 7)] == [["NOT_FEMALE"], ["NOT_THAI"], ["AGE"], ["AGE"], ["IPD"], ["SELF_PAID"]]


def test_missing_lab_and_price():
    (row,) = check(data([item("v1", "2026-03-01", qty=2, amount=130)], labs={"v1": {"Hb": "11"}}))
    assert codes(row) == ([], ["NO_LAB", "PRICE"])
