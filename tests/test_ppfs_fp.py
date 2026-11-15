from datetime import date

from app.ppfs_fp import check


def item(vn, day, code, qty=1, amount=None, drug=False, hn="1", sex="2", nationality="99", age=25, an=None):
    rate = {"FP003_1": 40, "FP003_2": 80, "FP003_3": 50, "FP003_4": 60, "FP001": 800, "FP002_1": 2150, "FP002_2": 350}[code]
    return {"vn": vn, "hn": hn, "vstdate": date.fromisoformat(day), "vsttime": "09:00:00", "an": an, "pttype": "20",
            "pttype_name": "ประกันสังคม", "ptname": "ผู้ป่วย ทดสอบ", "cid": "1" * 13, "nationality": nationality, "sex": sex,
            "age_y": age, "code": code, "icode": "1500195" if drug else "3003891", "item_name": "DMPA" if drug else "18ค่าบริการ",
            "qty": qty, "amount": (11.5 * qty if drug else rate * qty) if amount is None else amount, "is_drug": drug}


def data(items, births=(), migraine=(), implants=None):
    history = {}
    for i in items:
        if not i["is_drug"] and i["qty"]:
            key = (i["vn"], i["code"])
            history.setdefault(key, {"vn": i["vn"], "hn": i["hn"], "vstdate": i["vstdate"], "code": i["code"], "qty": 0})
            history[key]["qty"] += i["qty"]
    return {"items": list(items), "history": list(history.values()),
            "births": [{"hn": h, "labor_date": date.fromisoformat(d)} for h, d in births], "migraine": set(migraine),
            "implants": implants or {}}


def codes(row):
    return [e["code"] for e in row.errors], [w["code"] for w in row.warnings]


def test_the_fee_is_claimed_and_the_drug_shown():
    (row,) = check(data([item("v1", "2026-03-01", "FP003_4"), item("v1", "2026-03-01", "FP003_4", drug=True)]))
    assert (row.key, str(row.qty), str(row.amount), row.extra["drug_qty"], row.count) == ("v1|FP003_4", "1", "60.00", "1", 1)
    assert codes(row) == ([], [])


def test_drug_without_fee_and_people():
    rows = {r.vn: r for r in check(data([
        item("v1", "2026-03-01", "FP003_4", drug=True),
        item("v2", "2026-03-01", "FP003_4", hn="2", sex="1"),
        item("v3", "2026-03-01", "FP003_4", hn="3", nationality="57", age=55),
        item("v4", "2026-03-01", "FP003_4", hn="4", an="69001", age=8)]))}
    assert codes(rows["v1"])[0] == ["NO_ITEM"]
    assert codes(rows["v2"])[0] == ["NOT_FEMALE"] and codes(rows["v3"]) == (["NOT_THAI"], [])
    assert codes(rows["v4"]) == (["IPD"], ["AGE"])


def test_yearly_caps():
    pills = [item(f"p{m}", f"2026-{m:02d}-01", "FP003_1", qty=3) for m in range(1, 6)]  # 15 packs
    shots = [item(f"s{m}", f"2026-{m:02d}-02", "FP003_4", hn="2") for m in range(1, 7)]  # 6 shots
    ec = [item("e1", "2026-01-03", "FP003_3", qty=2, hn="3"), item("e2", "2026-02-03", "FP003_3", hn="3")]
    next_year = [item("s9", "2027-01-02", "FP003_4", hn="2")]
    rows = {r.vn: r for r in check(data(pills + shots + ec + next_year))}
    assert [rows[f"p{m}"].count for m in range(1, 6)] == [3, 6, 9, 12, 15]
    assert codes(rows["p4"])[0] == [] and codes(rows["p5"])[0] == ["OVER_YEAR"]
    assert codes(rows["s5"])[0] == [] and codes(rows["s6"])[0] == ["OVER_YEAR"] and codes(rows["s9"])[0] == []
    assert codes(rows["e1"])[0] == [] and codes(rows["e2"])[0] == ["OVER_YEAR"]


def test_pills_and_single_hormone():
    rows = {r.vn: r for r in check(data([
        item("v1", "2026-03-01", "FP003_1", qty=4),
        item("v2", "2026-03-01", "FP003_2", hn="2"),  # birth a year ago: breast-feeding
        item("v3", "2026-03-01", "FP003_2", hn="3"),  # migraine with aura
        item("v4", "2026-03-01", "FP003_2", hn="4"),  # neither
        item("v5", "2026-03-01", "FP003_1", hn="5"), item("v5", "2026-03-01", "FP003_1", hn="5", qty=3, drug=True)],
        births=[("2", "2025-03-01"), ("4", "2023-01-01")], migraine=["3"]))}
    assert codes(rows["v1"])[0] == ["PER_VISIT"]
    assert codes(rows["v2"]) == ([], []) and codes(rows["v3"]) == ([], [])
    assert codes(rows["v4"])[1] == ["SINGLE_HORMONE"]
    assert codes(rows["v5"])[1] == ["DRUG_QTY"]  # fee for 1 pack, 3 packs given


def test_removal_and_price():
    rows = {r.vn: r for r in check(data([
        item("v1", "2026-03-01", "FP002_2"), item("v2", "2026-03-01", "FP002_2", hn="2"),
        item("v3", "2026-03-01", "FP002_1", hn="3", amount=2000)], implants={"1": date(2023, 5, 1)}))}
    assert codes(rows["v1"]) == ([], []) and codes(rows["v2"])[1] == ["NO_IMPLANT"]
    assert codes(rows["v3"])[1] == ["PRICE"]


def test_a_fee_the_patient_paid_is_not_sent():
    paid = {**item("v1", "2026-03-01", "FP002_1"), "paidst": "03"}
    (row,) = check(data([paid]))
    assert codes(row)[0] == ["SELF_PAID"]
