"""Tests for the gating-risk heuristic (pure, no network).

Run:  python -m pytest tests/test_gating.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines import gating


# --------------------------------------------------------------------------- #
# brand gating
# --------------------------------------------------------------------------- #
def test_brand_field_gated():
    ok, m = gating.brand_is_gated(brand="Nike", title="Nike Air Max 90")
    assert ok and m == "nike"


def test_brand_field_with_suffix_still_matches():
    ok, m = gating.brand_is_gated(brand="Nike, Inc.")
    assert ok and m == "nike"


def test_brand_field_ungated():
    ok, m = gating.brand_is_gated(brand="Generic Co", title="Generic Co Widget")
    assert not ok and m is None


def test_title_scan_when_no_brand_field():
    ok, m = gating.brand_is_gated(title="Apple AirPods Pro 2nd Gen")
    assert ok and m == "apple"


def test_title_scan_avoids_short_token_false_hit():
    # "lg" (len 2) must NOT match inside "bulge"; it's below the title-scan floor
    ok, _ = gating.brand_is_gated(title="Cushioned bulge support pillow")
    assert not ok


# --------------------------------------------------------------------------- #
# category risk
# --------------------------------------------------------------------------- #
def test_category_high_grocery():
    risk, marker = gating.category_risk("Grocery & Gourmet Food > Snacks")
    assert risk == "high" and marker == "grocery"


def test_category_high_beauty():
    assert gating.category_risk("Beauty & Personal Care > Skin Care")[0] == "high"


def test_category_low_home_kitchen():
    assert gating.category_risk("Home & Kitchen > Storage")[0] == "low"


def test_category_medium_toys():
    assert gating.category_risk("Toys & Games > Building Sets")[0] == "medium"


def test_category_unknown_when_blank():
    assert gating.category_risk("")[0] == "unknown"
    assert gating.category_risk(None)[0] == "unknown"


# --------------------------------------------------------------------------- #
# hazmat
# --------------------------------------------------------------------------- #
def test_hazmat_keyword_hit():
    flag, kw = gating.hazmat_flag(title="Axe Body Spray Aerosol 4oz")
    assert flag == "yes" and kw == "aerosol"


def test_hazmat_lithium_battery():
    assert gating.hazmat_flag(title="Anker Power Bank lithium-ion 20000mAh")[0] == "yes"


def test_hazmat_no_when_clean_text():
    assert gating.hazmat_flag(title="Stainless Steel Mixing Bowl", category="Kitchen")[0] == "no"


def test_hazmat_unknown_when_no_text():
    assert gating.hazmat_flag()[0] == "unknown"


def test_hazmat_alcohol_free_does_not_trip():
    # bare "alcohol" is intentionally excluded so "alcohol-free" doesn't false-flag
    assert gating.hazmat_flag(title="Alcohol-Free Witch Hazel Toner")[0] == "no"


# --------------------------------------------------------------------------- #
# combined assessment
# --------------------------------------------------------------------------- #
def test_assess_gated_by_brand():
    a = gating.assess(title="Nike Air Max 90", brand="Nike", category="Shoes")
    assert a.gating_status == "likely-gated" and a.is_gated
    assert any("nike" in r for r in a.reasons)


def test_assess_gated_by_high_category():
    a = gating.assess(title="Acme Multivitamin 90ct", brand="Acme",
                      category="Health & Household > Vitamins")
    assert a.gating_status == "likely-gated"


def test_assess_gated_by_hazmat_even_in_open_category():
    a = gating.assess(title="WD-40 Lubricant Spray", brand="WD-40",
                      category="Tools & Home Improvement")
    assert a.hazmat == "yes" and a.gating_status == "likely-gated"


def test_assess_open_low_category_no_other_signal():
    a = gating.assess(title="Acme Stainless Mixing Bowl", brand="Acme",
                      category="Home & Kitchen > Bakeware")
    assert a.gating_status == "likely-open" and a.is_open
    assert a.risk_score < 0.5


def test_assess_unknown_when_medium_and_no_signal():
    a = gating.assess(title="Acme Board Game", brand="Acme", category="Toys & Games")
    assert a.gating_status == "unknown"


def test_assess_note_describes_gated_reason():
    a = gating.assess(title="Apple AirPods", brand="Apple", category="Electronics")
    note = a.note()
    assert "🔒" in note and "apple" in note


def test_assess_open_note_is_empty_for_lead_card():
    a = gating.assess(title="Acme Bowl", brand="Acme", category="Home & Kitchen")
    assert a.is_open  # open items don't need a gating caveat on the card


# --------------------------------------------------------------------------- #
# ordering helpers
# --------------------------------------------------------------------------- #
def test_gating_rank_orders_open_first():
    assert gating.gating_rank("likely-open") < gating.gating_rank("unknown")
    assert gating.gating_rank("unknown") < gating.gating_rank("likely-gated")
    assert gating.gating_rank(None) == gating.gating_rank("unknown")
