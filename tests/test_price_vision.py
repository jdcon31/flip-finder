"""Tests for the vision price backstop — pure parsing + reconcile brain (no GPU).

Run:  python -m pytest tests/test_price_vision.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers import price_vision as pv


# --------------------------------------------------------------------------- #
# JSON parsing / mapping
# --------------------------------------------------------------------------- #
def test_parse_price_json_plain():
    assert pv.parse_price_json('{"price": 15.64, "currency": "USD"}') == {
        "price": 15.64, "currency": "USD"}


def test_parse_price_json_with_prose_and_fence():
    raw = "Here is the price:\n```json\n{\"price\": 12.0, \"list_price\": 20}\n```"
    assert pv.parse_price_json(raw) == {"price": 12.0, "list_price": 20}


def test_parse_price_json_trailing_comma():
    assert pv.parse_price_json('{"price": 9.99,}') == {"price": 9.99}


def test_parse_price_json_garbage_is_empty():
    assert pv.parse_price_json("no json here") == {}
    assert pv.parse_price_json("") == {}


def test_vision_price_from_json_coerces_and_cleans():
    vp = pv.vision_price_from_json({"price": "$15.64", "currency": "usd", "list_price": "22.35"})
    assert vp.price == 15.64 and vp.currency == "USD" and vp.list_price == 22.35
    assert vp.ok


def test_vision_price_drops_listprice_when_not_a_strikethrough():
    # list_price <= price isn't a strikethrough — drop it
    vp = pv.vision_price_from_json({"price": 20.0, "list_price": 18.0})
    assert vp.price == 20.0 and vp.list_price is None


def test_vision_price_null_price_not_ok():
    vp = pv.vision_price_from_json({"price": None})
    assert vp.price is None and not vp.ok


# --------------------------------------------------------------------------- #
# reconcile — the decision brain
# --------------------------------------------------------------------------- #
def test_reconcile_agree_promotes_to_high():
    d = pv.reconcile(15.64, "low", 15.60)        # within 5%
    assert d["price"] == 15.64 and d["confidence"] == "high"
    assert d["source"] == "structured+vision" and not d["changed"]
    assert "confirmed" in d["note"]


def test_reconcile_vision_higher_flags_low_dangerous_direction():
    # vision reads HIGHER -> structured may be under-reading (false-lead direction)
    d = pv.reconcile(5.56, "low", 15.64)          # a single-source conflict
    assert d["price"] == 5.56 and d["confidence"] == "low" and not d["changed"]
    assert "15.64" in d["note"] and "5.56" in d["note"]   # surfaces both for the human


def test_reconcile_vision_lower_is_not_alarmed_subscribe_case():
    # vision reads LOWER (a Subscribe & Save / member price we don't baseline) -> keep
    # the structured public price quietly, no "verify" flag
    d = pv.reconcile(10.79, "medium", 6.47)       # the subscribe-price shape
    assert d["price"] == 10.79 and d["confidence"] == "medium"
    assert d["note"] == "" and not d["changed"]


def test_reconcile_supplies_price_when_structured_missing():
    d = pv.reconcile(None, None, 29.99)
    assert d["price"] == 29.99 and d["confidence"] == "medium"
    assert d["source"] == "vision" and d["changed"]


def test_reconcile_vision_failed_keeps_structured_confidence():
    d = pv.reconcile(40.0, "medium", None)
    assert d["price"] == 40.0 and d["confidence"] == "medium"
    assert d["note"] == "" and not d["changed"]


def test_reconcile_neither_is_none():
    d = pv.reconcile(None, None, None)
    assert d["price"] is None and d["confidence"] == "none"


def test_reconcile_tolerance_is_relative():
    # 4% apart on a big number still agrees at the 5% default tol
    d = pv.reconcile(100.0, "low", 96.0)
    assert d["confidence"] == "high"
    # 10% higher does not (and higher = the dangerous direction -> flagged low)
    d2 = pv.reconcile(100.0, "low", 110.0)
    assert d2["confidence"] == "low"


# --------------------------------------------------------------------------- #
# slug helper
# --------------------------------------------------------------------------- #
def test_slug_sanitizes():
    assert pv._slug("Trail Runner 90!!") == "trail-runner-90"
    assert pv._slug("") == "item"
    assert len(pv._slug("x" * 100)) <= 50
