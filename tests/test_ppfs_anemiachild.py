from datetime import date
from types import SimpleNamespace

from app.ppfs_anemiachild import bands, check
from app.ppfs_ironchild import keep_icodes, rewrite_adp

SETTINGS = SimpleNamespace(ppfs_anemia_child_adp="10000", ppfs_anemia_child_icodes=("3003783",),
                           ppfs_anemia_child2_icodes=("3004022",))
BANDS = bands(SETTINGS)


def item(vn, day, hn="1", months=10, icode="3003783", nationality="99", an=None, qty=1, amount=30, paidst="02"):
    return {"vn": vn, "hn": hn, "vstdate": date.fromisoformat(day), "vsttime": "09:00:00", "an": an, "pttype": "89",
            "pttype_name": "บัตรทอง", "ptname": "เด็ก ทดสอบ", "cid": "1" * 13, "nationality": nationality, "sex": "2",
            "age_y": months // 12, "age_m": months % 12, "age_d": 0, "icode": icode, "paidst": paidst,
            "item_name": "คัดกรองโลหิตจาง Hb/Hct", "qty": qty, "amount": amount}


def data(items, history=(), labs=None):
    claimed = [{"vn": i["vn"], "hn": i["hn"], "vstdate": i["vstdate"], "icode": i["icode"]} for i in items if i["qty"]]
    return {"items": list(items), "history": claimed + list(history),
            "labs": {i["vn"]: {"Hb": "11.2", "Hct": "34"} for i in items} if labs is None else labs}


def codes(row):
    return [e["code"] for e in row.errors], [w["code"] for w in row.warnings]


def test_both_bands_send_10000():
    infant, child = check(data([item("v1", "2026-03-01"), item("v2", "2026-03-02", hn="2", months=40, icode="3004022")]), BANDS)
    assert (infant.key, infant.extra["band"], codes(infant)) == ("v1|10000", "6–12 เดือน", ([], []))
    assert (child.key, child.extra["band"], codes(child)) == ("v2|10000", "3–5 ปี", ([], []))


def test_once_in_each_band_for_life():
    old = [{"vn": "o1", "hn": "1", "vstdate": date(2020, 5, 1), "icode": "3003783"}]
    rows = {r.vn: r for r in check(data([item("v1", "2026-03-01", months=11), item("v2", "2026-03-01", hn="2", months=40, icode="3004022")],
                                        history=old + [{"vn": "o2", "hn": "2", "vstdate": date(2023, 1, 1), "icode": "3003783"}]), BANDS)}
    assert codes(rows["v1"])[0] == ["DONE_BEFORE"] and rows["v1"].extra["first"] == "2020-05-01"
    assert codes(rows["v2"])[0] == [] and rows["v2"].extra["other_band"] == "2023-01-01"  # the other band does not count


def test_age_of_each_band():
    rows = {r.vn: r for r in check(data([
        item("v1", "2026-03-01", hn="1", months=5), item("v2", "2026-03-01", hn="2", months=13),
        item("v3", "2026-03-01", hn="3", months=35, icode="3004022"), item("v4", "2026-03-01", hn="4", months=72, icode="3004022"),
        item("v5", "2026-03-01", hn="5", months=36, icode="3004022")]), BANDS)}
    assert [codes(rows[f"v{i}"])[0] for i in range(1, 6)] == [["AGE"], ["AGE"], ["AGE"], ["AGE"], []]


def test_others_and_the_zip():
    rows = {r.vn: r for r in check(data([
        item("v1", "2026-03-01", hn="1", nationality="57"), item("v2", "2026-03-01", hn="2", an="69001"),
        item("v3", "2026-03-01", hn="3", paidst="03"), item("v4", "2026-03-01", hn="4", qty=2, amount=60)], labs={}), BANDS)}
    assert codes(rows["v1"])[0] == ["NOT_THAI"] and codes(rows["v2"])[0] == ["IPD"] and codes(rows["v3"])[0] == ["SELF_PAID"]
    assert codes(rows["v4"]) == ([], ["NO_LAB", "PRICE"])
    assert keep_icodes(BANDS, {"10000"}) == {"3003783", "3004022"}
    built = {"ADP": [{"SEQ": "v1", "CODE": "10000"}]}
    rewrite_adp(built, {"v1": {"10000"}})
    assert built["ADP"][0]["CODE"] == "10000"  # sent with the code HOSxP has
