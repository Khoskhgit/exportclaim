from datetime import date

from app.ppfs_fluoride import check


def item(vn, day, hn="1", age=40, an=None, qty=1, amount=100, paidst="02"):
    return {"vn": vn, "hn": hn, "vstdate": date.fromisoformat(day), "vsttime": "09:00:00", "an": an, "pttype": "89",
            "pttype_name": "บัตรทอง", "ptname": "ผู้ป่วย ทดสอบ", "cid": "1" * 13, "nationality": "99", "sex": "1",
            "age_y": age, "icode": "3003902", "paidst": paidst, "item_name": "เคลือบฟลูออไรด์", "qty": qty, "amount": amount}


def data(items, unbilled=(), dental=None):
    history = [{"vn": i["vn"], "hn": i["hn"], "vstdate": i["vstdate"]} for i in items if i["qty"]]
    vns = [i["vn"] for i in items] + [u["vn"] for u in unbilled]
    return {"items": list(items), "unbilled": list(unbilled), "history": history,
            "dental": {v: ["2387021 ทาฟลูออไรด์-ผญร่วมกับprophylaxis"] for v in vns} if dental is None else dental}


def codes(row):
    return [e["code"] for e in row.errors], [w["code"] for w in row.warnings]


def test_a_clean_varnish():
    (row,) = check(data([item("v1", "2026-03-01")]))
    assert (row.key, row.count, str(row.amount), row.extra["year"]) == ("v1|15001", 1, "100.00", 2026)
    assert codes(row) == ([], [])


def test_twice_a_fiscal_year():
    rows = {r.vn: r for r in check(data([item(f"v{i}", d) for i, d in
                                         enumerate(["2025-10-02", "2026-02-01", "2026-09-30", "2026-10-01"])]))}
    assert [rows[f"v{i}"].count for i in range(4)] == [1, 2, 3, 1]
    assert codes(rows["v2"])[0] == ["OVER_YEAR"] and codes(rows["v1"])[0] == [] and codes(rows["v3"])[0] == []


def test_age_unbilled_and_others():
    rows = {r.vn: r for r in check(data(
        [item("v1", "2026-03-01", hn="1", age=24), item("v2", "2026-03-01", hn="2", age=60), item("v3", "2026-03-01", hn="3", an="69001"),
         item("v4", "2026-03-01", hn="4", paidst="03"), item("v5", "2026-03-01", hn="5", qty=2, amount=200)],
        unbilled=[item("v9", "2026-03-02", hn="9")], dental={"v9": ["2387020"], "v5": ["2387020"]}))}
    assert codes(rows["v1"])[0] == ["AGE"] and codes(rows["v2"])[0] == ["AGE"]
    assert codes(rows["v3"])[0] == ["IPD"] and codes(rows["v4"])[0] == ["SELF_PAID"]
    assert codes(rows["v5"]) == ([], ["PRICE"])
    assert codes(rows["v1"])[1] == ["NO_DENTAL"]  # billed but no dental record
    assert codes(rows["v9"])[0] == ["NO_ITEM"]  # dental record but nothing billed
