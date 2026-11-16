from datetime import date

from app.ppfs import fiscal_year
from app.ppfs_ncd import FPG, LIPID, check


def item(vn, day, code, hn="1", age=50, an=None, qty=1, amount=None, paidst="02"):
    return {"vn": vn, "hn": hn, "vstdate": date.fromisoformat(day), "vsttime": "08:00:00", "an": an, "pttype": "20",
            "pttype_name": "ประกันสังคม", "ptname": "ผู้ป่วย ทดสอบ", "cid": "1" * 13, "nationality": "99", "sex": "1",
            "age_y": age, "code": code, "icode": "3003780", "paidst": paidst, "item_name": "คัดกรอง", "qty": qty,
            "amount": (40 if code == FPG else 160) * qty if amount is None else amount}


def data(items, history=(), dm=None, lipid=None, statin=None, labs=None):
    claimed = [{"vn": i["vn"], "hn": i["hn"], "vstdate": i["vstdate"], "code": i["code"]} for i in items if i["qty"]]
    full = {i["vn"]: {"FPG": "95", "TC": "180", "HDL": "50"} for i in items}
    return {"items": list(items), "history": claimed + list(history), "labs": full if labs is None else labs,
            "dm": dm or {}, "lipid": lipid or {}, "statin": statin or {}}


def codes(row):
    return [e["code"] for e in row.errors], [w["code"] for w in row.warnings]


def test_fiscal_year():
    assert [fiscal_year(date.fromisoformat(d)) for d in ("2025-09-30", "2025-10-01", "2026-09-30")] == [2025, 2026, 2026]


def test_clean_screenings():
    rows = check(data([item("v1", "2026-03-01", FPG), item("v1", "2026-03-01", LIPID)]))
    assert [codes(r) for r in rows] == [([], []), ([], [])]
    assert rows[0].extra["labs"] == {"FPG": "95"} and rows[1].extra["labs"] == {"TC": "180", "HDL": "50"}


def test_ages():
    rows = {r.key: r for r in check(data([item("v1", "2026-03-01", FPG, age=60), item("v1", "2026-03-01", LIPID, age=60),
                                          item("v2", "2026-03-01", LIPID, hn="2", age=44)]))}
    assert codes(rows["v1|12003"])[0] == ["AGE"] and codes(rows["v1|12004"])[0] == []
    assert codes(rows["v2|12004"])[0] == ["AGE"]


def test_treated_before_or_found_today():
    rows = {r.key: r for r in check(data(
        [item("v1", "2026-03-01", FPG), item("v2", "2026-03-01", FPG, hn="2"), item("v3", "2026-03-01", LIPID, hn="3"),
         item("v4", "2026-03-01", LIPID, hn="4"), item("v5", "2026-03-01", LIPID, hn="5")],
        dm={"1": date(2020, 1, 1), "2": date(2026, 3, 1)}, lipid={"4": date(2026, 3, 1)}, statin={"3": date(2019, 5, 1)}))}
    assert codes(rows["v1|12003"])[0] == ["DM_HISTORY"] and codes(rows["v2|12003"]) == ([], ["NEW_DM"])
    assert codes(rows["v3|12004"])[0] == ["LIPID_HISTORY"] and codes(rows["v4|12004"]) == ([], ["NEW_LIPID"])
    assert codes(rows["v5|12004"]) == ([], [])


def test_once_a_fiscal_year_and_once_in_five_years():
    old = [{"vn": "o1", "hn": "1", "vstdate": date(2025, 11, 1), "code": FPG},
           {"vn": "o2", "hn": "1", "vstdate": date(2025, 9, 30), "code": FPG},
           {"vn": "o3", "hn": "2", "vstdate": date(2022, 3, 1), "code": LIPID},
           {"vn": "o4", "hn": "3", "vstdate": date(2020, 1, 1), "code": LIPID}]
    rows = {r.key: r for r in check(data(
        [item("v1", "2026-03-01", FPG), item("v2", "2026-03-01", LIPID, hn="2"), item("v3", "2026-03-01", LIPID, hn="3")],
        history=old))}
    assert codes(rows["v1|12003"])[0] == ["OVER_YEAR"]  # o1 is in the same fiscal year 2569; o2 is in 2568
    assert codes(rows["v2|12004"])[0] == ["WITHIN_5Y"] and rows["v2|12004"].extra["last"] == "2022-03-01"
    assert codes(rows["v3|12004"])[0] == []  # more than 5 years ago


def test_missing_lab_price_and_self_paid():
    rows = {r.key: r for r in check(data([item("v1", "2026-03-01", LIPID, qty=2), item("v2", "2026-03-01", FPG, hn="2", paidst="03")],
                                         labs={"v1": {"TC": "200"}}))}
    assert codes(rows["v1|12004"])[1] == ["NO_LAB", "PRICE"]
    assert "SELF_PAID" in codes(rows["v2|12003"])[0]


def test_a_screening_visit_without_the_fee():
    unbilled = {**item("v9", "2026-11-05", FPG, hn="9", qty=0, amount=0), "code": FPG}
    rows = check({**data([]), "unbilled": [unbilled], "labs": {"v9": {"FPG": "98"}}})
    (row,) = rows
    assert (row.key, codes(row)[0]) == ("v9|12003", ["NO_ITEM"]) and "3003780" in row.errors[0]["message"]
