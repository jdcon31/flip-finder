"""Unit tests for the Phase 1 profit engine: fee tiers, tax, shipping,
and end-to-end profit math (including the worked example from the design notes).

Run:  python -m pytest tests/ -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines import fba_fees, profit_calc, shipping_calc
from utils import tax_tables


# --------------------------------------------------------------------------- #
# Referral fee tier boundaries
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "category, price, expected_rate",
    [
        ("jewelry", 249.0, 0.20),
        ("jewelry", 251.0, 0.05),
        ("electronics accessories", 99.0, 0.15),
        ("electronics accessories", 101.0, 0.08),
        ("furniture", 199.0, 0.15),
        ("furniture", 201.0, 0.10),
        ("baby", 9.99, 0.08),
        ("baby", 10.01, 0.15),
        ("beauty", 5.00, 0.08),
        ("health", 50.0, 0.15),
        ("Shoes", 80.0, 0.15),  # unknown -> default flat 15%
        ("", 80.0, 0.15),  # empty -> default
    ],
)
def test_referral_rate_tiers(category, price, expected_rate):
    assert fba_fees.referral_rate(category, price) == pytest.approx(expected_rate)


def test_referral_fee_dollars():
    assert fba_fees.referral_fee("default", 100.0) == pytest.approx(15.0)
    assert fba_fees.referral_fee("jewelry", 300.0) == pytest.approx(15.0)  # 5% of 300


def test_referral_is_case_insensitive():
    assert fba_fees.referral_rate("JEWELRY", 100.0) == fba_fees.referral_rate("jewelry", 100.0)


# --------------------------------------------------------------------------- #
# Closing fee
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cat", ["books", "DVD", "Music", "software", "video games"])
def test_media_closing_fee(cat):
    assert fba_fees.closing_fee(cat) == pytest.approx(1.80)


@pytest.mark.parametrize("cat", ["default", "shoes", "beauty", ""])
def test_non_media_no_closing_fee(cat):
    assert fba_fees.closing_fee(cat) == 0.0


# --------------------------------------------------------------------------- #
# Sales tax
# --------------------------------------------------------------------------- #
def test_il_tax():
    assert tax_tables.sales_tax(100.0, "IL") == pytest.approx(6.25)


@pytest.mark.parametrize("state", ["AK", "DE", "MT", "NH", "OR"])
def test_no_tax_states(state):
    assert tax_tables.sales_tax(100.0, state) == 0.0


def test_resale_exemption_zeroes_tax():
    assert tax_tables.sales_tax(100.0, "IL", resale_exempt_states=["IL"]) == 0.0
    # Exemption elsewhere doesn't affect IL.
    assert tax_tables.sales_tax(100.0, "IL", resale_exempt_states=["TX"]) == pytest.approx(6.25)


def test_all_states_and_dc_present():
    assert len(tax_tables.STATE_TAX_RATES) == 51  # 50 states + DC


# --------------------------------------------------------------------------- #
# Shipping
# --------------------------------------------------------------------------- #
def test_flat_shipping_adds_packaging():
    assert shipping_calc.fbm_shipping(flat_cost=7.99, packaging_cost=1.0) == pytest.approx(8.99)


def test_weight_based_fallback():
    # 0.5 lb falls in the first (<=1 lb) tier = 5.00, + 1.0 packaging.
    assert shipping_calc.fbm_shipping(weight_lb=0.5, packaging_cost=1.0) == pytest.approx(6.00)
    # Very heavy uses the catch-all tier.
    assert shipping_calc.fbm_shipping(weight_lb=100.0, packaging_cost=0.0) == pytest.approx(40.00)


def test_known_weight_wins_over_flat_both_directions():
    # Light item: weight-based ($5) beats flat $7.99 -> cheaper, unlocks the lead.
    assert shipping_calc.fbm_shipping(weight_lb=0.5, flat_cost=7.99,
                                      packaging_cost=1.0) == pytest.approx(6.00)
    # Heavy item: weight-based ($16 @ 8 lb) beats flat $7.99 -> MORE, kills a
    # would-be false lead the flat rate under-charged.
    assert shipping_calc.fbm_shipping(weight_lb=8.0, flat_cost=7.99,
                                      packaging_cost=1.0) == pytest.approx(17.00)
    # Unknown weight (0) -> the flat measured cost still applies.
    assert shipping_calc.fbm_shipping(weight_lb=0.0, flat_cost=7.99,
                                      packaging_cost=1.0) == pytest.approx(8.99)


def test_grocery_threshold_is_15_and_clothing_17():
    assert fba_fees.referral_rate("grocery", 15.0) == pytest.approx(0.08)
    assert fba_fees.referral_rate("grocery", 15.01) == pytest.approx(0.15)
    assert fba_fees.referral_rate("clothing", 50.0) == pytest.approx(0.17)
    assert fba_fees.referral_rate("apparel", 9.0) == pytest.approx(0.17)


@pytest.mark.parametrize("breadcrumb, expected", [
    ("Beauty & Personal Care > Makeup > Lip", "beauty"),
    ("Health & Household > Vitamins & Dietary Supplements", "health"),
    ("Grocery & Gourmet Food > Snack Foods", "grocery"),
    ("Baby Products > Diapering", "baby"),
    ("Toys & Games > Action Figures", None),       # 15% default -> no special map
    ("Electronics > Headphones", None),
    ("", None),
])
def test_amazon_fee_category_mapping(breadcrumb, expected):
    assert fba_fees.amazon_fee_category(breadcrumb) == expected


def test_amazon_category_drives_8pct_for_cheap_beauty():
    # a $9 beauty item: mapped to 'beauty' -> 8%, not the 15% default
    assert fba_fees.referral_rate(
        fba_fees.amazon_fee_category("Beauty & Personal Care > Skin Care"), 9.0) == pytest.approx(0.08)


def test_weight_aware_shipping_improves_light_item_profit():
    s = profit_calc.ProfitSettings(state="IL")
    light = profit_calc.calculate(profit_calc.Deal(
        sale_price=11.99, sell_price=29.99, category="pet",
        weight=0.5, fbm_shipping_flat=7.99), s)
    flat = profit_calc.calculate(profit_calc.Deal(
        sale_price=11.99, sell_price=29.99, category="pet",
        weight=0.0, fbm_shipping_flat=7.99), s)
    # the light item's known weight ships cheaper -> strictly more profit
    assert light.breakdown["fbm_shipping"] < flat.breakdown["fbm_shipping"]
    assert light.net_profit > flat.net_profit


# --------------------------------------------------------------------------- #
# Profit calc — formula correctness & internal consistency
# --------------------------------------------------------------------------- #
def test_breakdown_is_internally_consistent():
    deal = profit_calc.Deal(sale_price=50.0, sell_price=100.0, category="default", weight=1.0)
    settings = profit_calc.ProfitSettings(state="IL")
    r = profit_calc.calculate(deal, settings)
    b = r.breakdown
    recomputed = (
        b["sell_price"]
        - b["effective_cost"]
        - b["referral_fee"]
        - b["closing_fee"]
        - b["fbm_shipping"]
        - b["return_reserve"]
        - b["misc_buffer"]
    )
    assert recomputed == pytest.approx(r.net_profit)
    assert r.roi_pct == pytest.approx(r.net_profit / b["effective_cost"] * 100)
    assert r.margin_pct == pytest.approx(r.net_profit / b["sell_price"] * 100)


def test_lead_list_mode_uses_flat_cashback_ignoring_deal_card():
    # Default mode is lead_list: deal.cc_cashback_pct is ignored in favor of
    # the generic flat rate, so two different cards yield identical cc cashback.
    settings = profit_calc.ProfitSettings(state="OR")  # no tax to isolate cashback
    a = profit_calc.calculate(profit_calc.Deal(50.0, 100.0, cc_cashback_pct=0.0), settings)
    b = profit_calc.calculate(profit_calc.Deal(50.0, 100.0, cc_cashback_pct=5.0), settings)
    assert a.breakdown["cc_cashback_pct"] == pytest.approx(profit_calc.LEAD_LIST_CASHBACK_PCT)
    assert a.breakdown["cc_cashback"] == pytest.approx(b.breakdown["cc_cashback"])
    assert a.cashback_note == profit_calc.CASHBACK_DISCLAIMER


def test_personal_mode_uses_deal_card_rate():
    settings = profit_calc.ProfitSettings(state="OR", cashback_mode="personal")
    low = profit_calc.calculate(profit_calc.Deal(50.0, 100.0, cc_cashback_pct=1.0), settings)
    high = profit_calc.calculate(profit_calc.Deal(50.0, 100.0, cc_cashback_pct=5.0), settings)
    assert high.breakdown["cc_cashback"] > low.breakdown["cc_cashback"]
    assert high.net_profit > low.net_profit
    assert low.cashback_note is None  # no disclaimer in personal mode


def test_portal_cashback_reduces_cost():
    settings = profit_calc.ProfitSettings(state="OR")
    base = profit_calc.calculate(profit_calc.Deal(50.0, 100.0), settings)
    with_portal = profit_calc.calculate(
        profit_calc.Deal(50.0, 100.0, portal_cashback_pct=3.0), settings
    )
    assert with_portal.breakdown["effective_cost"] < base.breakdown["effective_cost"]
    assert with_portal.net_profit > base.net_profit


def test_resale_exempt_lowers_cost_vs_taxed():
    taxed = profit_calc.calculate(
        profit_calc.Deal(50.0, 100.0), profit_calc.ProfitSettings(state="IL")
    )
    exempt = profit_calc.calculate(
        profit_calc.Deal(50.0, 100.0),
        profit_calc.ProfitSettings(state="IL", resale_exempt_states=("IL",)),
    )
    assert exempt.breakdown["sales_tax"] == 0.0
    assert exempt.breakdown["effective_cost"] < taxed.breakdown["effective_cost"]


# --------------------------------------------------------------------------- #
# Worked example ("Deal Found" card: ~$22 profit / ~31% ROI)
# --------------------------------------------------------------------------- #
def test_worked_example_end_to_end():
    # Source store $67.97 (free ship over $50) -> Amazon $119.99, 1% cashback portal.
    deal = profit_calc.Deal(
        sale_price=67.97,
        sell_price=119.99,
        category="shoes",  # unknown -> 15% default
        source_shipping=0.0,
        portal_cashback_pct=1.0,
        weight=2.0,
    )
    settings = profit_calc.ProfitSettings(state="IL", default_shipping_cost=7.99)
    r = profit_calc.calculate(deal, settings).rounded()

    # Should land in the same ballpark as the illustrative card, not exact.
    assert 12.0 <= r.net_profit <= 28.0
    assert 18.0 <= r.roi_pct <= 40.0
    assert 8.0 <= r.margin_pct <= 24.0
