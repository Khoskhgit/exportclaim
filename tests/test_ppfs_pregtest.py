from datetime import date

from app.ppfs_pregtest import check


def visit(vn, day, hn="1", sex="2", nationality="99", age=25, an=None, qty=1, amount=75):
    return {"vn": vn, "hn": hn, "vstdate": date.fromisoformat(day), "vsttime": "09:00:00", "an": an, "pttype": "20",
            "pttype_name": "ประกันสังคม", "ptname": "ผู้ป่วย ทดสอบ", "cid": "1" * 13, "nationality": nationality,
            "sex": sex, "age_y": age, "icode": "3003896", "item_name": "ทดสอบการตั้งครรภ์", "qty": qty, "amount": amount}


def data(items, unbilled=(), upt=None, z32=None):
    history = [{"vn": i["vn"], "hn": i["hn"], "vstdate": i["vstdate"]} for i in items if i["qty"]]
    vns = [i["vn"] for i in items]
    return {"items": list(items), "unbilled": list(unbilled), "history": history,
            "upt": {v: "Negative" for v in vns} if upt is None else upt, "z32": set(vns) if z32 is None else z32}


def codes(row):
    return [e["code"] for e in row.errors], [w["code"] for w in row.warnings]


def test_a_clean_test():
    (row,) = check(data([visit("v1", "2026-03-01")]))
    assert (row.key, row.count, str(row.amount), row.extra["upt"], row.extra["z32"]) == ("v1|30014", 1, "75.00", "Negative", True)
    assert codes(row) == ([], [])


def test_four_a_fiscal_year():
    days = ["2025-10-01", "2026-01-05", "2026-03-01", "2026-05-01", "2026-09-30", "2026-10-01"]
    rows = {r.vn: r for r in check(data([visit(f"v{i}", d) for i, d in enumerate(days)]))}
    assert [rows[f"v{i}"].count for i in range(6)] == [1, 2, 3, 4, 5, 1]  # counts start again on 1 October
    assert codes(rows["v4"])[0] == ["OVER_COUNT"] and codes(rows["v3"])[0] == [] and codes(rows["v5"])[0] == []


def test_errors_and_warnings():
    rows = {r.vn: r for r in check(data(
        [visit("v1", "2026-03-01", hn="1"), visit("v2", "2026-03-01", hn="1"), visit("v3", "2026-03-02", hn="3", sex="1"),
         visit("v4", "2026-03-02", hn="4", nationality="57", age=8, qty=2, amount=150), visit("v5", "2026-03-03", hn="5", an="69001")],
        unbilled=[visit("v9", "2026-03-04", hn="9")], upt={"v1": "Negative", "v2": "Positive"}, z32={"v1", "v2", "v9"}))}
    assert codes(rows["v1"])[0] == [] and codes(rows["v2"])[0] == ["SAME_DAY"]
    assert codes(rows["v3"])[0] == ["NOT_FEMALE"]
    assert codes(rows["v4"]) == ([], ["NOT_THAI", "AGE", "NO_UPT", "NO_Z32", "PRICE"])  # a foreigner may still be sent
    assert codes(rows["v5"])[0] == ["IPD"]
    assert codes(rows["v9"])[0] == ["NO_ITEM"]  # Z32 but nothing billed: key the item in HOSxP
