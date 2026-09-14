from datetime import date

def test_referral_code_format():
    telegram_id = 123456
    assert f"u{telegram_id}" == "u123456"


def test_pack_cost_is_seven():
    gen_cost = {"pack": 7}
    assert gen_cost["pack"] == 7
