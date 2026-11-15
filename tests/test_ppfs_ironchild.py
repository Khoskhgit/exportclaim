from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.ppfs_ironchild import _form, bands, check, form_codes, keep_icodes, rewrite_adp

SETTINGS = SimpleNamespace(ppfs_iron_child_icodes=("3004084",), ppfs_iron_child2_icodes=("3004023",),
                           ppfs_iron_syrup_adp="689697", ppfs_iron_tablet_adp="776508")
BANDS, CODES = bands(SETTINGS), form_codes(SETTINGS)
SYRUP = {"name": "Ferrous fumarate 15 mg/0.6ml × 1", "qty": Decimal("1"), "forms": {"syrup"}}
TABLET = {"name": "Ferrous fumarate 200 mg × 30", "qty": Decimal("30"), "forms": {"tablet"}}


def item(vn, day, hn="1", months=8, icode="3004084", nationality="99", an=None, qty=1, amount=50, paidst="02"):
    return {"vn": vn, "hn": hn, "vstdate": date.fromisoformat(day), "vsttime": "09:00:00", "an": an, "pttype": "89",
            "pttype_name": "บัตรทอง", "ptname": "เด็ก ทดสอบ", "cid": "1" * 13, "nationality": nationality, "sex": "1",
            "age_y": months // 12, "age_m": months % 12, "age_d": 0, "icode": icode, "paidst": paidst,
            "item_name": "18จ่ายยาเสริมธาตุเหล็ก", "qty": qty, "amount": amount}


def data(items, iron=None):
    history = [{"vn": i["vn"], "hn": i["hn"], "vstdate": i["vstdate"], "icode": i["icode"]} for i in items if i["qty"]]
    return {"items": list(items), "history": history, "iron": {i["vn"]: SYRUP for i in items} if iron is None else iron}


def codes(row):
    return [e["code"] for e in row.errors], [w["code"] for w in row.warnings]


def test_the_form_of_the_drug():
    assert [_form(f) for f in ("SYRUPS", "SUSPENSIONS", "TABLETS", "INJECTIONS", "")] == ["syrup", "syrup", "tablet", "", ""]


def test_the_code_follows_the_drug_given():
    syrup, tablet = check(data([item("v1", "2026-03-01"), item("v2", "2026-03-02", hn="2", months=36, icode="3004023")],
                               iron={"v1": SYRUP, "v2": TABLET}), BANDS, CODES)
    assert (syrup.key, syrup.extra["form"], syrup.extra["band"], codes(syrup)) == ("v1|689697", "ยาน้ำ", "6–12 เดือน", ([], []))
    assert (tablet.key, tablet.extra["form"], tablet.extra["band"], codes(tablet)) == ("v2|776508", "ยาเม็ด", "2–5 ปี", ([], []))


def test_no_drug_or_both_forms_cannot_be_sent():
    rows = {r.vn: r for r in check(data([item("v1", "2026-03-01"), item("v2", "2026-03-01", hn="2")],
                                        iron={"v2": {**SYRUP, "forms": {"syrup", "tablet"}}}), BANDS, CODES)}
    assert codes(rows["v1"])[0] == ["NO_IRON"] and rows["v1"].code == "10000-1"
    assert codes(rows["v2"])[0] == ["BOTH_FORMS"]


def test_age_of_each_band_and_once_a_fiscal_year():
    rows = {r.vn: r for r in check(data([
        item("v1", "2025-10-05", months=7), item("v2", "2026-01-05", months=10), item("v3", "2026-03-01", hn="3", months=13),
        item("v4", "2026-03-01", hn="4", months=5), item("v5", "2026-03-01", hn="5", months=12),
        item("v6", "2026-03-01", hn="6", months=23, icode="3004023"), item("v7", "2026-03-01", hn="7", months=71, icode="3004023"),
        item("v8", "2026-03-01", hn="8", months=72, icode="3004023")]), BANDS, CODES)}
    assert codes(rows["v1"])[0] == [] and codes(rows["v2"])[0] == ["OVER_YEAR"]
    assert [codes(rows[v])[0] for v in ("v3", "v4", "v5", "v6", "v7", "v8")] == [["AGE"], ["AGE"], [], ["AGE"], [], ["AGE"]]


def test_others():
    rows = {r.vn: r for r in check(data([
        item("v1", "2026-03-01", hn="1", nationality="57"), item("v2", "2026-03-01", hn="2", an="69001"),
        item("v3", "2026-03-01", hn="3", paidst="03"), item("v4", "2026-03-01", hn="4", qty=2, amount=100)]), BANDS, CODES)}
    assert codes(rows["v1"])[0] == ["NOT_THAI"] and codes(rows["v2"])[0] == ["IPD"] and codes(rows["v3"])[0] == ["SELF_PAID"]
    assert codes(rows["v4"]) == ([], ["PRICE"])


def test_the_zip_sends_the_chosen_code():
    assert keep_icodes(BANDS, {"776508"}) == {"3004084", "3004023"}
    built = {"ADP": [{"SEQ": "v1", "CODE": "10000"}, {"SEQ": "v2", "CODE": "10000"}, {"SEQ": "v3", "CODE": "10000"},
                     {"SEQ": "v1", "CODE": "30011"}]}
    rewrite_adp(built, {"v1": {"689697"}, "v2": {"776508"}})
    assert [r["CODE"] for r in built["ADP"]] == ["689697", "776508", "10000", "30011"]
    # A ticked vaccine also under 10000 keeps its code: only the fee's rate is rewritten.
    built = {"ADP": [{"SEQ": "v1", "CODE": "10000", "RATE": "50.00"}, {"SEQ": "v1", "CODE": "10000", "RATE": "20.00"}]}
    rewrite_adp(built, {"v1": {"689697"}}, Decimal("50"))
    assert [r["CODE"] for r in built["ADP"]] == ["689697", "10000"]
