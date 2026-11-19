from app import eclaim_codes


def test_the_lookup_holds_every_code_of_the_nhso_document():
    table = eclaim_codes.table()
    assert len(table) == 578 and {"101", "565", "999", "EA1"} <= set(table)
    for row in table.values():
        assert row["detail"] and " า" not in row["detail"] and "ครั ง" not in row["fix"]


def test_explain_gives_the_detail_and_each_fix_step_on_its_own_line():
    found = eclaim_codes.explain("999")
    assert found["found"] and "สนบท" in found["detail"]
    assert found["fix"].split("\n")[0].startswith("1.") and found["fix"].split("\n")[1].startswith("2.")
    assert eclaim_codes.explain(" 140 ")["detail"].startswith("AN ซ้ำ")
    assert eclaim_codes.explain("C99") == {"code": "C99", "detail": "", "fix": "", "found": False}


def test_only_e_claim_accounts_use_the_lookup():
    assert eclaim_codes.is_eclaim("UCS") and eclaim_codes.is_eclaim("ofc") and eclaim_codes.is_eclaim("LGO")
    assert not eclaim_codes.is_eclaim("SIGN") and not eclaim_codes.is_eclaim("SOCD") and not eclaim_codes.is_eclaim(None)


def test_search_puts_the_exact_code_first_and_finds_words():
    assert eclaim_codes.search("565")[0]["code"] == "565"
    assert all("Vascular" in r["detail"] + r["fix"] for r in eclaim_codes.search("vascular"))
    assert len(eclaim_codes.search("", 10)) == 10
